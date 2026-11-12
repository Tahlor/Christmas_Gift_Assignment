"""Data access models and operations for the Christmas Gift Exchange."""

import csv
import hashlib
import io
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from web.assignment import compute_roster_hash
from web.db import get_db
from web.security import generate_family_token


# ---------------------------------------------------------
# Settings Operations & Production Seed
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


def get_production_seed() -> str:
    """Retrieve durable production seed, generating once securely if absent.

    Guarantees no hardcoded fallback is ever used.
    """
    seed = get_setting("cycle_secret")
    if not seed or not seed.strip():
        seed = secrets.token_hex(32)
        set_setting("cycle_secret", seed)
    return seed.strip()


def get_seed_fingerprint(seed: Optional[str] = None) -> str:
    """Compute non-revealing SHA-256 fingerprint for production seed.

    Exposes only a hash prefix to prevent exposing plaintext seed in GET, logs, or messages.
    """
    if seed is None:
        seed = get_production_seed()
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"SHA256:{h[:16]}"


def has_published_years() -> bool:
    """Check whether an official assignment has already been prepared or released."""
    db = get_db()
    # Lock production seed as soon as an official draw is prepared, not only after release.
    # Manual historical imports never lock the seed.
    row = db.execute("SELECT 1 FROM assignments WHERE provenance = 'official' LIMIT 1").fetchone()
    return bool(row)


def set_production_seed(new_seed: str):
    """Set or replace production seed with explicit safeguards.

    Permanently forbidden if any official assignments have already been published.
    """
    if has_published_years():
        raise ValueError(
            "Production seed is permanently locked because official assignments have already "
            "been published. Rotating the seed would break cycle continuity."
        )
    if not new_seed or not new_seed.strip():
        raise ValueError("Production seed cannot be blank.")
    set_setting("cycle_secret", new_seed.strip())


def regenerate_production_seed() -> str:
    """Generate a fresh random production seed if no published years exist."""
    if has_published_years():
        raise ValueError(
            "Production seed is permanently locked because official assignments have already "
            "been published."
        )
    new_seed = secrets.token_hex(32)
    set_setting("cycle_secret", new_seed)
    return new_seed


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
            COALESCE(a.provenance, 'official') as provenance,
            COALESCE(a.confidence, 'confirmed') as confidence,
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


def is_official_year(year: int) -> bool:
    """Check if a year has official assignments (draft or published)."""
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM assignments WHERE year = ? AND provenance = 'official' LIMIT 1",
        (year,)
    ).fetchone()
    if row:
        return True
    row2 = db.execute(
        "SELECT 1 FROM historical_years WHERE year = ? AND provenance = 'official' LIMIT 1",
        (year,)
    ).fetchone()
    return bool(row2)


