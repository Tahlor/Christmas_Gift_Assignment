"""Data access models and operations for the Christmas Gift Exchange."""

import csv
import io
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from web.assignment import compute_roster_hash
from web.db import get_db
from web.security import generate_family_token


# ---------------------------------------------------------
# Settings Operations
# ---------------------------------------------------------

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieve setting value by key."""
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """Store or update setting key-value pair."""
    db = get_db()
    with db:
        db.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value)
        )


def get_all_settings() -> Dict[str, str]:
    """Retrieve all system settings as a dictionary."""
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------
# Family Operations
# ---------------------------------------------------------

def get_all_families(active_only: bool = False) -> List[sqlite3.Row]:
    """Retrieve all families ordered by name."""
    db = get_db()
    if active_only:
        return db.execute(
            "SELECT * FROM families WHERE active = 1 ORDER BY name COLLATE NOCASE ASC"
        ).fetchall()
    return db.execute(
        "SELECT * FROM families ORDER BY name COLLATE NOCASE ASC"
    ).fetchall()


def get_family_by_id(family_id: int) -> Optional[sqlite3.Row]:
    """Retrieve a family by ID."""
    db = get_db()
    return db.execute("SELECT * FROM families WHERE id = ?", (family_id,)).fetchone()


def get_family_by_token(token: str) -> Optional[sqlite3.Row]:
    """Retrieve a family by its unpredictable access token."""
    db = get_db()
    return db.execute("SELECT * FROM families WHERE access_token = ?", (token,)).fetchone()


def create_family(
    name: str,
    emails: str,
    mailing_address: str = "",
    notes: str = "",
    members: str = "",
    active: int = 1
) -> int:
    """Create a new family with a secure access token."""
    db = get_db()
    token = generate_family_token()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        cursor = db.execute(
            """
            INSERT INTO families (
                name, emails, mailing_address, notes, members,
                active, access_token, token_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (name.strip(), emails.strip(), mailing_address.strip(),
             notes.strip(), members.strip(), active, token, now, now)
        )
        return cursor.lastrowid


def update_family(
    family_id: int,
    name: str,
    emails: str,
    mailing_address: str = "",
    notes: str = "",
    members: str = "",
    active: int = 1
):
    """Update family details."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            UPDATE families
            SET name = ?, emails = ?, mailing_address = ?, notes = ?,
                members = ?, active = ?, updated_at = ?
            WHERE id = ?
            """,
            (name.strip(), emails.strip(), mailing_address.strip(),
             notes.strip(), members.strip(), active, now, family_id)
        )


def toggle_family_active(family_id: int) -> int:
    """Toggle family active status between 1 and 0."""
    db = get_db()
    fam = get_family_by_id(family_id)
    if not fam:
        return 0
    new_active = 0 if fam["active"] == 1 else 1
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            "UPDATE families SET active = ?, updated_at = ? WHERE id = ?",
            (new_active, now, family_id)
        )
    return new_active


def revoke_family_token(family_id: int) -> str:
    """Revoke existing family access token and issue a fresh one."""
    db = get_db()
    new_token = generate_family_token()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            UPDATE families
            SET access_token = ?,
                token_version = token_version + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (new_token, now, family_id)
        )
    return new_token


def update_family_contact_and_notes(
    family_id: int,
    mailing_address: str,
    notes: str
):
    """Allow family to update their own address and notes via portal."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            UPDATE families
            SET mailing_address = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (mailing_address.strip(), notes.strip(), now, family_id)
        )


# ---------------------------------------------------------
# Wishlist Operations
# ---------------------------------------------------------

def get_family_wishlist(family_id: int) -> List[sqlite3.Row]:
    """Get ordered list of wishlist items for a family."""
    db = get_db()
    return db.execute(
        """
        SELECT * FROM wishlist_items
        WHERE family_id = ?
        ORDER BY display_order ASC, id ASC
        """,
        (family_id,)
    ).fetchall()


def get_wishlist_as_text(family_id: int) -> str:
    """Retrieve family wishlist formatted as newline-separated text."""
    items = get_family_wishlist(family_id)
    return "\n".join(item["item_text"] for item in items)


def set_family_wishlist_from_text(family_id: int, text: str):
    """Replace family wishlist items from newline-separated text."""
    db = get_db()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute("DELETE FROM wishlist_items WHERE family_id = ?", (family_id,))
        for idx, line in enumerate(lines):
            db.execute(
                """
                INSERT INTO wishlist_items (family_id, item_text, display_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (family_id, line, idx, now, now)
            )


# ---------------------------------------------------------
# Assignments Operations
# ---------------------------------------------------------

