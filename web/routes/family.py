"""Family portal routes for viewing assignments, managing wishlists, and guessing game."""

from datetime import datetime
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from web.models import (
    calculate_game_scores,
    get_all_families,
    get_family_activity,
    get_family_ballot,
    get_family_by_token,
    get_family_recipient_for_year,
    get_family_wishlist,
    get_setting,
    get_wishlist_as_text,
    is_guesses_revealed,
    is_year_published,
    record_family_assignment_confirmation,
    record_family_visit,
    record_family_wishlist_update,
    set_family_wishlist_from_text,
    submit_guess_ballot,
    update_family_contact_and_notes,
)

from web.requests import (family_requests_enabled, has_request, request_choices, requests_are_open, save_request)


family_bp = Blueprint("family", __name__, url_prefix="/christmas")


@family_bp.route("/family/<token>", methods=["GET"])
@family_bp.route("/f/<token>", methods=["GET"])
def portal(token: str):
    """Private family portal view.

    Displays recipient assignment (if published) and tools to update
    their own family's wish list, mailing address, and notes.
    Also hosts the optional guess-the-giver game after release.
    """
    family = get_family_by_token(token)
    if not family:
        abort(404, description="Invalid or expired family link. Please check your link or contact the gift exchange organizer.")

    current_year = int(get_setting("current_year", str(datetime.now().year)))
    raw_year = request.args.get("year", str(current_year))
    if not raw_year.isdigit() or not 2000 <= int(raw_year) <= current_year:
        abort(404, description="That exchange year is not available.")
    requested_year = int(raw_year)
    # Explicit year links continue to point to their archived exchange, without
    # accidentally opening a newer year's recipient or allowing old-year edits.
    if requested_year != current_year:
        if not is_year_published(requested_year):
            abort(404, description="That exchange year has not been released.")
        old_recipient = get_family_recipient_for_year(family["id"], requested_year)
        return render_template(
            "family/archive.html", family=family, token=token,
            year=requested_year, current_year=current_year,
            recipient=old_recipient,
        )
    published = is_year_published(current_year)

    # Record link visit in family_activity (minimal timestamp, no secret logged)
    record_family_visit(family["id"], current_year)
    activity = get_family_activity(family["id"], current_year)
    is_confirmed = bool(activity and activity["assignment_confirmed_at"])

    recipient = None
    recipient_wishlist = []
    active_families = []
    existing_guesses = {}
    game_results = None
    guesses_revealed = False

    if published:
        recipient = get_family_recipient_for_year(family["id"], current_year)
        if recipient:
            recipient_wishlist = get_family_wishlist(recipient["receiver_id"])

        active_families = get_all_families(active_only=True)
        guesses_revealed = is_guesses_revealed(current_year)
        existing_guesses = get_family_ballot(current_year, family["id"])

        if guesses_revealed:
            game_results = calculate_game_scores(current_year)

    show_game = bool(
        request.args.get("game") == "1"
        or existing_guesses
        or guesses_revealed
        or (get_setting(f"guessing_game_enabled_{current_year}") == "1")
    )

    # Requests are available to the designated email-preview test household by
    # default, not to all families. Server-side POSTs enforce the same rule.
    request_feature_enabled = family_requests_enabled(current_year, family["id"])
    request_open = bool(request_feature_enabled and requests_are_open(current_year))
    request_eligible, request_excluded = (
        request_choices(current_year, family["id"]) if request_open else ([], [])
    )
    request_submitted = has_request(current_year, family["id"])

    # Own wishlist
    own_wishlist_text = get_wishlist_as_text(family["id"])
    own_wishlist_items = get_family_wishlist(family["id"])

    return render_template(
        "family/portal.html",
        family=family,
        token=token,
        current_year=current_year,
        published=published,
        recipient=recipient,
        recipient_wishlist=recipient_wishlist,
        own_wishlist_text=own_wishlist_text,
        own_wishlist_items=own_wishlist_items,
        active_families=active_families,
        existing_guesses=existing_guesses,
        guesses_revealed=guesses_revealed,
        game_results=game_results,
        show_game=show_game,
        is_confirmed=is_confirmed,
        request_open=request_open,
        request_feature_enabled=request_feature_enabled,
        request_eligible=request_eligible,
        request_excluded=request_excluded,
        request_submitted=request_submitted,
        assignment_confirmed_at=activity["assignment_confirmed_at"] if activity else None
    )