def save_draft_assignments(year: int, assignment_map: Dict[int, int]):
    """Save draft assignments for a year as sealed draft. Fails if year is already published."""
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
                INSERT INTO assignments (
                    year, giver_family_id, receiver_family_id, published,
                    created_at, provenance, confidence
                )
                VALUES (?, ?, ?, 0, ?, 'official', 'confirmed')
                """,
                (year, giver_id, receiver_id, now)
            )


def discard_draft_assignments(year: int):
    """Discard unreleased draft assignments for a year."""
    if is_year_published(year):
        raise ValueError("Published assignments are locked and cannot be discarded.")
    db = get_db()
    with db:
        db.execute("DELETE FROM assignments WHERE year = ? AND published = 0", (year,))
        db.execute("DELETE FROM archive_metadata WHERE year = ?", (year,))


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
        db.execute(
            """
            UPDATE assignments
            SET published = 1, published_at = ?, provenance = 'official', confidence = 'confirmed'
            WHERE year = ?
            """,
            (now, year)
        )
        db.execute(
            """
            INSERT INTO historical_years (year, provenance, confidence, notes, created_at, updated_at)
            VALUES (?, 'official', 'confirmed', 'Official exchange publication', ?, ?)
            ON CONFLICT(year) DO UPDATE SET
                provenance = 'official',
                confidence = 'confirmed',
                updated_at = excluded.updated_at
            """,
            (year, now, now)
        )

    # Save immutable archive metadata for the official published exchange
    mode = get_setting("assignment_mode", "shuffled_rotation")
    no_repeat = int(get_setting("no_repeat_years", "1"))
    coverage_k = int(get_setting("coverage_window_k", "4"))
    seed_fp = get_seed_fingerprint()
    roster_sig = compute_roster_hash(list(active_ids))
    # A production prepare step normally saved immutable metadata already.
    # Keep the original snapshot, even if Settings were subsequently edited.
    if get_archive_metadata(year) is None:
        save_archive_metadata(
            year=year,
            assignment_mode=mode,
            no_repeat_years=no_repeat,
            coverage_window=coverage_k,
            seed_fingerprint=seed_fp,
            roster_signature=roster_sig
        )


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

    db = get_db()
    disallowed: Dict[int, Set[int]] = {fid: set() for fid in family_ids}
    rows = db.execute(
        """SELECT giver_family_id, receiver_family_id FROM assignments
           WHERE published = 1 AND confidence = 'confirmed'
             AND year >= ? AND year < ?""",
        (current_year - lookback_years, current_year),
    ).fetchall()
    for row in rows:
        giver, receiver = row['giver_family_id'], row['receiver_family_id']
        if giver in disallowed:
            disallowed[giver].add(receiver)
    return disallowed


# ---------------------------------------------------------
# Manual Past-History Operations
# ---------------------------------------------------------

def save_manual_history_for_year(
    year: int,
    pairings: List[Tuple[int, int]],
    confidence: str = "confirmed",
    notes: str = "",
    overwrite: bool = False
):
    """Save manual historical pairings for a past year.

    Safeguards:
    - Never overwrites an official published exchange year.
    - Requires overwrite=True if manual history already exists for this year.
    - Validates no self-giving and no duplicate receiver/giver.
    - If confidence is 'confirmed', enforces a complete matching (set(givers) == set(receivers)).
    """
    if is_official_year(year) and is_year_published(year):
        raise ValueError(f"Year {year} is an official published exchange and cannot be overwritten with manual history.")

    db = get_db()

    existing = db.execute("SELECT provenance FROM assignments WHERE year = ? LIMIT 1", (year,)).fetchone()
    if existing:
        if existing["provenance"] == "official":
            raise ValueError(f"Year {year} is reserved for official assignments.")
        if not overwrite:
            raise ValueError(f"Manual history already exists for year {year}. Confirm replacement to proceed.")

    if not pairings:
        raise ValueError("No pairings provided for manual history.")

    givers = [g for g, _ in pairings]
    receivers = [r for _, r in pairings]

    # Rule: no self-giving
    for g, r in pairings:
        if g == r:
            raise ValueError("Self-assignments are strictly forbidden.")

    # Rule: no duplicate giver
    if len(set(givers)) != len(givers):
        raise ValueError("Each family can give to at most one recipient in the same year.")

    # Rule: no duplicate receiver
    if len(set(receivers)) != len(receivers):
        raise ValueError("Each recipient family can receive from at most one giver in the same year.")

    confidence = confidence.lower().strip()
    if confidence not in ("confirmed", "partial"):
        confidence = "confirmed"

    if confidence == "confirmed":
        if set(givers) != set(receivers) or len(givers) < 2:
            raise ValueError(
                "Confirmed history requires a complete gift exchange where every participating family "
                "both gives and receives a gift. For incomplete records, select 'Partial' confidence."
            )

    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute("DELETE FROM assignments WHERE year = ?", (year,))
        for giver_id, receiver_id in pairings:
            db.execute(
                """
                INSERT INTO assignments (
                    year, giver_family_id, receiver_family_id, published,
                    created_at, published_at, provenance, confidence
                )
                VALUES (?, ?, ?, 1, ?, ?, 'manual', ?)
                """,
                (year, giver_id, receiver_id, now, now, confidence)
            )

        db.execute(
            """
            INSERT INTO historical_years (year, provenance, confidence, notes, created_at, updated_at)
            VALUES (?, 'manual', ?, ?, ?, ?)
            ON CONFLICT(year) DO UPDATE SET
                provenance = 'manual',
                confidence = excluded.confidence,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (year, confidence, notes.strip(), now, now)
        )


