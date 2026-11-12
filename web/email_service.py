"""Email and invitation service supporting manual mailto and optional SMTP batch delivery."""

import smtplib
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Tuple

from flask import current_app
from web.db import get_db
from web.models import get_all_families, get_assignments_for_year


def is_smtp_configured() -> bool:
    """Check if SMTP credentials and server settings are fully configured in environment."""
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_FROM"))


def generate_mailto_link(family_name: str, emails: str, portal_url: str, year: int) -> str:
    """Generate a pre-filled mailto: URI for sending assignments directly from the organizer's mail client."""
    recipients = emails.replace("\n", ",").replace(";", ",")
    # Extract first email or clean list
    clean_recipients = ",".join(e.strip() for e in recipients.split(",") if e.strip())

    subject = f"Christmas {year} Gift Exchange: Your Family Assignment"
    body = (
        f"Hi {family_name} Family,\n\n"
        f"The assignments for the Christmas {year} Family Gift Exchange are ready!\n\n"
        f"Access your family's private portal here:\n"
        f"{portal_url}\n\n"
        f"In your portal, you can:\n"
        f"1. See the family you are giving to and their mailing address.\n"
        f"2. View their live wish list and notes.\n"
        f"3. Update your own family's wish list, mailing address, and gift notes.\n\n"
        f"Please keep your private link safe and do not share it with other families so the secret giver stays a secret!\n\n"
        f"Merry Christmas!"
    )

    query = urllib.parse.urlencode({
        "subject": subject,
        "body": body
    }, quote_via=urllib.parse.quote)

    return f"mailto:{clean_recipients}?{query}"


def log_email_status(
    year: int,
    family_id: int,
    recipient_email: str,
    status: str,
    error_message: str = ""
):
    """Record email dispatch status in the database."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            INSERT INTO email_logs (year, family_id, recipient_email, status, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (year, family_id, recipient_email, status, error_message, now)
        )


def get_email_logs_for_year(year: int) -> List[sqlite3.Row]:
    """Retrieve all email logs for a specific year."""
    db = get_db()
    return db.execute(
        """
        SELECT el.*, f.name as family_name
        FROM email_logs el
        JOIN families f ON el.family_id = f.id
        WHERE el.year = ?
        ORDER BY el.sent_at DESC
        """,
        (year,)
    ).fetchall()


def get_latest_email_status_per_family(year: int) -> Dict[int, Dict[str, str]]:
    """Retrieve the latest email delivery status for each family for a given year."""
    db = get_db()
    rows = db.execute(
        """
        SELECT el.family_id, el.status, el.error_message, el.sent_at
        FROM email_logs el
        INNER JOIN (
            SELECT family_id, MAX(id) as max_id
            FROM email_logs
            WHERE year = ?
            GROUP BY family_id
        ) latest ON el.id = latest.max_id
        """,
        (year,)
    ).fetchall()

    return {
        r["family_id"]: {
            "status": r["status"],
            "error_message": r["error_message"],
            "sent_at": r["sent_at"]
        }
        for r in rows
    }


def send_smtp_invitation(
    family_name: str,
    recipient_email: str,
    portal_url: str,
    year: int
) -> Tuple[bool, str]:
    """Send an invitation email via configured SMTP server."""
    cfg = current_app.config
    host = cfg.get("SMTP_HOST")
    port = cfg.get("SMTP_PORT", 587)
    user = cfg.get("SMTP_USER")
    password = cfg.get("SMTP_PASS")
    sender = cfg.get("SMTP_FROM")
    use_tls = cfg.get("SMTP_USE_TLS", True)

    if not host or not sender:
        return False, "SMTP is not configured."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Christmas {year} Gift Exchange: Your Family Assignment"
    msg["From"] = sender
    msg["To"] = recipient_email

    text_body = (
        f"Hi {family_name} Family,\n\n"
        f"The assignments for the Christmas {year} Family Gift Exchange are ready!\n\n"
        f"Access your family's private portal here:\n{portal_url}\n\n"
        f"In your portal, you can view your recipient, their address, and their wish list, "
        f"as well as update your own family's wish list.\n\n"
        f"Merry Christmas!"
    )

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #2d3748; background-color: #fcfbf7; padding: 24px;">
  <div style="max-width: 580px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 32px; box-shadow: 0 4px 6px rgba(0,0,0,0.04);">
    <div style="text-align: center; margin-bottom: 24px;">
      <span style="font-size: 36px;">🎄</span>
      <h2 style="color: #1a4332; margin: 8px 0 0 0;">Christmas {year} Gift Exchange</h2>
    </div>
    <p>Hi <strong>{family_name} Family</strong>,</p>
    <p>Assignments for this year's family gift exchange are published and ready for you!</p>
    <div style="text-align: center; margin: 32px 0;">
      <a href="{portal_url}" style="background-color: #a32027; color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 6px; font-weight: 600; display: inline-block;">View Your Assignment & Wish List</a>
    </div>
    <p style="font-size: 14px; color: #4a5568;">
      Or copy and paste this private link into your browser:<br>
      <a href="{portal_url}" style="color: #1a4332; word-break: break-all;">{portal_url}</a>
    </p>
    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
    <p style="font-size: 13px; color: #718096; margin-bottom: 0;">
      Please keep your link private so the secret gift giver stays a secret!
    </p>
  </div>
</body>
</html>"""

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if use_tls:
                server.starttls()

        if user and password:
            server.login(user, password)

        server.send_message(msg)
        server.quit()
        return True, ""
    except Exception as e:
        return False, str(e)


def send_batch_invitations(
    year: int,
    base_url: str,
    only_failed: bool = False
) -> Dict[str, object]:
    """Execute batch email invitations for published assignments.

    NEVER runs automatically — only when explicitly triggered by admin action.
    """
    assignments = get_assignments_for_year(year)
    if not assignments or not assignments[0]["published"]:
        return {
            "success": False,
            "message": "Assignments for this year are not published yet.",
            "sent_count": 0,
            "failed_count": 0
        }

    latest_statuses = get_latest_email_status_per_family(year)
    clean_base = base_url.rstrip("/")

    sent_count = 0
    failed_count = 0
    errors: List[str] = []

    for a in assignments:
        fam_id = a["giver_id"]
        fam_name = a["giver_name"]
        raw_emails = a["giver_emails"] or ""
        token = a["giver_token"]
        portal_url = f"{clean_base}/christmas/f/{token}"

        if only_failed:
            curr = latest_statuses.get(fam_id)
            if curr and curr["status"] == "sent":
                continue

        emails = [
            e.strip() for e in raw_emails.replace("\n", ",").replace(";", ",").split(",")
            if e.strip() and "@" in e
        ]

        if not emails:
            msg = f"{fam_name}: No valid email address configured."
            log_email_status(year, fam_id, "", "failed", msg)
            failed_count += 1
            errors.append(msg)
            continue

        family_all_ok = True
        for email in emails:
            success, err_msg = send_smtp_invitation(fam_name, email, portal_url, year)
            status = "sent" if success else "failed"
            log_email_status(year, fam_id, email, status, err_msg)
            if not success:
                family_all_ok = False
                errors.append(f"{fam_name} ({email}): {err_msg}")

        if family_all_ok:
            sent_count += 1
        else:
            failed_count += 1

    return {
        "success": failed_count == 0,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "errors": errors
    }
