"""Organizer communications tab: reusable drafts, merge previews, sends and history."""
import secrets

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for,
)
from web.communications import (
    PLACEHOLDERS, ensure_templates, get_preview_family_id, get_template,
    legacy_email_history, list_templates, merge_fields, message_history,
    recipient_emails, render_message, save_template, send_campaign,
    set_preview_family_id, smtp_ready,
)
from web.models import get_all_families, get_family_by_id, get_setting
from web.security import admin_required

communications_bp = Blueprint("communications", __name__, url_prefix="/christmas/admin/communications")


def current_year():
    return int(get_setting("current_year", "2026"))


@communications_bp.route("", methods=["GET"])
@communications_bp.route("/", methods=["GET"])
@admin_required
def page():
    ensure_templates()
    families = get_all_families(active_only=True)
    templates = list_templates()
    raw_id = request.args.get("template", "")
    selected = get_template(int(raw_id)) if raw_id.isdigit() else None
    selected = selected or (templates[0] if templates else None)
    preview_id = get_preview_family_id(families)
    year = current_year()
    raw_history = request.args.get("history_year", "")
    history_year = int(raw_history) if raw_history.isdigit() and 2000 <= int(raw_history) <= year else year
    return render_template(
        "admin/communications.html",
        active_tab="communications",
        current_year=year,
        history_year=history_year,
        families=families,
        templates=templates,
        selected_template=selected,
        preview_family_id=preview_id,
        placeholders=PLACEHOLDERS,
        messages=message_history(history_year),
        legacy_messages=legacy_email_history(history_year),
        smtp_available=smtp_ready(),
        dispatch_id=secrets.token_hex(16),
    )


@communications_bp.route("/template", methods=["POST"])
@admin_required
def save_message_template():
    raw_id = request.form.get("template_id", "")
    template_id = int(raw_id) if raw_id.isdigit() else None
    try:
        saved_id = save_template(
            template_id, request.form.get("name"), request.form.get("kind"),
            request.form.get("subject"), request.form.get("body"),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("communications.page", **({"template": template_id} if template_id else {})))
    flash("Message template saved. Nothing was sent.", "success")
    return redirect(url_for("communications.page", template=saved_id))


@communications_bp.route("/preview-family", methods=["POST"])
@admin_required
def save_preview_family():
    raw_id = request.form.get("preview_family_id", "")
    try:
        if not raw_id.isdigit():
            raise ValueError("Choose a preview household.")
        set_preview_family_id(int(raw_id))
        flash("Default preview household saved.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("communications.page"))


@communications_bp.route("/preview", methods=["POST"])
@admin_required
def preview():
    """Render unsaved draft fields without sending or logging."""
    raw_id = request.form.get("preview_family_id", "")
    try:
        family_id = int(raw_id)
        family = get_family_by_id(family_id)
        if not family or not family["active"]:
            raise ValueError("Choose an active preview family.")
        subject, body = render_message(
            current_year(), family_id,
            request.form.get("subject", ""), request.form.get("body", ""),
        )
        return jsonify({
            "ok": True, "family": family["name"],
            "to": ", ".join(recipient_emails(family)),
            "subject": subject, "body": body,
        })
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@communications_bp.route("/send", methods=["POST"])
@admin_required
def send():
    """Explicit send only: saved template + chosen scope + confirmation phrase."""
    if request.form.get("confirm_send") != "SEND":
        flash("No email was sent. Confirm by typing SEND.", "error")
        return redirect(url_for("communications.page"))
    raw = request.form.get("template_id", "")
    if not raw.isdigit():
        flash("Save and select a template before sending.", "error")
        return redirect(url_for("communications.page"))
    template_id = int(raw)
    scope = request.form.get("scope")
    families = get_all_families(active_only=True)
    if scope == "all":
        ids = [family["id"] for family in families]
    elif scope == "one":
        raw_id = request.form.get("family_id", "")
        ids = [int(raw_id)] if raw_id.isdigit() else []
    else:
        ids = []
    try:
        counts = send_campaign(
            current_year(), template_id, ids, request.form.get("dispatch_id", "")
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("communications.page", template=template_id))
    if counts["failed"]:
        flash(
            f"Mail server accepted {counts['accepted']} message(s), and {counts['failed']} attempt(s) failed. "
            "Review the history before retrying; a timeout may mean an email was accepted.",
            "warning",
        )
    else:
        flash(
            f"Mail server accepted {counts['accepted']} message(s). This does not confirm delivery.",
            "success",
        )
    return redirect(url_for("communications.page", template=template_id))