def delete_manual_history_for_year(year: int):
    """Delete manual history for a past year. Fails if year is official."""
    db = get_db()
    row = db.execute("SELECT provenance FROM assignments WHERE year = ? LIMIT 1", (year,)).fetchone()
    if row and row["provenance"] == "official":
        raise ValueError(f"Year {year} is an official exchange and cannot be deleted via manual history.")

    with db:
        db.execute("DELETE FROM assignments WHERE year = ? AND provenance = 'manual'", (year,))
        db.execute("DELETE FROM historical_years WHERE year = ? AND provenance = 'manual'", (year,))


def import_manual_history_from_csv(
    csv_content: str,
    default_year: Optional[int] = None,
    confidence: str = "confirmed",
    notes: str = "",
    overwrite: bool = False
) -> Tuple[int, List[str]]:
    """Import past history from CSV text (headers: year, giver, receiver/recipient).

    Returns: (imported_count, errors)
    """
    reader = csv.reader(io.StringIO(csv_content.strip()))
    rows = [r for r in reader if r and any(cell.strip() for cell in r)]
    if not rows:
        return 0, ["Empty CSV file."]

    all_families = get_all_families()
    name_map = {f["name"].strip().lower(): f["id"] for f in all_families}
    id_set = {f["id"] for f in all_families}

    first_row = [c.strip().lower() for c in rows[0]]
    start_idx = 0
    has_header = any(h in first_row for h in ("year", "giver", "receiver", "recipient"))

    year_idx = None
    giver_idx = None
    receiver_idx = None

    if has_header:
        start_idx = 1
        for idx, col in enumerate(first_row):
            if col in ("year", "exchange_year"):
                year_idx = idx
            elif col in ("giver", "giver_name", "giver_family"):
                giver_idx = idx
            elif col in ("receiver", "recipient", "receiver_name", "recipient_family"):
                receiver_idx = idx

    if giver_idx is None or receiver_idx is None:
        first_len = len(rows[0])
        if first_len >= 3:
            year_idx, giver_idx, receiver_idx = 0, 1, 2
        elif first_len == 2:
            giver_idx, receiver_idx = 0, 1
        else:
            return 0, ["CSV must have at least 2 columns (giver, recipient) or 3 (year, giver, recipient)."]

    def resolve_family(val: str) -> Optional[int]:
        val_clean = val.strip()
        if val_clean.isdigit():
            fid = int(val_clean)
            if fid in id_set:
                return fid
        return name_map.get(val_clean.lower())

    pairings_by_year: Dict[int, List[Tuple[int, int]]] = {}
    errors: List[str] = []

    for line_num, row in enumerate(rows[start_idx:], start=start_idx + 1):
        if not row:
            continue
        row_year = default_year
        if year_idx is not None and year_idx < len(row) and row[year_idx].strip():
            try:
                row_year = int(row[year_idx].strip())
            except ValueError:
                errors.append(f"Line {line_num}: Invalid year '{row[year_idx]}'.")
                continue

        if not row_year:
            errors.append(f"Line {line_num}: No year specified.")
            continue

        if giver_idx >= len(row) or receiver_idx >= len(row):
            errors.append(f"Line {line_num}: Incomplete row.")
            continue

        giver_raw = row[giver_idx].strip()
        receiver_raw = row[receiver_idx].strip()

        giver_id = resolve_family(giver_raw)
        receiver_id = resolve_family(receiver_raw)

        if not giver_id:
            errors.append(f"Line {line_num}: Giver family '{giver_raw}' not recognized.")
            continue
        if not receiver_id:
            errors.append(f"Line {line_num}: Recipient family '{receiver_raw}' not recognized.")
            continue

        if row_year not in pairings_by_year:
            pairings_by_year[row_year] = []
        pairings_by_year[row_year].append((giver_id, receiver_id))

    if errors:
        return 0, errors

    total_saved = 0
    for yr, pairs in pairings_by_year.items():
        try:
            save_manual_history_for_year(
                year=yr,
                pairings=pairs,
                confidence=confidence,
                notes=notes,
                overwrite=overwrite
            )
            total_saved += len(pairs)
        except ValueError as exc:
            errors.append(f"Year {yr}: {exc}")

    return total_saved, errors


