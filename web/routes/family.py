"""Family portal routes for viewing assignments and managing wishlists."""

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
    get_family_by_token,
    get_family_recipient_for_year,
    get_family_wishlist,
    get_setting,
    get_wishlist_as_text,
    is_year_published,
    set_family_wishlist_from_text,
    update_family_contact_and_notes,
)

family_bp = Blueprint("family", __name__, url_prefix="/christmas")


@family_bp.route("/family/<token>", methods=["GET"])
@family_bp.route("/f/<token>", methods=["GET"])
def portal(token: str):
    """Private family portal view.

    Displays recipient assignment (if published) and tools to update
    their own family's wish list, mailing address, and notes.
    """
    family = get_family_by_token(token)
    if not family:
        abort(404, description="Invalid or expired family link. Please check your link or contact the gift exchange organizer.")

    current_year = int(get_setting("current_year", str(datetime.now().year)))
    published = is_year_published(current_year)

    recipient = None
    recipient_wishlist = []
    if published:
        recipient = get_family_recipient_for_year(family["id"], current_year)
        if recipient:
            recipient_wishlist = get_family_wishlist(recipient["receiver_id"])

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
        own_wishlist_items=own_wishlist_items
    )


@family_bp.route("/f/<token>/wishlist", methods=["POST"])
@family_bp.route("/family/<token>/wishlist", methods=["POST"])
def update_wishlist(token: str):
    """Update family's own wishlist items."""
    family = get_family_by_token(token)
    if not family:
        abort(404)

    wishlist_text = request.form.get("wishlist_text", "")
    set_family_wishlist_from_text(family["id"], wishlist_text)
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
