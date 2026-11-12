"""Admin routes for family management, assignments, history, and system settings."""

from datetime import datetime
import json
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from web.assignment import (
    AssignmentError,
    AssignmentInfeasibleError,
    analyze_history_coverage,
    compute_roster_hash,
    get_cycle_info,
    predictable_rotation,
    randomized_coverage_cycle,
    uniform_random_derangement,
)
from web.email_service import (
    generate_mailto_link,
    get_email_logs_for_year,
    get_latest_email_status_per_family,
    is_smtp_configured,
    send_batch_invitations,
)
from web.models import (
    check_roster_stability,
    create_family,
    export_families_to_csv,
    export_history_to_csv,
    get_all_families,
    get_all_settings,
    get_assignments_for_year,
    get_family_by_id,
    get_history_by_year,
    get_recent_history_disallowed,
    get_setting,
    import_families_from_csv,
    is_year_published,
    publish_assignments,
    revoke_family_token,
    save_draft_assignments,
    set_setting,
    toggle_family_active,
    unpublish_assignments,
    update_family,
)
from web.security import (
    admin_required,
    check_login_rate_limit,
    record_login_attempt,
    verify_admin_password,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/christmas")


# ---------------------------------------------------------
# Authentication Routes
# ---------------------------------------------------------

@admin_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Admin login page with constant-time password check and IP rate limiting."""
    if session.get("is_admin"):
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        ip = request.remote_addr or "127.0.0.1"

        if not check_login_rate_limit(ip, max_failures=5, window_minutes=15):
            record_login_attempt(ip, success=False)
            abort(429, description="Too many failed login attempts. Please wait 15 minutes before trying again.")

        password = request.form.get("password", "")
        if verify_admin_password(password):
            record_login_attempt(ip, success=True)
            session.clear()
            session["is_admin"] = True
            session.permanent = False
            flash("Welcome! You are logged in as administrator.", "success")
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/christmas"):
                return redirect(next_url)
            return redirect(url_for("admin.dashboard"))
        else:
            record_login_attempt(ip, success=False)
            flash("Incorrect admin password. Please try again.", "error")

    return render_template("admin/login.html")


@admin_bp.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    """Log out administrator and clear session."""
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("admin.admin_login"))


# ---------------------------------------------------------
# Dashboard & Assignments
# ---------------------------------------------------------

@admin_bp.route("/", methods=["GET"])
@admin_bp.route("/admin", methods=["GET"])
@admin_bp.route("/admin/dashboard", methods=["GET"])
@admin_required
def dashboard():
    """Main administrative dashboard displaying status, metrics, and assignment tools."""
    settings = get_all_settings()
    current_year = int(settings.get("current_year", datetime.now().year))
    anchor_year = int(settings.get("cycle_anchor_year", current_year))
    mode = settings.get("assignment_mode", "randomized_cycle")
    no_repeat_years = int(settings.get("no_repeat_years", "1"))

    all_families = get_all_families()
    active_families = [f for f in all_families if f["active"] == 1]
    active_ids = [f["id"] for f in active_families]
    n = len(active_ids)

    # Mathematical cycle information
    cycle_info = get_cycle_info(n, current_year, anchor_year)
    is_roster_stable, roster_warning = check_roster_stability(active_ids)

    # Current year assignments
    assignments = get_assignments_for_year(current_year)
    is_published = is_year_published(current_year)
    email_statuses = get_latest_email_status_per_family(current_year)
    smtp_available = is_smtp_configured()

    # Invitation progress
    invited_count = sum(1 for a in assignments if email_statuses.get(a["giver_id"], {}).get("status") == "sent")

    # Mailto links map
    base_url = request.host_url.rstrip("/")
    mailto_links = {}
    portal_links = {}
    for a in assignments:
        giver_id = a["giver_id"]
        token = a["giver_token"]
        p_url = f"{base_url}/christmas/f/{token}"
        portal_links[giver_id] = p_url
        mailto_links[giver_id] = generate_mailto_link(
            family_name=a["giver_name"],
            emails=a["giver_emails"],
            portal_url=p_url,
            year=current_year
        )

    # Check for pending preview in session
    preview_data = session.get("assignment_preview")
    preview_assignments = None
    if preview_data and preview_data.get("year") == current_year:
        preview_assignments = preview_data.get("assignments")

    return render_template(
        "admin/dashboard.html",
        current_year=current_year,
        anchor_year=anchor_year,
        mode=mode,
        no_repeat_years=no_repeat_years,
        all_families=all_families,
        active_families=active_families,
        n=n,
        cycle_info=cycle_info,
        is_roster_stable=is_roster_stable,
        roster_warning=roster_warning,
        assignments=assignments,
        is_published=is_published,
        email_statuses=email_statuses,
        invited_count=invited_count,
        smtp_available=smtp_available,
        portal_links=portal_links,
        mailto_links=mailto_links,
        preview_assignments=preview_assignments,
        active_tab="dashboard"
    )


@admin_bp.route("/admin/assignments/preview", methods=["POST"])
@admin_required
def preview_assignments():
    """Generate and preview assignments without saving or publishing."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    anchor_year = int(get_setting("cycle_anchor_year", str(current_year)))
    mode = request.form.get("assignment_mode", get_setting("assignment_mode", "randomized_cycle"))
    cycle_secret = get_setting("cycle_secret", "fallback-secret")
    lookback = int(request.form.get("no_repeat_years", get_setting("no_repeat_years", "1")))

    active_families = get_all_families(active_only=True)
    family_map = {f["id"]: f["name"] for f in active_families}
    family_ids = [f["id"] for f in active_families]

    if len(family_ids) < 2:
        flash("At least 2 active families are required to generate assignments.", "error")
        return redirect(url_for("admin.dashboard"))

    stable, warning = check_roster_stability(family_ids)
    if mode in ("predictable_rotation", "randomized_cycle") and not stable:
        flash(warning + " Reset the cycle anchor explicitly before previewing another complete cycle.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        if mode == "predictable_rotation":
            mapping = predictable_rotation(family_ids, current_year, anchor_year)
        elif mode == "randomized_cycle":
            mapping = randomized_coverage_cycle(family_ids, current_year, anchor_year, cycle_secret)
        elif mode == "uniform_random":
            history_disallowed = get_recent_history_disallowed(family_ids, current_year, lookback)
            mapping = uniform_random_derangement(family_ids, history_disallowed)
        else:
            flash(f"Unknown assignment mode: {mode}", "error")
            return redirect(url_for("admin.dashboard"))

        # Convert to serializable preview list
        preview_list = []
        for giver_id, receiver_id in mapping.items():
            preview_list.append({
                "giver_id": giver_id,
                "giver_name": family_map.get(giver_id, f"Family #{giver_id}"),
                "receiver_id": receiver_id,
                "receiver_name": family_map.get(receiver_id, f"Family #{receiver_id}"),
            })

        session["assignment_preview"] = {
            "year": current_year,
            "mode": mode,
            "assignments": preview_list,
            "raw_mapping": {str(k): v for k, v in mapping.items()},
            "roster_hash": compute_roster_hash(family_ids),
            "anchor_year": anchor_year
        }
        flash(f"Generated preview assignments for {len(family_ids)} families using mode '{mode}'. Review below.", "info")

    except AssignmentError as e:
        flash(str(e), "error")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/clear-preview", methods=["POST"])
@admin_required
def clear_preview():
    """Clear currently generated preview from session."""
    session.pop("assignment_preview", None)
    flash("Assignment preview cleared.", "info")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/save", methods=["POST"])
@admin_required
def save_assignments():
    """Commit preview assignments as draft in database."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))

    if is_year_published(current_year):
        flash(f"Assignments for {current_year} are already published and cannot be overwritten.", "error")
        return redirect(url_for("admin.dashboard"))

    preview_data = session.get("assignment_preview")
    if not preview_data or preview_data.get("year") != current_year:
        flash("No active preview found to save. Please generate a preview first.", "error")
        return redirect(url_for("admin.dashboard"))

    active_ids = [f["id"] for f in get_all_families(active_only=True)]
    if preview_data.get("roster_hash") != compute_roster_hash(active_ids) or preview_data.get("anchor_year") != int(get_setting("cycle_anchor_year", str(current_year))):
        session.pop("assignment_preview", None)
        flash("Families or cycle settings changed since preview. Please generate a fresh preview.", "error")
        return redirect(url_for("admin.dashboard"))
    raw_mapping = preview_data.get("raw_mapping", {})
    mapping = {int(k): int(v) for k, v in raw_mapping.items()}

    try:
        save_draft_assignments(current_year, mapping)
        session.pop("assignment_preview", None)
        flash(f"Draft assignments for {current_year} successfully saved! They are not visible to families until published.", "success")
    except Exception as e:
        flash(f"Failed to save assignments: {e}", "error")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/publish", methods=["POST"])
@admin_required
def publish():
    """Publish and lock assignments for current year."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    assignments = get_assignments_for_year(current_year)

    if not assignments:
        flash(f"No saved assignments found for {current_year}. Generate and save assignments first.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        publish_assignments(current_year)
        flash(f"Christmas {current_year} assignments are now PUBLISHED and locked! Families can now view their assigned recipient via their private link.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/unpublish", methods=["POST"])
@admin_required
def unpublish():
    """Unpublish assignments for current year with explicit confirmation."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    flash("Published assignments are locked to protect the families who may have seen them. Contact the organizer for a documented correction.", "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/send-emails", methods=["POST"])
@admin_required
def send_emails():
    """Send invitation emails via SMTP batch."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    only_failed = request.form.get("only_failed") == "1"

    if not is_year_published(current_year):
        flash("Cannot send emails: assignments must be published first.", "error")
        return redirect(url_for("admin.dashboard"))

    if not is_smtp_configured():
        flash("SMTP is not configured in environment variables. Email sending is unavailable.", "error")
        return redirect(url_for("admin.dashboard"))

    base_url = request.host_url.rstrip("/")
    result = send_batch_invitations(current_year, base_url, only_failed=only_failed)

    if result["success"]:
        flash(f"Successfully sent invitation emails to {result['sent_count']} families!", "success")
    else:
        err_msg = "; ".join(result["errors"]) if result["errors"] else "Unknown error."
        flash(f"Sent {result['sent_count']} emails, but {result['failed_count']} failed. Errors: {err_msg}", "warning")

    return redirect(url_for("admin.dashboard"))


# ---------------------------------------------------------
# Families Management
# ---------------------------------------------------------

@admin_bp.route("/admin/families", methods=["GET"])
@admin_required
def families_list():
    """Family roster management view."""
    families = get_all_families()
    base_url = request.host_url.rstrip("/")
    portal_links = {
        f["id"]: f"{base_url}/christmas/f/{f['access_token']}"
        for f in families
    }

    return render_template(
        "admin/families.html",
        families=families,
        portal_links=portal_links,
        active_tab="families"
    )


@admin_bp.route("/admin/families/add", methods=["POST"])
@admin_required
def add_family():
    """Add a new family to the exchange."""
    name = request.form.get("name", "").strip()
    emails = request.form.get("emails", "").strip()
    address = request.form.get("mailing_address", "").strip()
    notes = request.form.get("notes", "").strip()
    members = request.form.get("members", "").strip()

    if not name:
        flash("Family name is required.", "error")
        return redirect(url_for("admin.families_list"))

    create_family(
        name=name,
        emails=emails,
        mailing_address=address,
        notes=notes,
        members=members,
        active=1
    )
    flash(f"Family '{name}' added successfully.", "success")
    return redirect(url_for("admin.families_list"))


@admin_bp.route("/admin/families/<int:family_id>/edit", methods=["POST"])
@admin_required
def edit_family(family_id: int):
    """Update family details."""
    fam = get_family_by_id(family_id)
    if not fam:
        abort(404, description="Family not found.")

    name = request.form.get("name", "").strip()
    emails = request.form.get("emails", "").strip()
    address = request.form.get("mailing_address", "").strip()
    notes = request.form.get("notes", "").strip()
    members = request.form.get("members", "").strip()
    active = 1 if request.form.get("active") == "1" else 0

    if not name:
        flash("Family name cannot be blank.", "error")
        return redirect(url_for("admin.families_list"))

    update_family(family_id, name, emails, address, notes, members, active)
    flash(f"Family '{name}' updated successfully.", "success")
    return redirect(url_for("admin.families_list"))


@admin_bp.route("/admin/families/<int:family_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_active(family_id: int):
    """Toggle family active status."""
    fam = get_family_by_id(family_id)
    if not fam:
        abort(404)

    new_status = toggle_family_active(family_id)
    status_str = "activated" if new_status == 1 else "deactivated"
    flash(f"Family '{fam['name']}' has been {status_str}.", "info")
    return redirect(url_for("admin.families_list"))


@admin_bp.route("/admin/families/<int:family_id>/revoke-token", methods=["POST"])
@admin_required
def revoke_token(family_id: int):
    """Revoke private link token and generate a fresh one."""
    fam = get_family_by_id(family_id)
    if not fam:
        abort(404)

    revoke_family_token(family_id)
    flash(f"Private link for '{fam['name']}' was revoked and a new secure link generated. The old link is now invalid.", "warning")
    return redirect(url_for("admin.families_list"))


@admin_bp.route("/admin/families/import-csv", methods=["POST"])
@admin_required
def import_csv():
    """Import family roster from uploaded CSV."""
    if "csv_file" not in request.files:
        flash("No file was uploaded.", "error")
        return redirect(url_for("admin.families_list"))

    file = request.files["csv_file"]
    if file.filename == "":
        flash("Please select a CSV file to upload.", "error")
        return redirect(url_for("admin.families_list"))

    try:
        content = file.stream.read().decode("utf-8", errors="replace")
        created, updated, errors = import_families_from_csv(content)
        msg = f"CSV Import complete: {created} families added, {updated} updated."
        if errors:
            msg += f" {len(errors)} issues encountered: " + "; ".join(errors[:3])
        flash(msg, "success" if not errors else "warning")
    except Exception as e:
        flash(f"CSV import failed: {e}", "error")

    return redirect(url_for("admin.families_list"))


@admin_bp.route("/admin/families/export-csv", methods=["GET"])
@admin_required
def export_families():
    """Download families roster as CSV with private links."""
    base_url = request.host_url.rstrip("/")
    csv_data = export_families_to_csv(base_url)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=christmas_families.csv"}
    )


# ---------------------------------------------------------
# History & Coverage
# ---------------------------------------------------------

@admin_bp.route("/admin/history", methods=["GET"])
@admin_required
def history():
    """Assignment history view with fairness and repeat checks."""
    all_families = get_all_families()
    family_map = {f["id"]: f["name"] for f in all_families}
    family_ids = [f["id"] for f in all_families]

    history_by_year = get_history_by_year()
    analysis = analyze_history_coverage(family_ids, history_by_year)

    # Format history rows for display
    history_records = []
    for yr in sorted(history_by_year.keys(), reverse=True):
        assignments = get_assignments_for_year(yr)
        history_records.append({
            "year": yr,
            "published": assignments[0]["published"] if assignments else 0,
            "published_at": assignments[0]["published_at"] if assignments else None,
            "assignments": assignments
        })

    return render_template(
        "admin/history.html",
        history_records=history_records,
        analysis=analysis,
        family_map=family_map,
        active_tab="history"
    )


@admin_bp.route("/admin/history/export-csv", methods=["GET"])
@admin_required
def export_history():
    """Export complete assignment history to CSV."""
    csv_data = export_history_to_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=christmas_assignment_history.csv"}
    )


# ---------------------------------------------------------
# Settings Operations
# ---------------------------------------------------------

@admin_bp.route("/admin/settings/update", methods=["POST"])
@admin_required
def update_settings():
    """Update system settings from Advanced Settings section."""
    current_year = request.form.get("current_year", "").strip()
    anchor_year = request.form.get("cycle_anchor_year", "").strip()
    mode = request.form.get("assignment_mode", "").strip()
    no_repeat_years = request.form.get("no_repeat_years", "").strip()

    if current_year:
        set_setting("current_year", current_year)
    if anchor_year:
        set_setting("cycle_anchor_year", anchor_year)
    if mode in ("randomized_cycle", "predictable_rotation", "uniform_random"):
        set_setting("assignment_mode", mode)
    if no_repeat_years in ("0", "1", "2"):
        set_setting("no_repeat_years", no_repeat_years)

    flash("Settings updated successfully.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/settings/reset-anchor", methods=["POST"])
@admin_required
def reset_anchor():
    """Reset cycle anchor year to current year and bind to current active roster."""
    current_year = get_setting("current_year", str(datetime.now().year))
    active_families = get_all_families(active_only=True)
    active_ids = [f["id"] for f in active_families]
    new_hash = compute_roster_hash(active_ids)

    set_setting("cycle_anchor_year", current_year)
    set_setting("roster_anchor_hash", new_hash)

    flash(
        f"Cycle anchor has been reset to year {current_year} with {len(active_ids)} active families. "
        f"A fresh full-coverage cycle begins from {current_year}.",
        "success"
    )
    return redirect(url_for("admin.dashboard"))