def get_historical_years_summary() -> List[Dict[str, object]]:
    """Return overview of all recorded years with provenance, confidence, archive metadata, and pairing count."""
    db = get_db()
    rows = db.execute(
        """
        SELECT
            a.year,
            a.provenance,
            a.confidence,
            COUNT(*) as pairing_count,
            a.published,
            MAX(a.published_at) as published_at,
            h.notes,
            m.assignment_mode,
            m.no_repeat_years,
            m.coverage_window,
            m.seed_fingerprint,
            m.roster_signature,
            m.config_json
        FROM assignments a
        LEFT JOIN historical_years h ON a.year = h.year
        LEFT JOIN archive_metadata m ON a.year = m.year
        WHERE a.published = 1
        GROUP BY a.year, a.provenance, a.confidence
        ORDER BY a.year DESC
        """
    ).fetchall()

    return [
        {
            "year": r["year"],
            "provenance": r["provenance"],
            "confidence": r["confidence"],
            "pairing_count": r["pairing_count"],
            "published": r["published"],
            "published_at": r["published_at"],
            "notes": r["notes"] or "",
            "assignment_mode": r["assignment_mode"] or "",
            "no_repeat_years": r["no_repeat_years"] if r["no_repeat_years"] is not None else "",
            "coverage_window": r["coverage_window"] if r["coverage_window"] is not None else "",
            "seed_fingerprint": r["seed_fingerprint"] or "",
            "roster_signature": r["roster_signature"] or "",
            "config_json": r["config_json"] or "{}"
        }
        for r in rows
    ]


# ---------------------------------------------------------
# Guess-The-Giver Game Operations
# ---------------------------------------------------------

def is_guesses_revealed(year: int) -> bool:
    """Check if answers for the guess-the-giver game have been revealed for a year."""
    return get_setting(f"guesses_revealed_{year}") == "1"


def reveal_guesses_for_year(year: int):
    """Irreversibly reveal answers for the guess-the-giver game and lock all ballots."""
    if is_guesses_revealed(year):
        return
    now = datetime.now(timezone.utc).isoformat()
    set_setting(f"guesses_revealed_{year}", "1")
    set_setting(f"guesses_revealed_at_{year}", now)


