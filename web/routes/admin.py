"""Admin routes for family management, assignments, history, and system settings."""

from datetime import datetime
import json
import os
import hmac
import urllib.request
import urllib.error
import urllib.parse
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
    batch_simulate_assignments,
    compute_roster_hash,
    find_deterministic_rotation_salt,
    fixed_rotation,
    generate_shuffled_rotation_schedule,
    generate_synthetic_roster,
    get_cycle_info,
    predictable_rotation,
    randomized_coverage_cycle,
    shuffled_repeating_rotation,
    simulate_assignment,
    uniform_random_derangement,
    validate_fixed_rotation_config,
    validate_mapping_against_history,
    validate_rules_feasibility,
)
from web.continuity import find_boundary_conflicts, find_boundary_safe_salt, confirmed_history_by_year
from web.db import get_db
from web.email_service import (
    generate_mailto_link,
    get_email_logs_for_year,
    get_latest_email_status_per_family,
    is_smtp_configured,
    send_batch_invitations,
)
from web.models import (
    calculate_game_scores,
    check_roster_stability,
    create_family,
    delete_manual_history_for_year,
    discard_draft_assignments,
    export_families_to_csv,
    export_history_to_csv,
    get_address_review,
    get_admin_tracking_data,
    get_all_address_reviews,
    get_all_families,
    get_all_family_activities_for_year,
    get_all_settings,
    get_archive_metadata,
    get_assignments_for_year,
    get_family_activity,
    get_family_by_id,
    get_guess_participation,
    get_guess_submission_status,
    get_historical_years_summary,
    get_history_by_year,
    get_pending_address_reviews,
    get_production_seed,
    get_recent_history_disallowed,
    get_seed_fingerprint,
    get_setting,
    has_published_years,
    import_families_from_csv,
    import_manual_history_from_csv,
    is_guesses_revealed,
    is_year_published,
    mark_address_reviewed,
    publish_assignments,
    record_family_assignment_confirmation,
    record_family_guess_submission,
    record_family_visit,
    record_family_wishlist_update,
    regenerate_production_seed,
    reveal_guesses_for_year,
    revoke_family_token,
    save_archive_metadata,
    save_draft_assignments,
    save_manual_history_for_year,
    set_production_seed,
    set_setting,
    toggle_family_active,
    unpublish_assignments,
    update_family,
    upsert_address_review,
)
from web.sso_auth import auto_login_from_existing_sso
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