def get_assignments_for_year(year: int) -> List[sqlite3.Row]:
    """Retrieve all assignments for a given year with giver and receiver family info."""
    db = get_db()
    return db.execute(
        """
        SELECT
            a.id as assignment_id,
            a.year,
            a.published,
            a.created_at,
            a.published_at,
            g.id as giver_id,
            g.name as giver_name,
            g.emails as giver_emails,
            g.access_token as giver_token,
            g.active as giver_active,
            r.id as receiver_id,
            r.name as receiver_name,
            r.emails as receiver_emails,
            r.mailing_address as receiver_address,
            r.notes as receiver_notes
        FROM assignments a
        JOIN families g ON a.giver_family_id = g.id
        JOIN families r ON a.receiver_family_id = r.id
        WHERE a.year = ?
        ORDER BY g.name COLLATE NOCASE ASC
        """,
        (year,)
    ).fetchall()


def is_year_published(year: int) -> bool:
    """Check if assignments for a given year are published."""
    db = get_db()
    row = db.execute(
        "SELECT published FROM assignments WHERE year = ? LIMIT 1",
        (year,)
    ).fetchone()
    return bool(row and row["published"] == 1)


def save_draft_assignments(year: int, assignment_map: Dict[int, int]):
    """Save draft assignments for a year. Fails if year is already published."""
    if is_year_published(year):
        raise ValueError(f"Year {year} assignments are already published and cannot be modified.")

    active_ids = {f["id"] for f in get_all_families(active_only=True)}
    if (len(active_ids) < 2 or set(assignment_map) != active_ids
            or set(assignment_map.values()) != active_ids
            or any(g == r for g, r in assignment_map.items())):
        raise ValueError("Assignments must be a complete one-to-one mapping of the current active families without self-gifts.")
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute("DELETE FROM assignments WHERE year = ?", (year,))
        for giver_id, receiver_id in assignment_map.items():
            db.execute(
                """
                INSERT INTO assignments (year, giver_family_id, receiver_family_id, published, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (year, giver_id, receiver_id, now)
            )


def publish_assignments(year: int):
    """Publish only a complete current-roster matching; never change an existing publication."""
    if is_year_published(year):
        raise ValueError("This year is already published and locked.")
    db = get_db()
    rows = db.execute("SELECT giver_family_id, receiver_family_id FROM assignments WHERE year=?", (year,)).fetchall()
    active_ids = {f["id"] for f in get_all_families(active_only=True)}
    mapping = {r["giver_family_id"]: r["receiver_family_id"] for r in rows}
    if (len(active_ids) < 2 or len(rows) != len(active_ids) or set(mapping) != active_ids
            or set(mapping.values()) != active_ids or any(g == v for g, v in mapping.items())):
        raise ValueError("Draft roster is stale or invalid. Generate and save a new preview before publishing.")
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute("UPDATE assignments SET published=1, published_at=? WHERE year=?", (now, year))


def unpublish_assignments(year: int):
    """Published results are immutable: never revoke a family's recipient invisibly."""
    raise ValueError("Published assignments cannot be unlocked or reshuffled.")


def get_family_recipient_for_year(family_id: int, year: int) -> Optional[sqlite3.Row]:
    """Retrieve recipient details for a giver family in a specific year if published."""
    db = get_db()
    return db.execute(
        """
        SELECT
            a.published,
            a.year,
            r.id as receiver_id,
            r.name as receiver_name,
            r.mailing_address as receiver_address,
            r.notes as receiver_notes
        FROM assignments a
        JOIN families r ON a.receiver_family_id = r.id
        WHERE a.year = ? AND a.giver_family_id = ?
        """,
        (year, family_id)
    ).fetchone()


def get_history_by_year() -> Dict[int, Dict[int, int]]:
    """Retrieve historical assignments as a nested dictionary {year: {giver_id: receiver_id}}."""
    db = get_db()
    rows = db.execute(
        """
        SELECT year, giver_family_id, receiver_family_id
        FROM assignments
        WHERE published = 1
        ORDER BY year ASC
        """
    ).fetchall()

    history: Dict[int, Dict[int, int]] = {}
    for r in rows:
        yr = r["year"]
        if yr not in history:
            history[yr] = {}
        history[yr][r["giver_family_id"]] = r["receiver_family_id"]
    return history


def get_recent_history_disallowed(
    family_ids: List[int],
    current_year: int,
    lookback_years: int = 1
) -> Dict[int, Set[int]]:
    """Get set of disallowed receivers for each giver based on recent published years."""
    if lookback_years <= 0:
        return {fid: set() for fid in family_ids}

    history = get_history_by_year()
    disallowed: Dict[int, Set[int]] = {fid: set() for fid in family_ids}

    target_years = [
        yr for yr in history.keys()
        if current_year - lookback_years <= yr < current_year
    ]

    for yr in target_years:
        for giver, receiver in history[yr].items():
            if giver in disallowed:
                disallowed[giver].add(receiver)

    return disallowed


# ---------------------------------------------------------
# CSV Import / Export
# ---------------------------------------------------------

def import_families_from_csv(csv_content: str) -> Tuple[int, int, List[str]]:
    """Import families from CSV text.

    Supports headers: name, email/emails, address/mailing_address, notes, members.
    Returns: (created_count, updated_count, error_messages)
    """
    reader = csv.DictReader(io.StringIO(csv_content.strip()))
    if not reader.fieldnames:
        return 0, 0, ["Empty CSV file or missing header row."]

    # Normalize header mapping
    header_map = {}
    for field in reader.fieldnames:
        clean = field.strip().lower().replace(" ", "_")
        if clean in ("name", "family", "family_name"):
            header_map["name"] = field
        elif clean in ("email", "emails", "email_address"):
            header_map["emails"] = field
        elif clean in ("address", "mailing_address", "street_address"):
            header_map["address"] = field
        elif clean in ("notes", "preferences", "note"):
            header_map["notes"] = field
        elif clean in ("members", "family_members", "people"):
            header_map["members"] = field

    if "name" not in header_map:
        return 0, 0, ["CSV must contain a 'name' or 'family_name' column."]

    db = get_db()
    existing_families = {f["name"].strip().lower(): f for f in get_all_families()}

    created = 0
    updated = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):
        name = row.get(header_map["name"], "").strip()
        if not name:
            errors.append(f"Row {row_num}: Name is missing, skipped.")
            continue

        emails = row.get(header_map.get("emails", ""), "").strip()
        address = row.get(header_map.get("address", ""), "").strip()
        notes = row.get(header_map.get("notes", ""), "").strip()
        members = row.get(header_map.get("members", ""), "").strip()

        key = name.lower()
        if key in existing_families:
            fam_id = existing_families[key]["id"]
            # Preserve existing fields if empty in CSV
            curr = existing_families[key]
            update_family(
                family_id=fam_id,
                name=name,
                emails=emails or curr["emails"],
                mailing_address=address or curr["mailing_address"],
                notes=notes or curr["notes"],
                members=members or curr["members"],
                active=curr["active"]
            )
            updated += 1
        else:
            create_family(
                name=name,
                emails=emails,
                mailing_address=address,
                notes=notes,
                members=members,
                active=1
            )
            created += 1

    return created, updated, errors


