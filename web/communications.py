"""Organizer-only communications: templates, safe merge fields, previews, dispatch, history.

All outbound sends require an explicit organizer action. Merger values are
server-derived; the browser never supplies recipient identities or addresses.
"""
import html
import re
import secrets
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

from flask import current_app
from web.db import get_db
from web.models import (
    get_all_families, get_family_by_id, get_family_wishlist, get_setting,
    is_year_published, set_setting,
)

PLACEHOLDERS = (
    ("Year", "{{year}}", False),
    ("Your family", "{{family_name}}", False),
    ("Your household members", "{{family_members}}", False),
    ("Your mailing address", "{{family_address}}", False),
    ("Private family link", "{{private_url}}", False),
    ("Year-specific private link", "{{year_portal_url}}", False),
    ("Submit a request link", "{{request_url}}", False),
    ("Assigned family", "{{recipient_name}}", True),
    ("Assignment sentence", "{{assignment_sentence}}", True),
    ("Recipient address", "{{recipient_address}}", True),
    ("Recipient wishlist", "{{recipient_wishlist}}", True),
    ("Recipient preferences / notes", "{{recipient_notes}}", True),
)
TOKENS = {token: needs for _, token, needs in PLACEHOLDERS}
TOKEN_PATTERN = re.compile(r"{{\s*([a-z_]+)\s*}}")
ASSIGNMENT_FIELDS = {token for token, needs in TOKENS.items() if needs}
MAX_SUBJECT = 180
MAX_BODY = 16000

DEFAULT_TEMPLATES = (
    ("Welcome & requests", "welcome",
     "Christmas {{year}} gift exchange — welcome!",
     "Hi {{family_name}} family,\n\n"
     "Welcome to the Christmas {{year}} gift exchange!\n\n"
     "Before assignments are drawn, you can privately request a family "
     "you would like to give to. Requests are optional and are not guaranteed. "
     "Please also update your family's wish list and mailing address.\n\n"
     "Your private family page: {{year_portal_url}}\n\n"
     "Keep this link private. We'll send your assignment separately when it's ready.\n\n"
     "Merry Christmas!"),
    ("Assignment announcement", "assignment",
     "Christmas {{year}} — your family assignment",
     "Hi {{family_name}} family,\n\n"
     "{{assignment_sentence}}\n\n"
     "Mailing address:\n{{recipient_address}}\n\n"
     "Wish list:\n{{recipient_wishlist}}\n\n"
     "Other details:\n{{recipient_notes}}\n\n"
     "View your private Christmas {{year}} page: {{year_portal_url}}\n\n"
     "Please keep your assignment and link private. Merry Christmas!"),
    ("Friendly reminder", "reminder",
     "Christmas {{year}} exchange — reminder",
     "Hi {{family_name}} family,\n\n"
     "Just a friendly reminder about this year's gift exchange. "
     "You can check your assignment and update your wish list here:\n"
     "{{year_portal_url}}\n\n"
     "Happy gifting!"),
)


def ensure_templates():
    """Seed the three defaults once. Existing custom edits are never overwritten."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        for name, kind, subject, body in DEFAULT_TEMPLATES:
            db.execute(
                """INSERT OR IGNORE INTO communication_templates
                   (name, kind, subject, body, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, kind, subject, body, now, now),
            )


def list_templates():
    return get_db().execute(
        "SELECT * FROM communication_templates WHERE archived=0 ORDER BY id"
    ).fetchall()


def get_template(template_id):
    return get_db().execute(
        "SELECT * FROM communication_templates WHERE id=? AND archived=0",
        (template_id,)
    ).fetchone()