@admin_bp.route("/admin/sso", methods=["GET"])
def admin_sso():
    """Authenticate the organizer using the existing validated Universal SSO session."""
    state = request.args.get("state", "")
    expected = session.get("_csrf_token", "")
    if not state or not expected or not hmac.compare_digest(state, expected):
        abort(400, description="Invalid login state. Start from the organizer login page.")

    return_url = "https://taylorarchibald.com" + url_for("admin.admin_sso", state=state)
    sso_login = "/sso/login?return_url=" + urllib.parse.quote(return_url, safe="")
    cookie = request.headers.get("Cookie", "")
    if not cookie or "universal_sso_token=" not in cookie:
        return redirect(sso_login)

    auth_request = urllib.request.Request(
        "http://127.0.0.1:5050/sso/auth-request",
        headers={
            "Cookie": cookie,
            "X-Original-URI": "/hub/",
            "Host": "taylorarchibald.com"
        }
    )
    try:
        with urllib.request.urlopen(auth_request, timeout=3) as response:
            if response.status != 204:
                abort(403)
            identity = (response.headers.get("X-Auth-Request-User", "") or "").strip().lower()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return redirect(sso_login) if exc.code == 401 else abort(403)
        abort(503)
    except (urllib.error.URLError, TimeoutError):
        abort(503, description="Universal SSO is temporarily unavailable. Use organizer password instead.")

    allowed = {
        email.strip().lower()
        for email in os.environ.get("CHRISTMAS_ADMIN_SSO_USERS", "").split(",")
        if email.strip()
    }
    if not allowed or identity not in allowed:
        abort(403, description="This account is not a Christmas exchange organizer.")

    session.clear()
    session["is_admin"] = True
    session.permanent = False
    flash("Signed in with Universal SSO.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Admin login page with constant-time password check and IP rate limiting."""
    if session.get("is_admin") or (
        request.method == "GET" and auto_login_from_existing_sso()
    ):
        next_url = request.args.get("next", "")
        if next_url.startswith("/christmas/") or next_url == "/christmas":
            return redirect(next_url)
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
    # Preserve an explicit local sign-out even if the global SSO cookie
    # remains valid in the same browser.
    session["skip_sso_auto"] = True
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
    """Main administrative dashboard with unified Test / Official mode switch."""
    mode_arg = request.args.get("mode")
    if mode_arg in ("test", "official"):
        session["admin_mode"] = mode_arg
    is_test_mode = (session.get("admin_mode", "official") == "test")

    settings = get_all_settings()
    current_year = int(settings.get("current_year", datetime.now().year))
    anchor_year = int(settings.get("cycle_anchor_year", current_year))
    mode = settings.get("assignment_mode", "shuffled_rotation")
    no_repeat_years = int(settings.get("no_repeat_years", "1"))
    enforce_sliding_coverage = (settings.get("enforce_sliding_coverage", "1") == "1")
    coverage_window_k = int(settings.get("coverage_window_k", "4"))
    rotation_salt = settings.get("rotation_salt", "")
    fixed_household_order = settings.get("fixed_household_order", "")
    fixed_offset_order = settings.get("fixed_offset_order", "")

    # Keep the exact choices after a test run when switching to Official.
    # This session-only draft NEVER changes saved production settings.
    draft_rules = session.get("draft_rule_config")
    if draft_rules:
        mode = draft_rules["mode"]
        no_repeat_years = draft_rules["no_repeat_years"]
        coverage_window_k = draft_rules["coverage_window_k"]
        enforce_sliding_coverage = draft_rules["enforce_sliding_coverage"]

    all_families = get_all_families()
    active_families = [f for f in all_families if f["active"] == 1]
    active_ids = [f["id"] for f in active_families]
    n = len(active_ids)

    # Mathematical rules feasibility check
    feasibility = validate_rules_feasibility(
        n=n,
        lookback_l=no_repeat_years,
        coverage_k=coverage_window_k,
        enforce_coverage=enforce_sliding_coverage
    )
    cycle_info = get_cycle_info(n, current_year, anchor_year)
    is_roster_stable, roster_warning = check_roster_stability(active_ids)

    # Current year assignments
    assignments = get_assignments_for_year(current_year)
    is_published = is_year_published(current_year)
    email_statuses = get_latest_email_status_per_family(current_year)
    smtp_available = is_smtp_configured()

    invited_count = sum(1 for a in assignments if email_statuses.get(a["giver_id"], {}).get("status") in ("sent", "success"))

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

    spoilers_unlocked = bool(session.get("spoilers_unlocked", False))
    seed_fingerprint = get_seed_fingerprint()
    has_published = has_published_years()

    # Historical confidence badges only; never expose recipient pairings here.
    recent_history = get_historical_years_summary()[:3]

    # Address reviews
    pending_address_reviews = get_pending_address_reviews()

    # Family tracking data (compact admin tracking table)
    tracking_data = get_admin_tracking_data(current_year, spoilers_unlocked=spoilers_unlocked)
    for row in tracking_data:
        p_url = f"{base_url}/christmas/f/{row['access_token']}"
        row["portal_url"] = p_url
        row["mailto_url"] = generate_mailto_link(
            family_name=row["name"],
            emails=row["emails"],
            portal_url=p_url,
            year=current_year
        )

    # Guess submission status
    guess_submission_status = get_guess_submission_status(current_year)
    guess_stats = get_guess_participation(current_year)
    is_revealed = is_guesses_revealed(current_year)
    game_scores = calculate_game_scores(current_year) if (is_published and is_revealed) else None

    # Test mode state (from session if simulation was run)
    test_sim_data = session.pop("test_sim_data", None)

    return render_template(
        "admin/dashboard.html",
        is_test_mode=is_test_mode,
        current_year=current_year,
        anchor_year=anchor_year,
        mode=mode,
        no_repeat_years=no_repeat_years,
        enforce_sliding_coverage=enforce_sliding_coverage,
        coverage_window_k=coverage_window_k,
        rotation_salt=rotation_salt,
        fixed_household_order=fixed_household_order,
        fixed_offset_order=fixed_offset_order,
        all_families=all_families,
        active_families=active_families,
        n=n,
        feasibility=feasibility,
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
        active_tab="dashboard",
        spoilers_unlocked=spoilers_unlocked,
        seed_fingerprint=seed_fingerprint,
        has_published=has_published,
        pending_address_reviews=pending_address_reviews,
        recent_history=recent_history,
        tracking_data=tracking_data,
        guess_submission_status=guess_submission_status,
        guess_stats=guess_stats,
        is_revealed=is_revealed,
        game_scores=game_scores,
        test_sim_data=test_sim_data
    )


@admin_bp.route("/admin/spoilers/unlock", methods=["POST"])
@admin_required
def unlock_spoilers():
    """Temporarily unlock spoiler protection for the current session."""
    session["spoilers_unlocked"] = True
    flash("Spoiler protection unlocked for this session. Mappings are now visible.", "warning")
    next_url = request.form.get("next") or request.referrer or url_for("admin.dashboard")
    return redirect(next_url)


@admin_bp.route("/admin/spoilers/lock", methods=["POST"])
@admin_required
def lock_spoilers():
    """Lock spoiler protection and mask assignments."""
    session["spoilers_unlocked"] = False
    flash("Spoiler protection enabled. Assignments and pairings are now masked.", "info")
    next_url = request.form.get("next") or request.referrer or url_for("admin.dashboard")
    return redirect(next_url)


@admin_bp.route("/admin/settings/seed", methods=["POST"])
@admin_required
def update_seed():
    """Explicitly set production seed if no published years exist."""
    new_seed = request.form.get("production_seed", "").strip()
    try:
        set_production_seed(new_seed)
        flash(f"Production seed updated. New fingerprint: {get_seed_fingerprint()}", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/settings/seed/regenerate", methods=["POST"])
@admin_required
def regenerate_seed():
    """Generate a new secure random production seed if no published years exist."""
    try:
        regenerate_production_seed()
        flash(f"New secure production seed generated. Fingerprint: {get_seed_fingerprint()}", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/prepare", methods=["POST"])
@admin_required
def prepare_assignments():
    """Generate official assignments and seal them as draft without revealing or emailing."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    anchor_year = int(get_setting("cycle_anchor_year", str(current_year)))
    mode = request.form.get("assignment_mode", get_setting("assignment_mode", "shuffled_rotation"))
    lookback = max(0, int(request.form.get("no_repeat_years", get_setting("no_repeat_years", "1"))))
    enforce_coverage = (request.form.get("enforce_sliding_coverage", get_setting("enforce_sliding_coverage", "1")) == "1")
    coverage_k = max(1, int(request.form.get("coverage_window_k", get_setting("coverage_window_k", "4"))))

    if is_year_published(current_year):
        flash(f"Assignments for {current_year} are already published and cannot be modified.", "error")
        return redirect(url_for("admin.dashboard"))
    if get_assignments_for_year(current_year):
        flash("A sealed draft already exists. Discard that draft explicitly before preparing a new one.", "error")
        return redirect(url_for("admin.dashboard"))

    active_families = get_all_families(active_only=True)
    family_ids = [f["id"] for f in active_families]
    family_map = {f["id"]: f["name"] for f in active_families}
    n = len(family_ids)

    if n < 2:
        flash("At least 2 active families are required to generate assignments.", "error")
        return redirect(url_for("admin.dashboard"))

    # Validate mathematical feasibility
    feasibility = validate_rules_feasibility(n, lookback, coverage_k, enforce_coverage)
    if not feasibility["is_feasible"]:
        if request.form.get("waiver") == "1":
            old_l, old_k = lookback, coverage_k
            lookback = feasibility["suggested_lookback"]
            coverage_k = feasibility["suggested_coverage_window"]
            flash(
                f"Using your explicitly approved feasible limits: no-repeat {old_l} -> {lookback} years; "
                f"coverage {old_k} -> {coverage_k} years. The original impossible settings were NOT promised.",
                "warning",
            )
        else:
            flash(f"These rules cannot both be guaranteed: {' '.join(feasibility['errors'])} "
                  "Correct the numbers or explicitly choose the suggested limits.", "error")
            return redirect(url_for("admin.dashboard"))

    if enforce_coverage and mode in ("uniform_random", "randomized_cycle"):
        flash("This method cannot guarantee coverage in EVERY sliding window. "
              "Choose Shuffled repeating rotation or Fixed rotation, or turn off the coverage guarantee.", "error")
        return redirect(url_for("admin.dashboard"))

    stable, warning = check_roster_stability(family_ids)
    if mode in ("shuffled_rotation", "shuffled_repeating_rotation", "fixed_rotation", "predictable_rotation", "randomized_cycle") and not stable:
        flash(warning + " Reset the cycle anchor explicitly before preparing a complete cycle.", "error")
        return redirect(url_for("admin.dashboard"))

    cycle_secret = get_production_seed()
    history_disallowed = get_recent_history_disallowed(family_ids, current_year, lookback)

    try:
        if mode in ("shuffled_rotation", "shuffled_repeating_rotation"):
            history_by_year = confirmed_history_by_year(get_db(), current_year, lookback)
            persisted_salt = get_setting("rotation_salt", "")
            official_exists = has_published_years()
            chosen_salt = None

            if persisted_salt:
                planned = lambda yr: shuffled_repeating_rotation(
                    family_ids, yr, anchor_year, cycle_secret, salt=persisted_salt)
                boundary_issues = find_boundary_conflicts(
                    planned, history_by_year, current_year, lookback)
                if boundary_issues:
                    if official_exists:
                        flash("The saved official rotation conflicts with the imported historical lookback. "
                              "Published rotation cannot be silently reseeded. Review the history or adjust the rules.",
                              "error")
                        return redirect(url_for("admin.dashboard"))
                else:
                    chosen_salt = persisted_salt

            if chosen_salt is None:
                if official_exists:
                    flash("An official draw already exists, but its stable rotation salt is missing. "
                          "Refusing to silently choose a new production schedule.", "error")
                    return redirect(url_for("admin.dashboard"))
                chosen_salt = find_boundary_safe_salt(
                    family_ids, current_year, anchor_year, cycle_secret,
                    history_by_year, lookback, max_candidates=10000)
                if chosen_salt is None:
                    flash("No shuffled rotation satisfied the historical boundary for all upcoming years "
                          f"within the {lookback}-year no-repeat limit. "
                          "Adjust the lookback or verified history; no official assignments were generated.",
                          "error")
                    return redirect(url_for("admin.dashboard"))
                set_setting("rotation_salt", chosen_salt)
                set_setting("roster_anchor_hash", compute_roster_hash(family_ids))

            mapping = shuffled_repeating_rotation(
                family_ids, current_year, anchor_year, cycle_secret, salt=chosen_salt)

        elif mode == "fixed_rotation":
            raw_h = request.form.get("fixed_household_order") or get_setting("fixed_household_order", "")
            raw_o = request.form.get("fixed_offset_order") or get_setting("fixed_offset_order", "")

            try:
                h_order = [int(x.strip()) for x in raw_h.split(",") if x.strip()]
            except ValueError:
                h_order = []
            try:
                o_order = [int(x.strip()) for x in raw_o.split(",") if x.strip()]
            except ValueError:
                o_order = []

            if not h_order:
                h_order = sorted(family_ids)
            if not o_order:
                o_order = list(range(1, n))

            is_valid, errs = validate_fixed_rotation_config(h_order, o_order, family_ids)
            if not is_valid:
                flash(f"Fixed rotation configuration error: {' '.join(errs)}", "error")
                return redirect(url_for("admin.dashboard"))

            mapping = fixed_rotation(h_order, o_order, current_year, anchor_year)
            set_setting("fixed_household_order", ",".join(str(i) for i in h_order))
            set_setting("fixed_offset_order", ",".join(str(i) for i in o_order))
            history_by_year = confirmed_history_by_year(get_db(), current_year, lookback)
            conflicts = find_boundary_conflicts(
                lambda yr: fixed_rotation(h_order, o_order, yr, anchor_year),
                history_by_year, current_year, lookback)
            if conflicts:
                c_givers = ", ".join(family_map.get(g, f"#{g}") for _, g, _, _ in conflicts[:2])
                flash(
                    f"Fixed rotation for {current_year} conflicts with the requested {lookback}-year lookback constraint "
                    f"for family ({c_givers}). Refusing to create assignments that violate explicit requested constraints. "
                    "Adjust fixed offset order, reduce lookback, or switch to Shuffled Repeating Rotation.",
                    "error"
                )
                return redirect(url_for("admin.dashboard"))

        elif mode == "predictable_rotation":
            mapping = predictable_rotation(family_ids, current_year, anchor_year)
            history_by_year = confirmed_history_by_year(get_db(), current_year, lookback)
            conflicts = find_boundary_conflicts(
                lambda yr: predictable_rotation(family_ids, yr, anchor_year),
                history_by_year, current_year, lookback)
            if conflicts:
                c_givers = ", ".join(family_map.get(g, f"#{g}") for _, g, _, _ in conflicts[:2])
                flash(
                    f"Predictable rotation for {current_year} violates the requested {lookback}-year lookback constraint "
                    f"for family ({c_givers}). Refusing to create assignments that violate explicit requested constraints. "
                    "Reduce lookback or switch to Uniform Random.",
                    "error"
                )
                return redirect(url_for("admin.dashboard"))
        elif mode == "randomized_cycle":
            mapping = randomized_coverage_cycle(family_ids, current_year, anchor_year, cycle_secret)
            conflicts = validate_mapping_against_history(mapping, history_disallowed)
            if conflicts and lookback > 0:
                c_givers = ", ".join(family_map.get(g, f"#{g}") for g, _ in conflicts[:2])
                flash(
                    f"Randomized cycle for {current_year} conflicts with the requested {lookback}-year lookback constraint "
                    f"for family ({c_givers}). Refusing to create assignments that violate explicit requested constraints. "
                    "Reduce lookback or switch to Uniform Random.",
                    "error"
                )
                return redirect(url_for("admin.dashboard"))
        elif mode == "uniform_random":
            mapping = uniform_random_derangement(family_ids, history_disallowed)
        else:
            flash(f"Unknown assignment mode: {mode}", "error")
            return redirect(url_for("admin.dashboard"))

        # Save settings used
        set_setting("assignment_mode", mode)
        set_setting("no_repeat_years", str(lookback))
        set_setting("enforce_sliding_coverage", "1" if enforce_coverage else "0")
        set_setting("coverage_window_k", str(coverage_k))

        save_draft_assignments(current_year, mapping)
        # Snapshot configuration at the moment the real mappings are sealed.
        save_archive_metadata(
            year=current_year, assignment_mode=mode,
            no_repeat_years=lookback, coverage_window=coverage_k,
            seed_fingerprint=get_seed_fingerprint(),
            roster_signature=compute_roster_hash(family_ids),
            config_json=json.dumps({
                "anchor_year": anchor_year,
                "rotation_salt": get_setting("rotation_salt", ""),
                "fixed_household_order": get_setting("fixed_household_order", ""),
                "fixed_offset_order": get_setting("fixed_offset_order", "")
            }, sort_keys=True)
        )
        session.pop("draft_rule_config", None)
        session.pop("assignment_preview", None)
        flash(
            f"Official assignments for Christmas {current_year} have been prepared and sealed as draft! "
            "They remain hidden behind spoiler protection and are not visible to families until you click 'Release to Families'.",
            "success"
        )
    except (AssignmentError, AssignmentInfeasibleError, ValueError) as exc:
        flash(str(exc), "error")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/discard-draft", methods=["POST"])
@admin_required
def discard_draft():
    """Discard unreleased draft assignments."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    try:
        discard_draft_assignments(current_year)
        session.pop("assignment_preview", None)
        flash(f"Draft assignments for Christmas {current_year} have been discarded.", "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/admin/assignments/release", methods=["POST"])
@admin_required
def release_assignments():
    """Explicitly release sealed draft assignments to families."""
    return publish()


@admin_bp.route("/admin/assignments/preview", methods=["POST"])
@admin_required
def preview_assignments():
    """Generate and preview assignments without saving or publishing."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    anchor_year = int(get_setting("cycle_anchor_year", str(current_year)))
    mode = request.form.get("assignment_mode", get_setting("assignment_mode", "shuffled_rotation"))
    cycle_secret = get_production_seed()
    lookback = max(0, int(request.form.get("no_repeat_years", get_setting("no_repeat_years", "1"))))

    active_families = get_all_families(active_only=True)
    family_map = {f["id"]: f["name"] for f in active_families}
    family_ids = [f["id"] for f in active_families]
    n = len(family_ids)

    if n < 2:
        flash("At least 2 active families are required to generate assignments.", "error")
        return redirect(url_for("admin.dashboard"))

    stable, warning = check_roster_stability(family_ids)
    if mode in ("shuffled_rotation", "shuffled_repeating_rotation", "fixed_rotation", "predictable_rotation", "randomized_cycle") and not stable:
        flash(warning + " Reset the cycle anchor explicitly before previewing another complete cycle.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        history_disallowed = get_recent_history_disallowed(family_ids, current_year, lookback)
        if mode in ("shuffled_rotation", "shuffled_repeating_rotation"):
            salt = get_setting("rotation_salt", "0")
            mapping = shuffled_repeating_rotation(family_ids, current_year, anchor_year, cycle_secret, salt=salt)
            conflicts = validate_mapping_against_history(mapping, history_disallowed)
            if conflicts and lookback > 0:
                flash(
                    f"Shuffled repeating rotation conflicts with the requested {lookback}-year lookback constraint. "
                    "Refusing to preview assignments that violate explicit requested constraints.",
                    "error"
                )
                return redirect(url_for("admin.dashboard"))
        elif mode == "fixed_rotation":
            raw_h = request.form.get("fixed_household_order") or get_setting("fixed_household_order", "")
            raw_o = request.form.get("fixed_offset_order") or get_setting("fixed_offset_order", "")
            try:
                h_order = [int(x.strip()) for x in raw_h.split(",") if x.strip()]
            except ValueError:
                h_order = []
            try:
                o_order = [int(x.strip()) for x in raw_o.split(",") if x.strip()]
            except ValueError:
                o_order = []
            if not h_order:
                h_order = sorted(family_ids)
            if not o_order:
                o_order = list(range(1, n))
            mapping = fixed_rotation(h_order, o_order, current_year, anchor_year)
            conflicts = validate_mapping_against_history(mapping, history_disallowed)
            if conflicts and lookback > 0:
                flash(
                    f"Fixed rotation conflicts with the requested {lookback}-year lookback constraint. "
                    "Refusing to preview assignments that violate explicit requested constraints.",
                    "error"
                )
                return redirect(url_for("admin.dashboard"))
        elif mode == "predictable_rotation":
            mapping = predictable_rotation(family_ids, current_year, anchor_year)
            conflicts = validate_mapping_against_history(mapping, history_disallowed)
            if conflicts and lookback > 0:
                flash(
                    f"Predictable rotation conflicts with the requested {lookback}-year lookback constraint. "
                    "Refusing to preview assignments that violate explicit requested constraints.",
                    "error"
                )
                return redirect(url_for("admin.dashboard"))
        elif mode == "randomized_cycle":
            mapping = randomized_coverage_cycle(family_ids, current_year, anchor_year, cycle_secret)
            conflicts = validate_mapping_against_history(mapping, history_disallowed)
            if conflicts and lookback > 0:
                flash(
                    f"Randomized cycle conflicts with the requested {lookback}-year lookback constraint. "
                    "Refusing to preview assignments that violate explicit requested constraints.",
                    "error"
                )
                return redirect(url_for("admin.dashboard"))
        elif mode == "uniform_random":
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


@admin_bp.route("/admin/address-reviews/<int:family_id>/mark-reviewed", methods=["POST"])
@admin_required
def mark_address_review(family_id: int):
    """Mark a family's address review as manually confirmed and reviewed."""
    fam = get_family_by_id(family_id)
    if not fam:
        abort(404)
    mark_address_reviewed(family_id)
    flash(f"Address review for '{fam['name']}' confirmed and marked as reviewed.", "success")
    return redirect(url_for("admin.dashboard"))


# ---------------------------------------------------------
# Test Lab View & Simulation (Redirects to Unified Dashboard)
# ---------------------------------------------------------

@admin_bp.route("/admin/test-lab", methods=["GET", "POST"])
@admin_bp.route("/admin/test-sim", methods=["POST"])
@admin_required
def test_lab():
    """Test simulation sandbox. GET redirects to dashboard?mode=test.

    POST executes pure in-memory simulations without mutating production state,
    then redirects to the unified dashboard in test mode to display results.
    """
    if request.method == "GET":
        return redirect(url_for("admin.dashboard", mode="test"))

    settings = get_all_settings()
    current_year = int(settings.get("current_year", datetime.now().year))
    active_families = get_all_families(active_only=True)

    mode = request.form.get("assignment_mode", "shuffled_rotation")
    year = int(request.form.get("year", current_year))
    anchor_year = int(request.form.get("anchor_year", current_year))
    test_seed = request.form.get("test_seed", "test-lab-seed-2026").strip()
    roster_choice = request.form.get("roster_choice", "current")
    custom_n = int(request.form.get("custom_n", "5"))
    lookback = max(0, int(request.form.get("no_repeat_years", "1")))
    coverage_k = max(1, int(request.form.get("coverage_window_k", "4")))
    enforce_coverage = (request.form.get("enforce_sliding_coverage") == "1")
    sim_type = request.form.get("sim_type", "single")
    session["draft_rule_config"] = {
        "mode": mode, "no_repeat_years": lookback,
        "coverage_window_k": coverage_k,
        "enforce_sliding_coverage": enforce_coverage,
    }

    # Determine roster to use
    if roster_choice == "synthetic_4":
        sim_roster = generate_synthetic_roster(4)
    elif roster_choice == "synthetic_5":
        sim_roster = generate_synthetic_roster(5)
    elif roster_choice == "synthetic_6":
        sim_roster = generate_synthetic_roster(6)
    elif roster_choice == "synthetic_8":
        sim_roster = generate_synthetic_roster(8)
    elif roster_choice == "custom":
        sim_roster = generate_synthetic_roster(max(2, min(custom_n, 25)))
    else:
        # Current active families
        if len(active_families) >= 2:
            sim_roster = [dict(f) for f in active_families]
        else:
            sim_roster = generate_synthetic_roster(5)
            roster_choice = "synthetic_5"

    family_map = {f["id"]: f["name"] for f in sim_roster}
    family_ids = [f["id"] for f in sim_roster]

    # Test the custom fixed order exactly as entered. Never save it to official settings.
    fixed_ids = None
    fixed_steps = None
    fixed_error = None
    if mode == "fixed_rotation":
        raw_ids = request.form.get("fixed_household_order", "").strip()
        raw_steps = request.form.get("fixed_offset_order", "").strip()
        try:
            fixed_ids = [int(x.strip()) for x in raw_ids.split(",") if x.strip()] if raw_ids else sorted(family_ids)
            fixed_steps = [int(x.strip()) for x in raw_steps.split(",") if x.strip()] if raw_steps else list(range(1, len(family_ids)))
            valid, problems = validate_fixed_rotation_config(fixed_ids, fixed_steps, family_ids)
            if not valid:
                fixed_error = " ".join(problems)
        except ValueError:
            fixed_error = "Enter comma-separated numbers for family IDs and yearly steps."

    single_result = None
    batch_result = None
    cycle_info = get_cycle_info(len(family_ids), year, anchor_year)
    error_message = None
    feasibility = validate_rules_feasibility(
        len(family_ids), lookback, coverage_k, enforce_coverage)
    if not feasibility["is_feasible"]:
        error_message = " ".join(feasibility["errors"])
    if fixed_error:
        error_message = fixed_error
    if enforce_coverage and mode in ("uniform_random", "randomized_cycle"):
        error_message = (
            "This method does not guarantee coverage in every sliding window. "
            "Use Shuffled repeating rotation or Custom fixed rotation, or turn off the guarantee."
        )

    try:
        if error_message:
            raise AssignmentError(error_message)
        if sim_type == "batch_100":
            batch_result = batch_simulate_assignments(
                family_ids=family_ids,
                mode=mode,
                start_year=year,
                anchor_year=anchor_year,
                seed=test_seed,
                lookback_years=lookback,
                num_runs=100,
                fixed_household_order=fixed_ids,
                fixed_offset_order=fixed_steps
            )
        else:
            history_disallowed = None
            if roster_choice == "current":
                history_disallowed = get_recent_history_disallowed(family_ids, year, lookback)

            mapping = simulate_assignment(
                family_ids=family_ids,
                mode=mode,
                year=year,
                anchor_year=anchor_year,
                seed=test_seed,
                history_disallowed=history_disallowed,
                fixed_household_order=fixed_ids,
                fixed_offset_order=fixed_steps
            )

            conflicts = []
            if history_disallowed and lookback > 0:
                conflicts = validate_mapping_against_history(mapping, history_disallowed)

            single_result = {
                "mapping": [
                    {
                        "giver_id": g,
                        "giver_name": family_map.get(g, f"Family #{g}"),
                        "receiver_id": r,
                        "receiver_name": family_map.get(r, f"Family #{r}"),
                        "has_conflict": (g, r) in conflicts
                    }
                    for g, r in mapping.items()
                ],
                "conflicts": conflicts
            }
    except (AssignmentError, AssignmentInfeasibleError) as exc:
        error_message = str(exc)

    test_sim_data = {
        "mode": mode,
        "year": year,
        "anchor_year": anchor_year,
        "test_seed": test_seed,
        "roster_choice": roster_choice,
        "custom_n": custom_n,
        "lookback": lookback,
        "sim_type": sim_type,
        "sim_roster": sim_roster,
        "family_map": family_map,
        "cycle_info": cycle_info,
        "single_result": single_result,
        "batch_result": batch_result,
        "error_message": error_message
    }

    session["test_sim_data"] = test_sim_data
    session["admin_mode"] = "test"
    return redirect(url_for("admin.dashboard", mode="test"))


# ---------------------------------------------------------
# History, Coverage & Manual Entry
# ---------------------------------------------------------

@admin_bp.route("/admin/history", methods=["GET"])
@admin_required
def history():
    """Assignment history view with fairness, repeat checks, and spoiler protection."""
    all_families = get_all_families()
    family_map = {f["id"]: f["name"] for f in all_families}
    family_ids = [f["id"] for f in all_families]

    history_by_year = get_history_by_year()
    history_summary = get_historical_years_summary()
    summary_by_year = {entry["year"]: entry for entry in history_summary}

    # Statistical guarantees must use evidence-backed records, never inferred
    # 2024 pairings merely reconstructed from the legacy random seed.
    confirmed_history = {}
    confirmed_rows = get_db().execute(
        """SELECT year,giver_family_id,receiver_family_id
           FROM assignments WHERE published=1 AND confidence='confirmed'"""
    ).fetchall()
    for rec in confirmed_rows:
        confirmed_history.setdefault(rec["year"], {})[rec["giver_family_id"]] = rec["receiver_family_id"]
    analysis = analyze_history_coverage(family_ids, confirmed_history)

    history_records = []
    for yr in sorted(history_by_year.keys(), reverse=True):
        assignments = get_assignments_for_year(yr)
        history_records.append({
            "year": yr,
            "published": assignments[0]["published"] if assignments else 0,
            "published_at": assignments[0]["published_at"] if assignments else None,
            "provenance": assignments[0]["provenance"] if assignments else "official",
            "confidence": summary_by_year.get(yr, {}).get("confidence", "confirmed"),
            "confirmed_pairings": summary_by_year.get(yr, {}).get("confirmed_pairings", 0),
            "notes": summary_by_year.get(yr, {}).get("notes", ""),
            "assignments": assignments
        })

    spoilers_unlocked = bool(session.get("spoilers_unlocked", False))

    return render_template(
        "admin/history.html",
        history_records=history_records,
        history_summary=history_summary,
        analysis=analysis,
        family_map=family_map,
        spoilers_unlocked=spoilers_unlocked,
        active_tab="history"
    )


@admin_bp.route("/admin/history/manual", methods=["GET", "POST"])
@admin_required
def manual_history():
    """Manual past-history entry and CSV import view."""
    all_families = get_all_families()
    spoilers_unlocked = bool(session.get("spoilers_unlocked", False))

    if request.method == "POST":
        action = request.form.get("action", "entry")
        confidence = request.form.get("confidence", "confirmed")
        notes = request.form.get("notes", "").strip()
        overwrite = request.form.get("overwrite") == "1"

        if action == "csv_upload":
            csv_text = ""
            if "csv_file" in request.files and request.files["csv_file"].filename:
                file = request.files["csv_file"]
                csv_text = file.stream.read().decode("utf-8", errors="replace")
            else:
                csv_text = request.form.get("csv_text", "").strip()

            if not csv_text:
                flash("Please upload a CSV file or paste CSV content.", "error")
                return redirect(url_for("admin.manual_history"))

            default_year_raw = request.form.get("default_year", "").strip()
            default_year = int(default_year_raw) if default_year_raw.isdigit() else None

            count, errors = import_manual_history_from_csv(
                csv_content=csv_text,
                default_year=default_year,
                confidence=confidence,
                notes=notes,
                overwrite=overwrite
            )
            if errors:
                flash(f"CSV Import encountered issues: {'; '.join(errors[:3])}", "error")
            else:
                flash(f"Successfully imported {count} manual historical pairings!", "success")
            return redirect(url_for("admin.manual_history"))

        elif action == "manual_add":
            year_raw = request.form.get("year", "").strip()
            if not year_raw.isdigit():
                flash("A valid exchange year is required.", "error")
                return redirect(url_for("admin.manual_history"))
            target_year = int(year_raw)

            pairings_text = request.form.get("pairings_text", "").strip()
            pairs = []
            if pairings_text:
                for line in pairings_text.splitlines():
                    line = line.strip()
                    if not line or "," not in line:
                        continue
                    parts = [p.strip() for p in line.split(",", 1)]
                    g_val, r_val = parts[0], parts[1]
                    g_id = int(g_val) if g_val.isdigit() else next((f["id"] for f in all_families if f["name"].lower() == g_val.lower()), None)
                    r_id = int(r_val) if r_val.isdigit() else next((f["id"] for f in all_families if f["name"].lower() == r_val.lower()), None)
                    if g_id and r_id:
                        pairs.append((g_id, r_id))
                    else:
                        flash(f"Unrecognized family in row: '{line}'.", "error")
                        return redirect(url_for("admin.manual_history"))
            else:
                idx = 0
                while True:
                    g_key = f"giver_{idx}"
                    r_key = f"receiver_{idx}"
                    if g_key not in request.form:
                        break
                    g_val = request.form.get(g_key, "").strip()
                    r_val = request.form.get(r_key, "").strip()
                    if g_val.isdigit() and r_val.isdigit():
                        pairs.append((int(g_val), int(r_val)))
                    idx += 1

            if not pairs:
                flash("Please specify at least one giver-recipient pairing.", "error")
                return redirect(url_for("admin.manual_history"))

            try:
                save_manual_history_for_year(
                    year=target_year,
                    pairings=pairs,
                    confidence=confidence,
                    notes=notes,
                    overwrite=overwrite
                )
                flash(f"Successfully saved {len(pairs)} manual pairings for Christmas {target_year}!", "success")
            except ValueError as exc:
                flash(str(exc), "error")
            return redirect(url_for("admin.manual_history"))

    history_summary = get_historical_years_summary()
    manual_records = [h for h in history_summary if h["provenance"] == "manual"]

    return render_template(
        "admin/manual_history.html",
        active_tab="history",
        all_families=all_families,
        manual_records=manual_records,
        spoilers_unlocked=spoilers_unlocked
    )


@admin_bp.route("/admin/history/manual/<int:year>/delete", methods=["POST"])
@admin_required
def delete_manual_year(year: int):
    """Delete manual history for a specific year."""
    try:
        delete_manual_history_for_year(year)
        flash(f"Manual history for Christmas {year} has been removed.", "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.manual_history"))


@admin_bp.route("/admin/history/export-csv", methods=["GET"])
@admin_required
def export_history():
    """Export complete assignment history only after explicit spoiler unlock."""
    if not session.get("spoilers_unlocked", False):
        abort(403, description="Unlock spoiler protection explicitly before exporting real assignments.")
    csv_data = export_history_to_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=christmas_assignment_history.csv"}
    )


# ---------------------------------------------------------
# Guessing Game Administration
# ---------------------------------------------------------

@admin_bp.route("/admin/game/reveal", methods=["POST"])
@admin_required
def reveal_game():
    """Irreversibly reveal answers for the guess-the-giver game and lock voting."""
    current_year = int(get_setting("current_year", str(datetime.now().year)))
    if not is_year_published(current_year):
        flash("Assignments must be released to families before revealing guessing game answers.", "error")
        return redirect(url_for("admin.dashboard"))

    status = get_guess_submission_status(current_year)
    override = (request.form.get("override_pending") == "1")

    if not status["all_submitted"] and not override:
        pending_names = [f["name"] for f in status["pending_families"]]
        flash(
            f"Cannot reveal answers: {status['submitted_count']} of {status['total_active']} households have submitted ballots. "
            f"Pending ballots: {', '.join(pending_names)}. All active households must submit ballots before revealing answers (or check the explicit override).",
            "error"
        )
        return redirect(url_for("admin.dashboard"))

    reveal_guesses_for_year(current_year)
    flash(f"Guess-the-giver answers for Christmas {current_year} have been revealed to all families! Voting is now locked.", "success")
    return redirect(url_for("admin.dashboard"))


# ---------------------------------------------------------
# Settings Operations
# ---------------------------------------------------------

@admin_bp.route("/admin/settings/update", methods=["POST"])
@admin_required
def update_settings():
    """Update system and algorithm settings with mathematical validation."""
    current_year = request.form.get("current_year", "").strip()
    anchor_year = request.form.get("cycle_anchor_year", "").strip()
    assignment_mode = request.form.get("assignment_mode", "").strip()
    no_repeat_years = request.form.get("no_repeat_years", "1").strip()
    enforce_coverage = (request.form.get("enforce_sliding_coverage", "0") == "1")
    coverage_k = request.form.get("coverage_window_k", "4").strip()
    waiver_confirmed = (request.form.get("waiver_confirmed", "0") == "1")

    active_families = get_all_families(active_only=True)
    n = len(active_families)

    try:
        l_int = max(0, int(no_repeat_years))
    except ValueError:
        l_int = 1

    try:
        k_int = max(1, int(coverage_k))
    except ValueError:
        k_int = 4

    # Validate mathematical feasibility
    feas = validate_rules_feasibility(n, l_int, k_int, enforce_coverage)
    if not feas["is_feasible"] and not waiver_confirmed:
        flash(
            f"Mathematical check failed: {' '.join(feas['errors'])} "
            f"Suggested values: Lookback L ≤ {feas['suggested_l']}, Window K ≥ {feas['suggested_k']}. "
            f"To bypass this check, you must explicitly confirm the waiver checkbox.",
            "error"
        )
        return redirect(url_for("admin.dashboard"))

    if current_year.isdigit() and len(current_year) == 4:
        set_setting("current_year", current_year)
    if anchor_year.isdigit() and len(anchor_year) == 4:
        set_setting("cycle_anchor_year", anchor_year)
    if assignment_mode in ("shuffled_rotation", "shuffled_repeating_rotation", "fixed_rotation", "randomized_cycle", "predictable_rotation", "uniform_random"):
        set_setting("assignment_mode", assignment_mode)

    set_setting("no_repeat_years", str(l_int))
    set_setting("enforce_sliding_coverage", "1" if enforce_coverage else "0")
    set_setting("coverage_window_k", str(k_int))

    # Fixed rotation config
    fixed_h = request.form.get("fixed_household_order", "").strip()
    fixed_o = request.form.get("fixed_offset_order", "").strip()
    if fixed_h:
        set_setting("fixed_household_order", fixed_h)
    if fixed_o:
        set_setting("fixed_offset_order", fixed_o)

    curr_yr_int = int(get_setting("current_year", "2026"))
    existing_assignments = get_assignments_for_year(curr_yr_int)
    if existing_assignments:
        flash(
            "Settings updated. Notice: Official assignments have already been prepared/published for this year. "
            "Altering settings will not affect current assignments unless you discard draft and regenerate.",
            "warning"
        )
    else:
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