def export_families_to_csv(base_url: str) -> str:
    """Export all families to a CSV string including invitation link."""
    families = get_all_families()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Emails", "Mailing Address", "Notes", "Members",
        "Active", "Private Invitation Link"
    ])

    for f in families:
        clean_base = base_url.rstrip("/")
        link = f"{clean_base}/christmas/f/{f['access_token']}"
        writer.writerow([
            f["name"],
            f["emails"],
            f["mailing_address"],
            f["notes"],
            f["members"],
            "Yes" if f["active"] == 1 else "No",
            link
        ])

    return output.getvalue()


def export_history_to_csv() -> str:
    """Export complete assignment history to CSV string."""
    db = get_db()
    rows = db.execute(
        """
        SELECT
            a.year,
            g.name as giver_name,
            r.name as receiver_name,
            a.published,
            a.published_at
        FROM assignments a
        JOIN families g ON a.giver_family_id = g.id
        JOIN families r ON a.receiver_family_id = r.id
        ORDER BY a.year DESC, g.name COLLATE NOCASE ASC
        """
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Year", "Giver Family", "Recipient Family", "Status", "Published At"])

    for r in rows:
        writer.writerow([
            r["year"],
            r["giver_name"],
            r["receiver_name"],
            "Published" if r["published"] == 1 else "Draft",
            r["published_at"] or ""
        ])

    return output.getvalue()


# ---------------------------------------------------------
# Roster Stability Check
# ---------------------------------------------------------

def check_roster_stability(current_active_ids: List[int]) -> Tuple[bool, str]:
    """Check whether active family roster has changed since the cycle anchor year.

    Returns: (is_stable, message)
    """
    anchor_hash = get_setting("roster_anchor_hash", "")
    anchor_year = int(get_setting("cycle_anchor_year", "2026"))
    current_hash = compute_roster_hash(current_active_ids)

    if not anchor_hash:
        # Do not anchor an empty or single-family roster during initial setup.
        if len(current_active_ids) < 2:
            return True, "Add at least two families before starting a complete rotation cycle."
        set_setting("roster_anchor_hash", current_hash)
        return True, "Cycle anchor roster initialized."

    if anchor_hash == current_hash:
        return True, "Roster is consistent with cycle anchor."

    return False, (
        f"Notice: The active family roster has changed since cycle anchor {anchor_year}. "
        f"The mathematical full-coverage cycle guarantee has reset. "
        f"To restore coverage guarantees for the new roster, reset the cycle anchor."
    )