@family_bp.route("/f/<token>/request", methods=["POST"])
@family_bp.route("/family/<token>/request", methods=["POST"])
def submit_recipient_request(token: str):
    """Private request: never accept a giver ID from the submitted form."""
    family = get_family_by_token(token)
    if not family:
        abort(404)
    year = int(get_setting("current_year", str(datetime.now().year)))
    if not family_requests_enabled(year, family["id"]):
        abort(403, description="Recipient requests are not enabled for this household.")
    if request.form.get("action") == "withdraw":
        recipient_id = None
    else:
        raw = request.form.get("recipient_id", "").strip()
        if not raw.isdigit():
            flash("Choose an eligible recipient, or withdraw your request.", "error")
            return redirect(url_for("family.portal", token=token))
        recipient_id = int(raw)
    try:
        save_request(year, family["id"], recipient_id)
        flash(
            "Your private request was removed." if recipient_id is None else
            "Your private request was saved. It is optional and not guaranteed.",
            "success"
        )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("family.portal", token=token))


@family_bp.route("/f/<token>/confirm-receipt", methods=["POST"])
@family_bp.route("/family/<token>/confirm-receipt", methods=["POST"])
def confirm_receipt(token: str):
    """Explicitly confirm receipt of assignment after release."""
    family = get_family_by_token(token)
    if not family:
        abort(404)

    current_year = int(get_setting("current_year", str(datetime.now().year)))
    if not is_year_published(current_year):
        flash("Assignments have not been released yet.", "error")
        return redirect(url_for("family.portal", token=token))

    record_family_assignment_confirmation(family["id"], current_year)
    flash("Thank you! We've recorded that you received your gift assignment.", "success")
    return redirect(url_for("family.portal", token=token))


@family_bp.route("/f/<token>/wishlist", methods=["POST"])
@family_bp.route("/family/<token>/wishlist", methods=["POST"])
def update_wishlist(token: str):
    """Update family's own wishlist items."""
    family = get_family_by_token(token)
    if not family:
        abort(404)

    current_year = int(get_setting("current_year", str(datetime.now().year)))
    wishlist_text = request.form.get("wishlist_text", "")
    set_family_wishlist_from_text(family["id"], wishlist_text)
    record_family_wishlist_update(family["id"], current_year)
    flash("Your family's wish list has been saved!", "success")
    return redirect(url_for("family.portal", token=token))


@family_bp.route("/f/<token>/contact", methods=["POST"])
@family_bp.route("/family/<token>/contact", methods=["POST"])
def update_contact(token: str):
    """Update family's own mailing address and notes."""
    family = get_family_by_token(token)
    if not family:
        abort(404)

    mailing_address = request.form.get("mailing_address", "")
    notes = request.form.get("notes", "")

    update_family_contact_and_notes(family["id"], mailing_address, notes)
    flash("Your mailing address and notes have been updated!", "success")
    return redirect(url_for("family.portal", token=token))


@family_bp.route("/f/<token>/guess", methods=["POST"])
@family_bp.route("/family/<token>/guess", methods=["POST"])
def submit_guesses(token: str):
    """Submit or update family's guess ballot for the guess-the-giver game."""
    family = get_family_by_token(token)
    if not family:
        abort(404)

    current_year = int(get_setting("current_year", str(datetime.now().year)))
    if not is_year_published(current_year):
        flash("Assignments have not been released yet.", "error")
        return redirect(url_for("family.portal", token=token))

    if is_guesses_revealed(current_year):
        flash("Guessing is closed. The organizer has already revealed the answers!", "error")
        return redirect(url_for("family.portal", token=token))

    active_families = get_all_families(active_only=True)
    active_ids = {f["id"] for f in active_families}

    guesses = {}
    for f in active_families:
        field_name = f"guess_{f['id']}"
        val = request.form.get(field_name, "").strip()
        if val.isdigit():
            guessed_id = int(val)
            if guessed_id in active_ids:
                guesses[f["id"]] = guessed_id
        else:
            guesses[f["id"]] = None

    try:
        submit_guess_ballot(current_year, family["id"], guesses)
        flash("Your guesses have been saved! You can update them until the organizer reveals the answers.", "success")
    except ValueError as exc:
        flash(str(exc), "error")

    return redirect(url_for("family.portal", token=token))