def submit_guess_ballot(
    year: int,
    voting_family_id: int,
    guesses: Dict[int, Optional[int]]
):
    """Save or update a family's guess ballot for a year.

    Guarantees:
    - Fails if guesses have already been revealed.
    - Ballot belongs strictly to voting_family_id.
    - Guesses map receiving_family_id -> guessed_giver_family_id (or None if not sure).
    """
    if is_guesses_revealed(year):
        raise ValueError("Guesses are locked. Answers have already been revealed for this year.")

    voting_fam = get_family_by_id(voting_family_id)
    if not voting_fam:
        raise ValueError("Invalid voting family.")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            "DELETE FROM guess_ballots WHERE year = ? AND voting_family_id = ?",
            (year, voting_family_id)
        )
        for receiver_id, guessed_giver_id in guesses.items():
            if guessed_giver_id is not None and guessed_giver_id > 0:
                db.execute(
                    """
                    INSERT INTO guess_ballots (
                        year, voting_family_id, receiving_family_id,
                        guessed_giver_family_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (year, voting_family_id, receiver_id, guessed_giver_id, now, now)
                )

    # Mark guess submitted in family_activity even if all guesses are 'not sure'
    record_family_guess_submission(voting_family_id, year)


def get_family_ballot(year: int, voting_family_id: int) -> Dict[int, int]:
    """Retrieve existing guesses for a specific voting family in a year.

    Returns: {receiving_family_id: guessed_giver_family_id}
    """
    db = get_db()
    rows = db.execute(
        """
        SELECT receiving_family_id, guessed_giver_family_id
        FROM guess_ballots
        WHERE year = ? AND voting_family_id = ?
        """,
        (year, voting_family_id)
    ).fetchall()
    return {r["receiving_family_id"]: r["guessed_giver_family_id"] for r in rows if r["guessed_giver_family_id"]}


def get_guess_participation(year: int) -> Dict[str, object]:
    """Get participation stats for the guess-the-giver game without revealing votes.

    Returns: {"voted_count": int, "total_active": int, "revealed": bool}
    """
    status = get_guess_submission_status(year)
    return {
        "voted_count": status["submitted_count"],
        "total_active": status["total_active"],
        "revealed": status["revealed"]
    }


def calculate_game_scores(year: int) -> Dict[str, object]:
    """Compute scores and scoreboard for the guess-the-giver game.

    Only callable after assignments are published.
    """
    db = get_db()
    assignments = get_assignments_for_year(year)
    actual_giver_map = {a["receiver_id"]: a["giver_id"] for a in assignments}
    actual_giver_names = {a["receiver_id"]: a["giver_name"] for a in assignments}

    all_families = get_all_families()
    family_map = {f["id"]: f["name"] for f in all_families}

    ballot_rows = db.execute(
        """
        SELECT voting_family_id, receiving_family_id, guessed_giver_family_id
        FROM guess_ballots
        WHERE year = ?
        """,
        (year,)
    ).fetchall()

    ballots_by_voter: Dict[int, Dict[int, int]] = {}
    for r in ballot_rows:
        v_id = r["voting_family_id"]
        if v_id not in ballots_by_voter:
            ballots_by_voter[v_id] = {}
        if r["guessed_giver_family_id"]:
            ballots_by_voter[v_id][r["receiving_family_id"]] = r["guessed_giver_family_id"]

    leaderboard = []
    total_receivers = len(actual_giver_map)

    for voter_id, guesses in ballots_by_voter.items():
        correct_count = 0
        breakdown = []
        for receiver_id, actual_giver_id in actual_giver_map.items():
            guessed_giver_id = guesses.get(receiver_id)
            is_correct = (guessed_giver_id == actual_giver_id)
            if is_correct:
                correct_count += 1
            breakdown.append({
                "receiver_id": receiver_id,
                "receiver_name": family_map.get(receiver_id, f"Family #{receiver_id}"),
                "actual_giver_id": actual_giver_id,
                "actual_giver_name": actual_giver_names.get(receiver_id, "Unknown"),
                "guessed_giver_id": guessed_giver_id,
                "guessed_giver_name": family_map.get(guessed_giver_id, "Not sure") if guessed_giver_id else "Not sure",
                "is_correct": is_correct
            })

        score_pct = round((correct_count / total_receivers * 100), 1) if total_receivers > 0 else 0
        leaderboard.append({
            "family_id": voter_id,
            "family_name": family_map.get(voter_id, f"Family #{voter_id}"),
            "correct_count": correct_count,
            "total_receivers": total_receivers,
            "score_pct": score_pct,
            "breakdown": breakdown
        })

    leaderboard.sort(key=lambda x: (x["correct_count"], x["score_pct"]), reverse=True)

    return {
        "year": year,
        "revealed": is_guesses_revealed(year),
        "total_families": len(all_families),
        "total_receivers": total_receivers,
        "leaderboard": leaderboard,
        "actual_assignments": [
            {
                "receiver_id": r_id,
                "receiver_name": family_map.get(r_id, f"Family #{r_id}"),
                "giver_id": g_id,
                "giver_name": actual_giver_names.get(r_id, f"Family #{g_id}")
            }
            for r_id, g_id in actual_giver_map.items()
        ]
    }


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


# ---------------------------------------------------------
# Address Reviews Operations
# ---------------------------------------------------------

def get_address_review(family_id: int) -> Optional[sqlite3.Row]:
    """Retrieve address review record for a specific family."""
    db = get_db()
    return db.execute(
        "SELECT * FROM family_address_reviews WHERE family_id = ?",
        (family_id,)
    ).fetchone()


def get_all_address_reviews() -> List[sqlite3.Row]:
    """Retrieve all address review records."""
    db = get_db()
    return db.execute("SELECT * FROM family_address_reviews").fetchall()


def get_pending_address_reviews() -> List[sqlite3.Row]:
    """Retrieve all address reviews currently marked pending."""
    db = get_db()
    return db.execute(
        """
        SELECT r.*, f.name as family_name
        FROM family_address_reviews r
        JOIN families f ON r.family_id = f.id
        WHERE r.status = 'pending' AND f.active = 1
        ORDER BY r.review_due_date ASC
        """
    ).fetchall()


def mark_address_reviewed(family_id: int):
    """Mark an address review as confirmed reviewed."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            UPDATE family_address_reviews
            SET status = 'reviewed', reviewed_at = ?
            WHERE family_id = ?
            """,
            (now, family_id)
        )


def upsert_address_review(
    family_id: int,
    review_due_date: str = "",
    status: str = "pending",
    admin_note: str = "",
    source_sheet_updated_at: str = ""
):
    """Insert or update address review specification matching live schema."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            INSERT INTO family_address_reviews (
                family_id, review_due_date, status, admin_note,
                source_sheet_updated_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(family_id) DO UPDATE SET
                review_due_date = excluded.review_due_date,
                status = excluded.status,
                admin_note = excluded.admin_note,
                source_sheet_updated_at = excluded.source_sheet_updated_at
            """,
            (family_id, review_due_date, status, admin_note, source_sheet_updated_at, now)
        )


# ---------------------------------------------------------
# Archive Metadata Operations
# ---------------------------------------------------------

def save_archive_metadata(
    year: int,
    assignment_mode: str,
    no_repeat_years: int,
    coverage_window: int,
    seed_fingerprint: str,
    roster_signature: str,
    config_json: str = "{}"
):
    """Persist immutable archive metadata for an official exchange publication."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            INSERT INTO archive_metadata (
                year, assignment_mode, no_repeat_years, coverage_window,
                seed_fingerprint, roster_signature, config_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(year) DO NOTHING
            """,
            (year, assignment_mode, no_repeat_years, coverage_window,
             seed_fingerprint, roster_signature, config_json, now)
        )


def get_archive_metadata(year: int) -> Optional[sqlite3.Row]:
    """Retrieve archive metadata for a specific year."""
    db = get_db()
    return db.execute("SELECT * FROM archive_metadata WHERE year = ?", (year,)).fetchone()


# ---------------------------------------------------------
# Family Activity Operations
# ---------------------------------------------------------

def record_family_visit(family_id: int, year: int):
    """Record family GET visit on their portal link."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            INSERT INTO family_activity (family_id, year, last_visit, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(family_id, year) DO UPDATE SET
                last_visit = excluded.last_visit,
                updated_at = excluded.updated_at
            """,
            (family_id, year, now, now, now)
        )


def record_family_assignment_confirmation(family_id: int, year: int):
    """Record family's explicit 'I received my assignment' confirmation."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            INSERT INTO family_activity (family_id, year, assignment_confirmed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(family_id, year) DO UPDATE SET
                assignment_confirmed_at = excluded.assignment_confirmed_at,
                updated_at = excluded.updated_at
            """,
            (family_id, year, now, now, now)
        )


def record_family_wishlist_update(family_id: int, year: int):
    """Record family wish-list POST update timestamp."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            INSERT INTO family_activity (family_id, year, wishlist_updated_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(family_id, year) DO UPDATE SET
                wishlist_updated_at = excluded.wishlist_updated_at,
                updated_at = excluded.updated_at
            """,
            (family_id, year, now, now, now)
        )


def record_family_guess_submission(family_id: int, year: int):
    """Record family guess ballot POST submission marker (even if all guesses are 'not sure')."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            """
            INSERT INTO family_activity (family_id, year, guess_submitted_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(family_id, year) DO UPDATE SET
                guess_submitted_at = excluded.guess_submitted_at,
                updated_at = excluded.updated_at
            """,
            (family_id, year, now, now, now)
        )


def get_family_activity(family_id: int, year: int) -> Optional[sqlite3.Row]:
    """Retrieve activity row for a specific family and year."""
    db = get_db()
    return db.execute(
        "SELECT * FROM family_activity WHERE family_id = ? AND year = ?",
        (family_id, year)
    ).fetchone()


def get_all_family_activities_for_year(year: int) -> Dict[int, sqlite3.Row]:
    """Retrieve all family activity records for a given year indexed by family_id."""
    db = get_db()
    rows = db.execute("SELECT * FROM family_activity WHERE year = ?", (year,)).fetchall()
    return {r["family_id"]: r for r in rows}


# ---------------------------------------------------------
# Guess Submission & Admin Tracking Aggregation
# ---------------------------------------------------------

def get_guess_submission_status(year: int) -> Dict[str, Any]:
    """Check guess ballot submissions across active households."""
    active_families = get_all_families(active_only=True)
    activities = get_all_family_activities_for_year(year)

    db = get_db()
    ballot_voter_ids = set()
    b_rows = db.execute("SELECT DISTINCT voting_family_id FROM guess_ballots WHERE year = ?", (year,)).fetchall()
    for b in b_rows:
        ballot_voter_ids.add(b["voting_family_id"])

    submitted = []
    pending = []

    for f in active_families:
        fid = f["id"]
        act = activities.get(fid)
        has_submitted = bool((act and act["guess_submitted_at"]) or (fid in ballot_voter_ids))
        if has_submitted:
            submitted.append({
                "id": fid,
                "name": f["name"],
                "submitted_at": act["guess_submitted_at"] if act else None
            })
        else:
            pending.append({
                "id": fid,
                "name": f["name"]
            })

    return {
        "total_active": len(active_families),
        "active_count": len(active_families),
        "submitted_count": len(submitted),
        "pending_count": len(pending),
        "submitted_families": submitted,
        "pending_families": pending,
        "all_submitted": len(pending) == 0,
        "revealed": is_guesses_revealed(year)
    }


def get_admin_tracking_data(year: int, spoilers_unlocked: bool = False) -> List[Dict[str, Any]]:
    """Aggregate engagement, address review, and delivery status for each participating family."""
    db = get_db()
    active_families = get_all_families(active_only=True)
    assignments = get_assignments_for_year(year)
    assignment_map = {a["giver_id"]: a["receiver_name"] for a in assignments}

    activities = get_all_family_activities_for_year(year)
    reviews = {r["family_id"]: r for r in get_all_address_reviews()}

    # Get latest email status per family
    email_logs = db.execute(
        """
        SELECT family_id, recipient_email, status, error_message, sent_at
        FROM email_logs
        WHERE year = ?
        ORDER BY id DESC
        """,
        (year,)
    ).fetchall()
    # Track each email address independently, not simply the last row per household.
    latest_email = {}
    for log in email_logs:
        family_rows = latest_email.setdefault(log["family_id"], {})
        address = log["recipient_email"].strip().lower() or "(missing address)"
        if address not in family_rows:
            family_rows[address] = log

    # Get wishlist items count and last update per family
    wishlist_counts = {}
    wishlist_rows = db.execute(
        """
        SELECT family_id, COUNT(*) as cnt, MAX(updated_at) as last_updated
        FROM wishlist_items
        GROUP BY family_id
        """
    ).fetchall()
    for w in wishlist_rows:
        wishlist_counts[w["family_id"]] = (w["cnt"], w["last_updated"])

    # Ballot submission status
    ballot_voter_ids = set()
    b_rows = db.execute("SELECT DISTINCT voting_family_id FROM guess_ballots WHERE year = ?", (year,)).fetchall()
    for b in b_rows:
        ballot_voter_ids.add(b["voting_family_id"])

    tracking = []
    for f in active_families:
        fid = f["id"]
        act = activities.get(fid)
        rev = reviews.get(fid)

        # Address review status
        addr_status = {
            "is_pending": False,
            "status": "current",
            "due_date": "",
            "admin_note": "",
            "label": "✓ Current"
        }
        if rev:
            is_pending = (rev["status"] == "pending")
            addr_status = {
                "is_pending": is_pending,
                "status": rev["status"],
                "due_date": rev["review_due_date"] or "",
                "admin_note": rev["admin_note"] or "",
                "label": f"⚠️ Review Due ({rev['review_due_date']})" if is_pending else "✓ Reviewed"
            }

        # Per-address SMTP status. SMTP accepted != actual delivery.
        attempts = list(latest_email.get(fid, {}).values())
        accepted = sum(e["status"] in ("success", "sent") for e in attempts)
        failures = sum(e["status"] == "failed" for e in attempts)
        if not attempts:
            inv_status = "not_sent"
            inv_label = "Not sent"
        elif failures == 0 and accepted == len(attempts):
            inv_status = "smtp_accepted"
            inv_label = f"SMTP accepted {accepted}/{len(attempts)}"
        elif accepted > 0:
            inv_status = "partial"
            inv_label = f"SMTP accepted {accepted}/{len(attempts)}; {failures} failed"
        else:
            inv_status = "failed"
            inv_label = f"Failed {failures}/{len(attempts)}"
        email_details = [
            {
                "recipient_email": item["recipient_email"],
                "status": "SMTP accepted" if item["status"] in ("success", "sent") else "Failed",
                "attempted_at": item["sent_at"],
                "error_message": item["error_message"] or "",
            }
            for item in attempts
        ]

        # Wishlist info
        w_cnt, w_updated = wishlist_counts.get(fid, (0, None))
        if act and act["wishlist_updated_at"]:
            w_updated = act["wishlist_updated_at"]

        # Ballot submission
        has_submitted = bool((act and act["guess_submitted_at"]) or (fid in ballot_voter_ids))
        guess_submitted_at = act["guess_submitted_at"] if act else None

        # Recipient name (masked unless spoilers unlocked)
        raw_receiver = assignment_map.get(fid)
        if raw_receiver:
            recipient_display = raw_receiver if spoilers_unlocked else "🔒 Hidden (Spoiler Protection)"
        else:
            recipient_display = "—"

        tracking.append({
            "family_id": fid,
            "name": f["name"],
            "family_name": f["name"],
            "emails": f["emails"],
            "access_token": f["access_token"],
            "address_status": addr_status,
            "address_review_status": addr_status["status"],
            "invitation_status": inv_status,
            "invitation_label": inv_label,
            "email_details": email_details,
            "last_visit": act["last_visit"] if act else None,
            "assignment_confirmed_at": act["assignment_confirmed_at"] if act else None,
            "is_confirmed": bool(act and act["assignment_confirmed_at"]),
            "wishlist_count": w_cnt,
            "wishlist_items_count": w_cnt,
            "wishlist_updated_at": w_updated,
            "has_submitted_guess": has_submitted,
            "guess_submitted_at": guess_submitted_at,
            "recipient_display": recipient_display,
            "recipient_name": raw_receiver,
        })

    return tracking