def _validate_template(name, kind, subject, body):
    name = (name or "").strip()
    kind = (kind or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if kind not in ("welcome", "assignment", "reminder", "custom"):
        raise ValueError("Choose a valid message category.")
    if not name or len(name) > 100:
        raise ValueError("Template name must be 1–100 characters.")
    if not subject or len(subject) > MAX_SUBJECT:
        raise ValueError("Subject must be 1–180 characters.")
    if not body or len(body) > MAX_BODY:
        raise ValueError("Message body must be 1–16,000 characters.")
    unknown = sorted({
        "{{" + match.group(1) + "}}"
        for field in (subject, body)
        for match in TOKEN_PATTERN.finditer(field)
        if "{{" + match.group(1) + "}}" not in TOKENS
    })
    if unknown:
        raise ValueError("Unknown mail-merge fields: " + ", ".join(unknown))
    if "{{" in TOKEN_PATTERN.sub("", subject + body):
        raise ValueError("Invalid merge-field syntax. Use a field from the toolbar.")
    if "\n" in subject or "\r" in subject:
        raise ValueError("Email subjects cannot contain line breaks.")
    return name, kind, subject, body


def save_template(template_id, name, kind, subject, body):
    name, kind, subject, body = _validate_template(name, kind, subject, body)
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    with db:
        if template_id:
            row = get_template(template_id)
            if row is None:
                raise ValueError("Template not found.")
            db.execute(
                """UPDATE communication_templates
                   SET name=?, kind=?, subject=?, body=?, updated_at=?
                   WHERE id=? AND archived=0""",
                (name, kind, subject, body, now, template_id)
            )
            return template_id
        return db.execute(
            """INSERT INTO communication_templates
               (name, kind, subject, body, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, kind, subject, body, now, now)
        ).lastrowid


def get_preview_family_id(families):
    ids = {f["id"] for f in families}
    raw = get_setting("communications_preview_family_id", "")
    if raw and raw.isdigit() and int(raw) in ids:
        return int(raw)
    for f in families:
        if "taylor" in (f["name"] + " " + (f["members"] or "")).lower():
            return f["id"]
    return families[0]["id"] if families else None


def set_preview_family_id(family_id):
    if get_family_by_id(family_id) is None:
        raise ValueError("Unknown preview family.")
    set_setting("communications_preview_family_id", str(family_id))


def _url(year, family):
    base = current_app.config.get(
        "CHRISTMAS_PUBLIC_BASE_URL", "https://taylorarchibald.com"
    ).rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("Public base URL must use HTTPS.")
    token = family["access_token"]
    return f"{base}/christmas/f/{token}", f"{base}/christmas/f/{token}?year={year}"


def _assignment(year, giver_id):
    return get_db().execute(
        """SELECT r.id, r.name, r.mailing_address, r.notes
           FROM assignments a JOIN families r ON r.id=a.receiver_family_id
           WHERE a.year=? AND a.giver_family_id=? AND a.published=1
             AND a.provenance='official'""",
        (year, giver_id)
    ).fetchone()


def merge_fields(year, family_id):
    family = get_family_by_id(family_id)
    if family is None or not family["active"]:
        raise ValueError("Preview recipient is not an active household.")
    link, year_link = _url(year, family)
    variables = {
        "year": str(year),
        "family_name": family["name"],
        "family_members": family["members"] or "(Not provided)",
        "family_address": family["mailing_address"] or "(Not provided)",
        "private_url": link,
        "year_portal_url": year_link,
        "request_url": year_link,
    }
    # Never read a sealed recipient pairing into preview or outgoing mail.
    if is_year_published(year):
        recipient = _assignment(year, family_id)
        if recipient:
            wishlist = get_family_wishlist(recipient["id"])
            variables.update({
                "recipient_name": recipient["name"],
                "assignment_sentence":
                    f"Your family is giving a gift to {recipient['name']}!",
                "recipient_address": recipient["mailing_address"] or "(Not provided)",
                "recipient_wishlist": "\n".join(x["item_text"] for x in wishlist) or "(No wishlist items yet)",
                "recipient_notes": recipient["notes"] or "(None provided)",
            })
    return variables


def render_message(year, family_id, subject, body):
    """Render server-side with a strict field whitelist and no expression evaluator."""
    _validate_template("Preview", "custom", subject, body)
    vars_ = merge_fields(year, family_id)
    requested_keys = {
        match.group(1)
        for text in (subject, body)
        for match in TOKEN_PATTERN.finditer(text)
    }
    unavailable = sorted(key for key in requested_keys if key not in vars_)
    if unavailable:
        raise ValueError(
            "This message uses assignment details that are unavailable until "
            "the year's official assignments are released."
        )

    def replace(match):
        return str(vars_[match.group(1)])

    final_subject = TOKEN_PATTERN.sub(replace, subject)
    final_body = TOKEN_PATTERN.sub(replace, body)
    if "\r" in final_subject or "\n" in final_subject:
        raise ValueError("A merge field made the subject invalid.")
    return final_subject, final_body


def recipient_emails(family):
    """Use only roster addresses, not addresses supplied by a POST."""
    unique = set()
    result = []
    for address in re.split(r"[,;\n]+", family["emails"] or ""):
        clean = address.strip()
        _, parsed = parseaddr(clean)
        if not clean or parsed != clean or "@" not in parsed or any(x.isspace() for x in parsed):
            continue
        if parsed.casefold() not in unique:
            unique.add(parsed.casefold())
            result.append(parsed)
    return result


def smtp_ready():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_FROM"))


def _smtp_send(address, subject, body):
    cfg = current_app.config
    message = EmailMessage()
    message["From"] = cfg["SMTP_FROM"]
    message["To"] = address
    message["Subject"] = subject
    message.set_content(body)
    safe_body = html.escape(body).replace("\n", "<br>\n")
    message.add_alternative(
        "<!doctype html><html><body style='background:#faf9f6;padding:24px;"
        "font-family:Arial,sans-serif;color:#233e33'>"
        "<main style='max-width:620px;margin:auto;padding:26px;"
        "background:#fff;border:1px solid #e6e3dc;border-radius:12px'>"
        "<h2 style='color:#1a4332'>🎄 Family Christmas Exchange</h2>"
        f"<div style='line-height:1.65;white-space:normal'>{safe_body}</div>"
        "</main></body></html>",
        subtype="html",
    )
    host, port = cfg["SMTP_HOST"], cfg.get("SMTP_PORT", 587)
    server = (smtplib.SMTP_SSL(host, port, timeout=15)
              if port == 465 else smtplib.SMTP(host, port, timeout=15))
    try:
        if port != 465 and cfg.get("SMTP_USE_TLS", True):
            server.starttls()
        if cfg.get("SMTP_USER") and cfg.get("SMTP_PASS"):
            server.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
        server.send_message(message)
    finally:
        server.quit()


def send_campaign(year, template_id, family_ids, dispatch_id):
    """Explicit send, immutable per-address snapshots, anti-double-submit key.

    SMTP accepted means the relay accepted a message, NOT that it was delivered.
    An exception may occur after relay acceptance; do not silently auto-retry.
    """
    if not smtp_ready():
        raise ValueError("SMTP is not configured; no messages were sent.")
    if not re.fullmatch(r"[a-f0-9]{32}", dispatch_id or ""):
        raise ValueError("Invalid send confirmation; reload Communications.")
    template = get_template(template_id)
    if template is None:
        raise ValueError("Select a saved template before sending.")
    families = {f["id"]: f for f in get_all_families(active_only=True)}
    selected = sorted(set(family_ids))
    if not selected or any(i not in families for i in selected):
        raise ValueError("Choose one or more participating families.")
    if len(selected) > 50:
        raise ValueError("Bulk sending is limited to 50 families at a time.")

    # Preflight all recipients and every field before dispatching anything.
    prepared = []
    for family_id in selected:
        family = families[family_id]
        emails = recipient_emails(family)
        if not emails:
            raise ValueError(f"{family['name']} has no valid email address.")
        subject, body = render_message(
            year, family_id, template["subject"], template["body"])
        for addr in emails:
            prepared.append((family_id, addr, subject, body))

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        with db:
            db.execute(
                """INSERT INTO communication_dispatches
                   (dispatch_id, year, template_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (dispatch_id, year, template_id, now))
            db.executemany(
                """INSERT INTO communication_messages
                   (dispatch_id, year, template_name, family_id, recipient_email,
                    subject, body, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                [(dispatch_id, year, template["name"], fid, addr, subject, body, now)
                 for fid, addr, subject, body in prepared]
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("This send was already submitted. Refresh the page to start another.") from exc

    accepted, failed = 0, 0
    # Messages are persisted before the SMTP call; no duplicate send on POST replay.
    rows = db.execute(
        """SELECT id, recipient_email, subject, body FROM communication_messages
           WHERE dispatch_id=? ORDER BY id""", (dispatch_id,)
    ).fetchall()
    for row in rows:
        with db:
            db.execute(
                "UPDATE communication_messages SET status='attempting' WHERE id=?",
                (row["id"],))
        try:
            _smtp_send(row["recipient_email"], row["subject"], row["body"])
            status, error = "accepted", ""
            accepted += 1
        except Exception as exc:
            status, error = "failed", type(exc).__name__ + ": " + str(exc)[:350]
            failed += 1
        with db:
            db.execute(
                """UPDATE communication_messages
                   SET status=?, error_message=?, completed_at=?
                   WHERE id=?""",
                (status, error, datetime.now(timezone.utc).isoformat(), row["id"]))
    return {"accepted": accepted, "failed": failed}


def message_history(year, limit=200):
    return get_db().execute(
        """SELECT cm.*, f.name AS family_name
           FROM communication_messages cm
           JOIN families f ON f.id=cm.family_id
           WHERE cm.year=?
           ORDER BY cm.id DESC LIMIT ?""",
        (year, limit)
    ).fetchall()


def legacy_email_history(year):
    return get_db().execute(
        """SELECT el.*, f.name AS family_name
           FROM email_logs el JOIN families f ON f.id=el.family_id
           WHERE el.year=? ORDER BY el.id DESC""",
        (year,)
    ).fetchall()
