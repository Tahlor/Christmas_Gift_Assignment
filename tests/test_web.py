"""Comprehensive test suite for the Christmas Gift Exchange web application.

Covers:
- Assignment mathematical properties (n=2..8, cyclic coverage, random derangement constraints)
- Aligned cycle coverage vs 5-year Pigeonhole limits
- Roster stability and mid-cycle warning detection
- Security: constant-time auth, session secret validation, IP rate limiting, CSRF fail-closed
- Family private links: token generation, revocation, privacy isolation, wishlist/address editing
- Publication workflow: preview vs save vs publish lock
- CSV import and export
- Health endpoints and reverse proxy route handling
- Database permissions (0700 dir, 0600 file) and SQLite WAL mode
"""

import os
import stat
import tempfile
from pathlib import Path
import pytest
from werkzeug.test import Client

from web import create_app
from web.assignment import (
    AssignmentError,
    AssignmentInfeasibleError,
    analyze_history_coverage,
    batch_simulate_assignments,
    compute_roster_hash,
    generate_synthetic_roster,
    get_cycle_info,
    predictable_rotation,
    randomized_coverage_cycle,
    simulate_assignment,
    uniform_random_derangement,
    validate_mapping_against_history,
)
from web.db import connect_db, init_db
from web.models import (
    calculate_game_scores,
    check_roster_stability,
    create_family,
    delete_manual_history_for_year,
    discard_draft_assignments,
    export_families_to_csv,
    export_history_to_csv,
    get_all_families,
    get_assignments_for_year,
    get_family_ballot,
    get_family_by_id,
    get_family_by_token,
    get_family_recipient_for_year,
    get_family_wishlist,
    get_guess_participation,
    get_historical_years_summary,
    get_history_by_year,
    get_production_seed,
    get_recent_history_disallowed,
    get_seed_fingerprint,
    get_wishlist_as_text,
    has_published_years,
    import_families_from_csv,
    import_manual_history_from_csv,
    is_guesses_revealed,
    is_year_published,
    publish_assignments,
    regenerate_production_seed,
    reveal_guesses_for_year,
    revoke_family_token,
    save_draft_assignments,
    save_manual_history_for_year,
    set_family_wishlist_from_text,
    set_production_seed,
    set_setting,
    submit_guess_ballot,
    toggle_family_active,
    unpublish_assignments,
    update_family,
)
from web.security import (
    check_login_rate_limit,
    record_login_attempt,
    verify_admin_password,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def app(tmp_path):
    """Create test Flask application with temporary SQLite database."""
    db_file = str(tmp_path / "test_data" / "christmas_test.db")
    test_config = {
        "TESTING": True,
        "DB_PATH": db_file,
        "ADMIN_PASSWORD": "ValidTestPassword123!",
        "SECRET_KEY": "a" * 32,
        "WTF_CSRF_ENABLED": False,
    }

    app = create_app(test_config)
    yield app


@pytest.fixture
def client(app):
    """Test client."""
    return app.test_client()


def get_csrf(client) -> str:
    """Helper to extract CSRF token from session."""
    with client.session_transaction() as sess:
        if "_csrf_token" not in sess:
            sess["_csrf_token"] = "test-csrf-token-12345"
        return sess["_csrf_token"]


def login_admin(client, password="ValidTestPassword123!"):
    """Helper to authenticate as admin in test client."""
    csrf = get_csrf(client)
    return client.post(
        "/christmas/admin/login",
        data={"password": password, "csrf_token": csrf},
        follow_redirects=True
    )


# =====================================================================
# 1. Mathematical Assignment Algorithm Tests (n=2..8)
# =====================================================================

class TestAssignmentMath:
    """Test mathematical properties, derangement guarantees, and coverage cycles."""

    @pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8])
    def test_predictable_rotation_properties(self, n):
        """Verify predictable rotation across complete n-1 year cycle."""
        family_ids = list(range(100, 100 + n))
        anchor_year = 2024
        cycle_len = n - 1

        recipients_given = {fid: set() for fid in family_ids}

        for offset in range(cycle_len):
            yr = anchor_year + offset
            assignment = predictable_rotation(family_ids, yr, anchor_year)

            # Property 1: Exactly n pairs
            assert len(assignment) == n

            # Property 2: No self-assignment
            for giver, receiver in assignment.items():
                assert giver != receiver, f"Family {giver} assigned to itself in year {yr}"
                recipients_given[giver].add(receiver)

            # Property 3: Exactly one receiver per giver, exactly one giver per receiver
            assert len(set(assignment.values())) == n
            assert set(assignment.keys()) == set(family_ids)

        # Property 4: Complete coverage guarantee over n-1 year aligned cycle
        for fid in family_ids:
            expected_recipients = set(family_ids) - {fid}
            assert recipients_given[fid] == expected_recipients, (
                f"Family {fid} did not give to every other family over {cycle_len} years"
            )

    @pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8])
    def test_randomized_coverage_cycle_properties(self, n):
        """Verify randomized coverage cycle guarantees complete coverage per aligned cycle."""
        family_ids = list(range(200, 200 + n))
        anchor_year = 2025
        cycle_len = n - 1
        secret = "super-secret-cycle-key-testing"

        recipients_given = {fid: set() for fid in family_ids}

        for offset in range(cycle_len):
            yr = anchor_year + offset
            assignment = randomized_coverage_cycle(family_ids, yr, anchor_year, secret)

            assert len(assignment) == n
            for giver, receiver in assignment.items():
                assert giver != receiver
                recipients_given[giver].add(receiver)

            assert len(set(assignment.values())) == n

        # Assert 100% full coverage over aligned cycle
        for fid in family_ids:
            expected = set(family_ids) - {fid}
            assert recipients_given[fid] == expected

    def test_cycle_info_5_year_boundary(self):
        """Verify n <= 6 covers within 5 years; n > 6 requires at least n - 1 years."""
        # For n = 6: cycle length = 5 <= 5
        info_6 = get_cycle_info(n=6, current_year=2026, anchor_year=2026)
        assert info_6["cycle_length"] == 5
        assert info_6["covers_within_5_years"] is True
        assert "within 5 years" in info_6["explanation"]

        # For n = 7: cycle length = 6 > 5
        info_7 = get_cycle_info(n=7, current_year=2026, anchor_year=2026)
        assert info_7["cycle_length"] == 6
        assert info_7["covers_within_5_years"] is False
        assert "Pigeonhole Principle" in info_7["explanation"]
        assert "at least 6 years" in info_7["explanation"]

    @pytest.mark.parametrize("n", [3, 4, 5, 6, 7])
    def test_uniform_random_derangement_no_repeat(self, n):
        """Verify uniform random derangement respects recent history exclusions."""
        family_ids = list(range(300, 300 + n))

        # Year 1
        assignment_y1 = uniform_random_derangement(family_ids)
        assert len(assignment_y1) == n
        for g, r in assignment_y1.items():
            assert g != r

        # Year 2 with 1-year history disallowed
        history_disallowed = {g: {r} for g, r in assignment_y1.items()}
        assignment_y2 = uniform_random_derangement(family_ids, history=history_disallowed)
        assert len(assignment_y2) == n

        for g, r in assignment_y2.items():
            assert g != r
            assert r != assignment_y1[g], f"Family {g} repeated receiver {r} in consecutive years!"

    def test_assignment_infeasible_refusal(self):
        """When constraints make derangement impossible (e.g. n=2 with repeat disallowance), refuse cleanly."""
        family_ids = [1, 2]
        # In year 1: 1->2, 2->1.
        # If year 2 disallows repeats: 1 cannot give to 2 (disallowed) or 1 (self).
        # Thus no assignment is possible!
        history = {1: {2}, 2: {1}}
        with pytest.raises(AssignmentInfeasibleError):
            uniform_random_derangement(family_ids, history=history, max_attempts=50)

    def test_assignment_error_under_two_families(self):
        """Algorithms must reject rosters with fewer than 2 families."""
        with pytest.raises(AssignmentError):
            predictable_rotation([1], 2026, 2026)
        with pytest.raises(AssignmentError):
            randomized_coverage_cycle([1], 2026, 2026, "secret")
        with pytest.raises(AssignmentError):
            uniform_random_derangement([1])

    def test_roster_hash_and_stability(self):
        """Test roster fingerprinting and change detection."""
        roster_a = [1, 2, 3, 4]
        roster_b = [4, 3, 2, 1]  # same set, different order
        roster_c = [1, 2, 3, 4, 5]  # new family added

        hash_a = compute_roster_hash(roster_a)
        hash_b = compute_roster_hash(roster_b)
        hash_c = compute_roster_hash(roster_c)

        assert hash_a == hash_b, "Hash should be deterministic and order-invariant"
        assert hash_a != hash_c, "Adding a family should change the hash"


# =====================================================================
# 2. Database Layer & Permissions Tests
# =====================================================================

class TestDatabaseLayer:
    """Verify database initialization, permissions (0700 dir, 0600 file), and SQLite WAL."""

    def test_db_permissions_and_wal(self, tmp_path):
        """Ensure parent directory is 0700, db file is 0600, and journal mode is WAL."""
        db_dir = tmp_path / "secure_vault"
        db_file = str(db_dir / "exchange.db")

        init_db(db_file)

        # Check directory mode (0700)
        dir_stat = os.stat(db_dir)
        dir_mode = stat.S_IMODE(dir_stat.st_mode)
        assert dir_mode == 0o700, f"Expected 0700 parent directory, got {oct(dir_mode)}"

        # Check DB file mode (0600)
        file_stat = os.stat(db_file)
        file_mode = stat.S_IMODE(file_stat.st_mode)
        assert file_mode == 0o600, f"Expected 0600 file mode, got {oct(file_mode)}"

        # Check SQLite WAL mode
        conn = connect_db(db_file)
        cur = conn.execute("PRAGMA journal_mode;")
        mode = cur.fetchone()[0]
        assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"
        conn.close()


# =====================================================================
# 3. Security, Authentication & CSRF Tests
# =====================================================================

class TestSecurityAndAuth:
    """Verify constant-time password check, IP rate-limiting, and CSRF protection."""

    def test_verify_admin_password(self, app):
        """Constant-time password verification."""
        with app.app_context():
            assert verify_admin_password("ValidTestPassword123!") is True
            assert verify_admin_password("WrongPassword") is False
            assert verify_admin_password("") is False

    def test_admin_login_success_and_logout(self, client):
        """Successful login creates session; logout terminates it."""
        resp = login_admin(client, "ValidTestPassword123!")
        assert resp.status_code == 200
        assert b"Christmas" in resp.data
        assert b"Dashboard" in resp.data

        # Logout
        csrf = get_csrf(client)
        resp_logout = client.post("/christmas/admin/logout", data={"csrf_token": csrf}, follow_redirects=True)
        assert b"You have been logged out" in resp_logout.data

        # Dashboard now redirects to login
        resp_dash = client.get("/christmas/admin/dashboard", follow_redirects=False)
        assert resp_dash.status_code == 302
        assert "/christmas/admin/login" in resp_dash.headers["Location"]

    def test_admin_login_rate_limiting(self, app, client):
        """5 failed login attempts triggers 429 Too Many Requests."""
        with app.app_context():
            ip = "192.168.1.100"
            csrf = get_csrf(client)

            # Record 5 failures
            for _ in range(5):
                record_login_attempt(ip, success=False)

            assert check_login_rate_limit(ip, max_failures=5, window_minutes=15) is False

            # Test via client with spoofed remote_addr
            resp = client.post(
                "/christmas/admin/login",
                data={"password": "WrongPassword", "csrf_token": csrf},
                environ_overrides={"REMOTE_ADDR": ip}
            )
            assert resp.status_code == 429
            assert b"Too Many Requests" in resp.data

    def test_csrf_fail_closed(self, client):
        """State-modifying requests without valid CSRF must fail with 400."""
        login_admin(client)

        # POST without CSRF token
        resp_no_csrf = client.post(
            "/christmas/admin/families/add",
            data={"name": "Attacker Family"}
        )
        assert resp_no_csrf.status_code == 400
        assert b"CSRF token missing or invalid" in resp_no_csrf.data

        # POST with forged CSRF token
        resp_bad_csrf = client.post(
            "/christmas/admin/families/add",
            data={"name": "Attacker Family", "csrf_token": "invalid-forged-token"}
        )
        assert resp_bad_csrf.status_code == 400
        assert b"CSRF token missing or invalid" in resp_bad_csrf.data

    def test_security_headers(self, client):
        """Responses must carry strict browser security headers."""
        resp = client.get("/christmas/admin/login")
        assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate, private"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")


# =====================================================================
# 4. Family Portal & Privacy Isolation Tests
# =====================================================================

class TestFamilyPortalAndPrivacy:
    """Verify family private token links, privacy isolation, and live wishlist editing."""

    def test_family_portal_access_and_isolation(self, app, client):
        """Families access via token, see only their recipient, cannot see their giver or others."""
        with app.app_context():
            fam_a_id = create_family("The Smiths", "smith@example.com", "123 Maple St")
            fam_b_id = create_family("The Browns", "brown@example.com", "456 Oak Ave")
            fam_c_id = create_family("The Taylors", "taylor@example.com", "789 Pine Rd")

            fam_a = get_family_by_id(fam_a_id)
            fam_b = get_family_by_id(fam_b_id)

            # A gives to B, B gives to C, C gives to A
            save_draft_assignments(2026, {fam_a_id: fam_b_id, fam_b_id: fam_c_id, fam_c_id: fam_a_id})

        # Before publish: empty state displayed, recipient NOT revealed
        resp_before = client.get(f"/christmas/f/{fam_a['access_token']}")
        assert resp_before.status_code == 200
        assert b"The Smiths" in resp_before.data
        assert b"Assignments Coming Soon" in resp_before.data
        assert b"The Browns" not in resp_before.data  # Recipient hidden!

        # Publish assignments
        with app.app_context():
            publish_assignments(2026)

        # After publish: A sees B, but NOT C and NOT who is giving to A
        resp_after = client.get(f"/christmas/f/{fam_a['access_token']}")
        assert resp_after.status_code == 200
        assert b"You are giving a gift to:" in resp_after.data
        assert b"The Browns" in resp_after.data  # Recipient visible
        assert b"456 Oak Ave" in resp_after.data  # Recipient address visible
        assert b"The Taylors" not in resp_after.data  # Other family hidden!

    def test_token_revocation(self, app, client):
        """Revoking a family's link invalidates the old token and generates a working new one."""
        with app.app_context():
            fam_id = create_family("The Johnsons", "johnson@example.com")
            fam = get_all_families()[0]
            old_token = fam["access_token"]

            new_token = revoke_family_token(fam_id)
            assert new_token != old_token

        # Old token now returns 404
        resp_old = client.get(f"/christmas/f/{old_token}")
        assert resp_old.status_code == 404

        # New token succeeds
        resp_new = client.get(f"/christmas/f/{new_token}")
        assert resp_new.status_code == 200
        assert b"The Johnsons" in resp_new.data

    def test_live_wishlist_and_address_update(self, app, client):
        """Family can update wishlist and address, and their secret giver sees it immediately."""
        with app.app_context():
            giver_id = create_family("Giver Family", "giver@example.com")
            receiver_id = create_family("Receiver Family", "receiver@example.com")

            giver_token = get_all_families()[0]["access_token"]
            receiver_token = get_all_families()[1]["access_token"]

            save_draft_assignments(2026, {giver_id: receiver_id, receiver_id: giver_id})
            publish_assignments(2026)

        # Receiver updates wishlist and address
        csrf = get_csrf(client)
        resp_w = client.post(
            f"/christmas/f/{receiver_token}/wishlist",
            data={
                "wishlist_text": "Espresso Machine\nWool Socks (Size L)\nBoard game Wingspan",
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp_w.status_code == 200

        resp_c = client.post(
            f"/christmas/f/{receiver_token}/contact",
            data={
                "mailing_address": "777 Winter Way, North Pole, AK 99705",
                "notes": "Loves dark roast coffee",
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp_c.status_code == 200

        # Giver visits their portal and sees receiver's new wishlist and address live!
        resp_giver = client.get(f"/christmas/f/{giver_token}")
        assert b"Espresso Machine" in resp_giver.data
        assert b"Wool Socks (Size L)" in resp_giver.data
        assert b"777 Winter Way" in resp_giver.data
        assert b"Loves dark roast coffee" in resp_giver.data


# =====================================================================
# 5. Publication Lock & Workflow Tests
# =====================================================================

class TestPublicationWorkflow:
    """Verify preview, save draft, publish lock, and unpublish operations."""

    def test_publication_immutability(self, app, client):
        """Confirmed publication cannot be silently overwritten."""
        with app.app_context():
            f1 = create_family("Fam 1", "1@ex.com")
            f2 = create_family("Fam 2", "2@ex.com")
            save_draft_assignments(2026, {f1: f2, f2: f1})
            publish_assignments(2026)

            assert is_year_published(2026) is True

            # Attempting to save new draft over published year must raise ValueError
            with pytest.raises(ValueError) as exc:
                save_draft_assignments(2026, {f1: f2, f2: f1})
            assert "already published" in str(exc.value)

            # Publication remains immutable, even for an administrator.
            with pytest.raises(ValueError):
                unpublish_assignments(2026)
            assert is_year_published(2026) is True

            with pytest.raises(ValueError):
                save_draft_assignments(2026, {f1: f2, f2: f1})


# =====================================================================
# 6. CSV Import and Export Tests
# =====================================================================

class TestCSVOperations:
    """Verify CSV import and export for families and history."""

    def test_csv_import_and_export(self, app):
        """Import families via CSV with headers and export to CSV."""
        with app.app_context():
            csv_content = """Name,Email,Address,Notes,Members
The Andersons,anderson@test.com,"100 Elm St, Boston, MA",Peanut allergy,"Mark, Lisa"
The Bakers,baker@test.com,"200 Oak St, Chicago, IL",Size M,"Tom, Anna"
"""
            created, updated, errors = import_families_from_csv(csv_content)
            assert created == 2
            assert updated == 0
            assert len(errors) == 0

            families = get_all_families()
            assert len(families) == 2

            # Update existing via CSV
            update_content = """Name,Email,Address,Notes,Members
The Andersons,new_anderson@test.com,"100 Elm St, Boston, MA",Peanut allergy,"Mark, Lisa"
"""
            created, updated, errors = import_families_from_csv(update_content)
            assert created == 0
            assert updated == 1

            # Export families CSV
            exported = export_families_to_csv("https://taylorarchibald.com")
            assert "The Andersons" in exported
            assert "new_anderson@test.com" in exported
            assert "https://taylorarchibald.com/christmas/f/" in exported

    def test_history_csv_export(self, app):
        """Export historical assignments to CSV."""
        with app.app_context():
            f1 = create_family("Fam A", "a@test.com")
            f2 = create_family("Fam B", "b@test.com")
            save_draft_assignments(2025, {f1: f2, f2: f1})
            publish_assignments(2025)

            csv_text = export_history_to_csv()
            assert "Year,Giver Family,Recipient Family,Status,Published At" in csv_text
            assert "2025,Fam A,Fam B,Published" in csv_text


# =====================================================================
# 7. Health Check and Route Prefix Tests
# =====================================================================

class TestHealthAndPrefix:
    """Verify health endpoints and route prefix handling."""

    def test_health_endpoints(self, client):
        """Both /health and /christmas/health return 200 OK."""
        r1 = client.get("/health")
        assert r1.status_code == 200
        assert r1.json == {"app": "christmas_gift_exchange", "status": "ok"}

        r2 = client.get("/christmas/health")
        assert r2.status_code == 200
        assert r2.json == {"app": "christmas_gift_exchange", "status": "ok"}

    def test_root_redirect(self, client):
        """GET / redirects to /christmas/."""
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/christmas/" in resp.headers["Location"]

    def test_static_asset_serving(self, client):
        """Static assets are properly served under /christmas/static."""
        resp_css = client.get("/christmas/static/css/style.css")
        assert resp_css.status_code == 200
        assert b"Family Christmas Gift Exchange" in resp_css.data

        resp_js = client.get("/christmas/static/js/app.js")
        assert resp_js.status_code == 200
        assert b"DOMContentLoaded" in resp_js.data


# =====================================================================
# 8. End-to-End Admin Workflow Tests
# =====================================================================

class TestAdminWorkflowE2E:
    """Verify full end-to-end admin UI interactions."""

    def test_family_crud_via_admin_routes(self, app, client):
        """Admin adds family, edits details, toggles active status, and revokes link."""
        login_admin(client)
        csrf = get_csrf(client)

        # 1. Add family
        resp_add = client.post(
            "/christmas/admin/families/add",
            data={
                "name": "The Clauses",
                "emails": "santa@northpole.com",
                "mailing_address": "1 Candy Cane Lane",
                "notes": "Loves milk and cookies",
                "members": "Santa, Mrs. Claus",
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp_add.status_code == 200
        assert b"The Clauses" in resp_add.data
        assert b"1 Candy Cane Lane" in resp_add.data

        # Find family ID
        with app.app_context():
            families = get_all_families()
            clause = next(f for f in families if f["name"] == "The Clauses")
            fam_id = clause["id"]

        # 2. Edit family
        resp_edit = client.post(
            f"/christmas/admin/families/{fam_id}/edit",
            data={
                "name": "The Clauses (Edited)",
                "emails": "santa.claus@northpole.com",
                "mailing_address": "2 Reindeer Blvd",
                "notes": "Prefers gluten-free cookies",
                "members": "Santa, Mrs. Claus, Rudolph",
                "active": "1",
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp_edit.status_code == 200
        assert b"The Clauses (Edited)" in resp_edit.data
        assert b"2 Reindeer Blvd" in resp_edit.data

        # 3. Toggle active/inactive
        resp_toggle = client.post(
            f"/christmas/admin/families/{fam_id}/toggle-active",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_toggle.status_code == 200
        assert b"deactivated" in resp_toggle.data

        # 4. Revoke token
        resp_revoke = client.post(
            f"/christmas/admin/families/{fam_id}/revoke-token",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_revoke.status_code == 200
        assert b"was revoked and a new secure link generated" in resp_revoke.data

    def test_assignment_workflow_via_admin_routes(self, client):
        """Admin adds families, previews, clears preview, previews again, saves draft, publishes."""
        login_admin(client)
        csrf = get_csrf(client)

        # Add 3 families
        for name in ["Family Alpha", "Family Beta", "Family Gamma"]:
            client.post(
                "/christmas/admin/families/add",
                data={"name": name, "emails": f"{name.lower().replace(' ', '')}@test.com", "csrf_token": csrf},
                follow_redirects=True
            )

        # Generate preview
        resp_prev = client.post(
            "/christmas/admin/assignments/preview",
            data={"assignment_mode": "randomized_cycle", "csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_prev.status_code == 200
        assert b"Generated preview assignments for 3 families" in resp_prev.data
        assert b"Generated Preview (Unsaved)" in resp_prev.data

        # Discard preview
        resp_clear = client.post(
            "/christmas/admin/assignments/clear-preview",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_clear.status_code == 200
        assert b"Assignment preview cleared" in resp_clear.data

        # Preview again
        client.post(
            "/christmas/admin/assignments/preview",
            data={"assignment_mode": "predictable_rotation", "csrf_token": csrf},
            follow_redirects=True
        )

        # Save draft
        resp_save = client.post(
            "/christmas/admin/assignments/save",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_save.status_code == 200
        assert b"Draft assignments for" in resp_save.data
        assert b"successfully saved" in resp_save.data

        # Publish assignments
        resp_pub = client.post(
            "/christmas/admin/assignments/publish",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_pub.status_code == 200
        assert b"PUBLISHED and locked" in resp_pub.data

    def test_settings_update_and_anchor_reset(self, client):
        """Admin updates system settings and resets cycle anchor."""
        login_admin(client)
        csrf = get_csrf(client)

        resp_set = client.post(
            "/christmas/admin/settings/update",
            data={
                "current_year": "2027",
                "cycle_anchor_year": "2027",
                "assignment_mode": "predictable_rotation",
                "no_repeat_years": "2",
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp_set.status_code == 200
        assert b"Settings updated successfully" in resp_set.data

        resp_reset = client.post(
            "/christmas/admin/settings/reset-anchor",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_reset.status_code == 200
        assert b"Cycle anchor has been reset to year 2027" in resp_reset.data


# =====================================================================
# 9. Configuration & Security Secret Validation
# =====================================================================

class TestConfigValidation:
    """Verify production security validation rules."""

    def test_production_secret_key_validation(self, monkeypatch):
        """In production (TESTING=False), weak or missing CHRISTMAS_SESSION_SECRET must raise ValueError."""
        from web.config import Config

        monkeypatch.setattr(Config, "TESTING", False)
        monkeypatch.setattr(Config, "SECRET_KEY", "short")

        with pytest.raises(ValueError) as exc:
            Config.validate_production()
        assert "CHRISTMAS_SESSION_SECRET must be set and strong" in str(exc.value)

        # Valid strong secret
        monkeypatch.setattr(Config, "SECRET_KEY", "very-strong-secure-production-secret-123456789")
        # Should not raise
        Config.validate_production()



def test_sso_login_requires_state_and_verified_session(client):
    # Login state is CSRF-bound; anonymous visitors cannot create admin sessions.
    assert client.get("/christmas/admin/sso").status_code == 400
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-state"
    r = client.get("/christmas/admin/sso?state=test-state")
    assert r.status_code == 302
    assert r.location.startswith("/sso/login?")


# =====================================================================
# 10. V2: Production Seed Lifecycle & Secrecy Tests
# =====================================================================

class TestProductionSeedLifecycle:
    """Verify durable editable production seed, fingerprinting, and lock invariants."""

    def test_seed_durable_and_non_revealing_fingerprint(self, app):
        """Seed is generated once, persists, and only exposes SHA-256 fingerprint."""
        with app.app_context():
            seed1 = get_production_seed()
            assert len(seed1) >= 32
            assert seed1 != "fallback-secret"

            # Consistent on subsequent retrieval
            seed2 = get_production_seed()
            assert seed1 == seed2

            # Fingerprint contains prefix only, never plaintext
            fp = get_seed_fingerprint()
            assert fp.startswith("SHA256:")
            assert seed1 not in fp

    def test_seed_modification_allowed_before_publication(self, app, client):
        """Organizer can set or regenerate seed before any official year is published."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            assert has_published_years() is False

        # Set custom seed
        custom_seed = "my-secure-custom-seed-material-2026"
        resp_set = client.post(
            "/christmas/admin/settings/seed",
            data={"production_seed": custom_seed, "csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_set.status_code == 200
        assert b"Production seed updated" in resp_set.data

        with app.app_context():
            assert get_production_seed() == custom_seed

        # Regenerate random seed
        resp_regen = client.post(
            "/christmas/admin/settings/seed/regenerate",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_regen.status_code == 200
        assert b"New secure production seed generated" in resp_regen.data

        with app.app_context():
            assert get_production_seed() != custom_seed

    def test_seed_permanently_locked_after_first_publication(self, app, client):
        """Once any year is published, seed cannot be replaced or regenerated."""
        with app.app_context():
            f1 = create_family("Fam 1", "1@test.com")
            f2 = create_family("Fam 2", "2@test.com")
            save_draft_assignments(2025, {f1: f2, f2: f1})
            publish_assignments(2025)
            assert has_published_years() is True

            with pytest.raises(ValueError) as exc1:
                set_production_seed("new-seed")
            assert "permanently locked" in str(exc1.value)

            with pytest.raises(ValueError) as exc2:
                regenerate_production_seed()
            assert "permanently locked" in str(exc2.value)

        # Via admin routes
        login_admin(client)
        csrf = get_csrf(client)
        resp = client.post(
            "/christmas/admin/settings/seed",
            data={"production_seed": "attempted-change", "csrf_token": csrf},
            follow_redirects=True
        )
        assert resp.status_code == 200
        assert b"permanently locked" in resp.data


# =====================================================================
# 11. V2: Arbitrary Lookback & Infeasibility Handling
# =====================================================================

class TestArbitraryLookbackAndHistoryConstraints:
    """Verify arbitrary non-negative lookback, hard constraints, and rotation conflict refusal."""

    def test_lookback_0_allows_repeats(self, app):
        """Lookback 0 places no recent-history restrictions on uniform derangements."""
        with app.app_context():
            family_ids = [1, 2, 3]
            # Even if 1 gave to 2 last year, lookback 0 has empty disallowed
            disallowed = get_recent_history_disallowed(family_ids, current_year=2026, lookback_years=0)
            assert all(len(d) == 0 for d in disallowed.values())

    def test_large_lookback_capped_safely(self, app):
        """Defensively accepts large lookback values up to 1000."""
        with app.app_context():
            family_ids = [1, 2, 3]
            disallowed = get_recent_history_disallowed(family_ids, current_year=2026, lookback_years=9999)
            assert isinstance(disallowed, dict)

    def test_uniform_random_infeasible_when_overconstrained(self):
        """Uniform random raises AssignmentInfeasibleError when constraints make derangement impossible."""
        family_ids = [10, 20, 30]
        # Disallow all pairs except self (which is illegal):
        history = {
            10: {20, 30},
            20: {10, 30},
            30: {10, 20}
        }
        with pytest.raises(AssignmentInfeasibleError):
            uniform_random_derangement(family_ids, history=history)

    def test_rotation_mode_conflicts_refused_in_prepare_and_preview(self, app, client):
        """Rotation modes refuse production assignment when mathematical cycle violates lookback."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            f1 = create_family("Alpha", "a@test.com")
            f2 = create_family("Beta", "b@test.com")
            f3 = create_family("Gamma", "c@test.com")

            # Record manual history for 2025: 1->2, 2->3, 3->1
            save_manual_history_for_year(2025, [(f1, f2), (f2, f3), (f3, f1)], confidence="confirmed")

            set_setting("current_year", "2026")
            set_setting("cycle_anchor_year", "2025")

        # Predictable rotation offset for 2026 with anchor 2025 on 3 families:
        # offset k = ((2026 - 2025) % 2) + 1 = 2.
        # In 2025 (k=1): f1->f2, f2->f3, f3->f1.
        # If lookback=0: succeeds.
        # But if lookback=2: check preview refusal if lookback conflicts:
        # Let's test prepare_assignments with conflict:
        with app.app_context():
            # In 2025, f1 gave to f2. If in 2026 predictable rotation gave f1 to f2 again (e.g. anchor set to 2026)
            set_setting("cycle_anchor_year", "2026")
            # With anchor 2026, offset for 2026 is k=1: f1->f2! Which was also in 2025!

        resp_prep = client.post(
            "/christmas/admin/assignments/prepare",
            data={"assignment_mode": "predictable_rotation", "no_repeat_years": "1", "csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_prep.status_code == 200
        assert b"violates the requested 1-year lookback constraint" in resp_prep.data
        assert b"Refusing to create assignments" in resp_prep.data


# =====================================================================
# 12. V2: Manual Past History & CSV Import Tests
# =====================================================================

class TestManualHistoryAndSafeguards:
    """Verify manual past history entry, CSV ingestion, validation, and immutability."""

    def test_manual_history_entry_and_querying(self, app):
        """Organizer records manual past year; properly reflected in history summary."""
        with app.app_context():
            f1 = create_family("Smith", "smith@test.com")
            f2 = create_family("Jones", "jones@test.com")
            f3 = create_family("Davis", "davis@test.com")

            save_manual_history_for_year(
                year=2024,
                pairings=[(f1, f2), (f2, f3), (f3, f1)],
                confidence="confirmed",
                notes="Legacy 2024 exchange"
            )

            hist = get_history_by_year()
            assert 2024 in hist
            assert hist[2024][f1] == f2

            summary = get_historical_years_summary()
            assert any(s["year"] == 2024 and s["provenance"] == "manual" and s["confidence"] == "confirmed" for s in summary)

    def test_manual_history_validation_rejects_self_assignment(self, app):
        """Self assignments in manual history must raise ValueError."""
        with app.app_context():
            f1 = create_family("Fam A", "a@test.com")
            f2 = create_family("Fam B", "b@test.com")

            with pytest.raises(ValueError) as exc:
                save_manual_history_for_year(2024, [(f1, f1), (f2, f2)])
            assert "Self-assignments are strictly forbidden" in str(exc.value)

    def test_manual_history_validation_rejects_duplicate_receiver(self, app):
        """Multiple givers giving to same receiver in same year is rejected."""
        with app.app_context():
            f1 = create_family("Fam A", "a@test.com")
            f2 = create_family("Fam B", "b@test.com")
            f3 = create_family("Fam C", "c@test.com")

            with pytest.raises(ValueError) as exc:
                save_manual_history_for_year(2024, [(f1, f3), (f2, f3)])
            assert "at most one giver" in str(exc.value)

    def test_manual_history_confirmed_vs_partial(self, app):
        """Partial allows incomplete history; Confirmed requires full derangement."""
        with app.app_context():
            f1 = create_family("Fam 1", "1@test.com")
            f2 = create_family("Fam 2", "2@test.com")
            f3 = create_family("Fam 3", "3@test.com")

            # Incomplete pairs with 'confirmed' should fail
            with pytest.raises(ValueError) as exc:
                save_manual_history_for_year(2024, [(f1, f2)], confidence="confirmed")
            assert "Confirmed history requires a complete gift exchange" in str(exc.value)

            # Incomplete pairs with 'partial' succeeds
            save_manual_history_for_year(2024, [(f1, f2)], confidence="partial", notes="Only know 1 pair")
            assert 2024 in get_history_by_year()

    def test_manual_history_cannot_overwrite_official_published_year(self, app):
        """Official published year cannot be overwritten by manual history."""
        with app.app_context():
            f1 = create_family("Alpha", "a@test.com")
            f2 = create_family("Beta", "b@test.com")

            save_draft_assignments(2026, {f1: f2, f2: f1})
            publish_assignments(2026)

            with pytest.raises(ValueError) as exc:
                save_manual_history_for_year(2026, [(f1, f2), (f2, f1)])
            assert "official published exchange" in str(exc.value)

    def test_manual_history_csv_import(self, app):
        """Importing manual history from CSV text correctly matches family names."""
        with app.app_context():
            create_family("Miller Family", "miller@test.com")
            create_family("Wilson Family", "wilson@test.com")

            csv_data = """year,giver,recipient
2024,Miller Family,Wilson Family
2024,Wilson Family,Miller Family
"""
            count, errors = import_manual_history_from_csv(csv_data, confidence="confirmed")
            assert len(errors) == 0
            assert count == 2
            assert 2024 in get_history_by_year()


# =====================================================================
# 13. V2: Sealed Draft Lifecycle & Irreversible Release
# =====================================================================

class TestSealedDraftAndLifecycle:
    """Verify Prepare Official Assignments -> Sealed Draft -> Release to Families."""

    def test_prepare_creates_sealed_draft_hidden_from_family_portal(self, app, client):
        """Prepared assignments are sealed draft, hidden from portal until released."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            f1_id = create_family("House Stark", "stark@winterfell.org")
            f2_id = create_family("House Lannister", "lannister@casterly.rock")
            f1 = get_family_by_id(f1_id)

        # Prepare official assignments
        resp_prep = client.post(
            "/christmas/admin/assignments/prepare",
            data={"assignment_mode": "predictable_rotation", "no_repeat_years": "0", "csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_prep.status_code == 200
        assert b"prepared and sealed as draft" in resp_prep.data

        # Family portal sees empty state, NOT recipient
        resp_portal = client.get(f"/christmas/f/{f1['access_token']}")
        assert resp_portal.status_code == 200
        assert b"Assignments Coming Soon" in resp_portal.data
        assert b"House Lannister" not in resp_portal.data

        # Release to families
        resp_rel = client.post(
            "/christmas/admin/assignments/release",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_rel.status_code == 200
        assert b"PUBLISHED and locked" in resp_rel.data

        # Now visible on portal
        resp_portal_after = client.get(f"/christmas/f/{f1['access_token']}")
        assert resp_portal_after.status_code == 200
        assert b"House Lannister" in resp_portal_after.data

    def test_discard_sealed_draft(self, app, client):
        """Organizer can discard unreleased draft before release."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            f1 = create_family("Fam 1", "1@test.com")
            f2 = create_family("Fam 2", "2@test.com")
            save_draft_assignments(2026, {f1: f2, f2: f1})
            assert len(get_assignments_for_year(2026)) == 2

        resp_disc = client.post(
            "/christmas/admin/assignments/discard-draft",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_disc.status_code == 200
        assert b"discarded" in resp_disc.data

        with app.app_context():
            assert len(get_assignments_for_year(2026)) == 0


# =====================================================================
# 14. V2: Spoiler Protection Gate Tests
# =====================================================================

class TestSpoilerProtectionGate:
    """Verify default spoiler masking, temporary session unlock via POST, and relocking."""

    def test_dashboard_masked_by_default(self, app, client):
        """Dashboard masks recipient names by default when spoilers are locked."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            f1 = create_family("Family Red", "red@test.com")
            f2 = create_family("Family Blue", "blue@test.com")
            save_draft_assignments(2026, {f1: f2, f2: f1})

        # By default: spoilers locked
        resp = client.get("/christmas/admin/dashboard")
        assert resp.status_code == 200
        assert b"Spoiler Protection is ACTIVE" in resp.data
        assert b"Hidden" in resp.data

    def test_spoiler_unlock_and_relock_flow(self, app, client):
        """Unlocking via POST reveals recipient; locking via POST masks them again."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            f1 = create_family("Family Gold", "gold@test.com")
            f2 = create_family("Family Silver", "silver@test.com")
            save_draft_assignments(2026, {f1: f2, f2: f1})

        # Unlock spoilers via POST with CSRF
        resp_unlock = client.post(
            "/christmas/admin/spoilers/unlock",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_unlock.status_code == 200
        assert b"Spoiler protection unlocked" in resp_unlock.data
        assert b"Family Silver" in resp_unlock.data

        # Relock spoilers
        resp_lock = client.post(
            "/christmas/admin/spoilers/lock",
            data={"csrf_token": csrf},
            follow_redirects=True
        )
        assert resp_lock.status_code == 200
        assert b"Spoiler protection enabled" in resp_lock.data


# =====================================================================
# 15. V2: Assignment Test Lab Tests
# =====================================================================

class TestAssignmentTestLab:
    """Verify Test Lab simulation isolation, synthetic rosters, and batch benchmarks."""

    def test_test_lab_does_not_mutate_production_db(self, app, client):
        """Simulations in test lab never create assignments or mutate production seed."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            initial_seed = get_production_seed()
            assert len(get_assignments_for_year(2026)) == 0

        # Run single simulation with arbitrary test seed
        resp = client.post(
            "/christmas/admin/test-lab",
            data={
                "assignment_mode": "randomized_cycle",
                "year": "2026",
                "anchor_year": "2026",
                "test_seed": "arbitrary-user-test-seed-12345",
                "roster_choice": "synthetic_5",
                "no_repeat_years": "1",
                "sim_type": "single",
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp.status_code == 200
        assert b"TEST \xe2\x80\x94 NOT REAL" in resp.data

        # Production DB remains completely untouched
        with app.app_context():
            assert get_production_seed() == initial_seed
            assert len(get_assignments_for_year(2026)) == 0

    def test_test_lab_batch_simulations(self, app, client):
        """Batch 100 simulations benchmark produces frequency spread and fairness summary."""
        login_admin(client)
        csrf = get_csrf(client)

        resp = client.post(
            "/christmas/admin/test-lab",
            data={
                "assignment_mode": "randomized_cycle",
                "year": "2026",
                "anchor_year": "2026",
                "test_seed": "batch-seed-abc",
                "roster_choice": "synthetic_4",
                "no_repeat_years": "0",
                "sim_type": "batch_100",
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp.status_code == 200
        assert b"Batch 100 Simulations Benchmark" in resp.data
        assert b"Successful Runs" in resp.data


# =====================================================================
# 16. V2: Family Guess-the-Giver Game Tests
# =====================================================================

class TestFamilyGuessingGame:
    """Verify ballot tracking, token isolation, secret protection, and answer reveal."""

    def test_guessing_game_flow_and_score_calculation(self, app, client):
        """Families submit ballots; organizer reveals answers; scores are computed correctly."""
        with app.app_context():
            f1_id = create_family("Giver A", "a@test.com")
            f2_id = create_family("Giver B", "b@test.com")
            f3_id = create_family("Giver C", "c@test.com")

            # Official matching: A->B, B->C, C->A
            save_draft_assignments(2026, {f1_id: f2_id, f2_id: f3_id, f3_id: f1_id})
            publish_assignments(2026)

            f1 = get_family_by_id(f1_id)
            f2 = get_family_by_id(f2_id)

        csrf = get_csrf(client)

        # Family A submits guess: thinks B received from A (correct), C received from B (correct), A from C (correct)
        resp_guess = client.post(
            f"/christmas/f/{f1['access_token']}/guess",
            data={
                f"guess_{f2_id}": str(f1_id),
                f"guess_{f3_id}": str(f2_id),
                f"guess_{f1_id}": str(f3_id),
                "csrf_token": csrf
            },
            follow_redirects=True
        )
        assert resp_guess.status_code == 200
        assert b"Your guesses have been saved" in resp_guess.data

        # Check participation from admin
        login_admin(client)
        resp_dash = client.get("/christmas/admin/dashboard")
        assert b"1 of 3 families have submitted ballots" in resp_dash.data

        # Organizer tries to reveal without override - blocked because families B & C pending
        admin_csrf = get_csrf(client)
        resp_blocked = client.post(
            "/christmas/admin/game/reveal",
            data={"csrf_token": admin_csrf},
            follow_redirects=True
        )
        assert resp_blocked.status_code == 200
        assert b"Cannot reveal answers" in resp_blocked.data

        # Organizer reveals answers with explicit emergency override
        resp_reveal = client.post(
            "/christmas/admin/game/reveal",
            data={"csrf_token": admin_csrf, "override_pending": "1"},
            follow_redirects=True
        )
        assert resp_reveal.status_code == 200
        assert b"revealed to all families" in resp_reveal.data

        # Family A visits portal; sees 100% score (3 of 3 correct)
        resp_score = client.get(f"/christmas/f/{f1['access_token']}")
        assert resp_score.status_code == 200
        assert b"3 of 3 Correct" in resp_score.data
        assert b"100.0%" in resp_score.data

        # Subsequent ballot submission is locked
        family_csrf = get_csrf(client)
        resp_locked = client.post(
            f"/christmas/f/{f1['access_token']}/guess",
            data={f"guess_{f2_id}": str(f3_id), "csrf_token": family_csrf},
            follow_redirects=True
        )
        assert b"Guessing is closed" in resp_locked.data

    def test_anonymous_and_invalid_token_protection(self, client):
        """Anonymous requests with invalid tokens return 404; cannot vote or view portal."""
        resp_get = client.get("/christmas/f/invalid-token-12345")
        assert resp_get.status_code == 404

        csrf = get_csrf(client)
        resp_post = client.post(
            "/christmas/f/invalid-token-12345/guess",
            data={"csrf_token": csrf}
        )
        assert resp_post.status_code == 404

    def test_spoilers_cleared_on_logout(self, app, client):
        """Session spoiler unlock is automatically terminated on admin logout."""
        login_admin(client)
        csrf = get_csrf(client)

        with app.app_context():
            f1 = create_family("House Stark", "stark@test.com")
            f2 = create_family("House Tully", "tully@test.com")
            save_draft_assignments(2026, {f1: f2, f2: f1})

        # Unlock spoilers
        client.post("/christmas/admin/spoilers/unlock", data={"csrf_token": csrf}, follow_redirects=True)
        with client.session_transaction() as sess:
            assert sess.get("spoilers_unlocked") is True

        # Logout
        client.post("/christmas/admin/logout", data={"csrf_token": csrf}, follow_redirects=True)
        with client.session_transaction() as sess:
            assert sess.get("spoilers_unlocked") is None


# Security and migration regression tests from release review.

def test_history_export_requires_explicit_spoiler_unlock(client):
    login_admin(client)
    assert client.get("/christmas/admin/history/export-csv").status_code == 403
    csrf = get_csrf(client)
    response = client.post("/christmas/admin/spoilers/unlock", data={"csrf_token": csrf})
    assert response.status_code in (302, 303)
    assert client.get("/christmas/admin/history/export-csv").status_code == 200


def test_partial_manual_history_is_visible_but_not_a_hard_lookback_constraint(app):
    from web.models import save_manual_history_for_year, get_history_by_year, get_recent_history_disallowed
    with app.app_context():
        x = create_family("History Family A", "a@test.invalid")
        y = create_family("History Family B", "b@test.invalid")
        save_manual_history_for_year(2024, [(x, y)], confidence="partial", notes="provisional")
        assert 2024 in get_history_by_year()
        excluded = get_recent_history_disallowed([x, y], 2026, 2000)
        assert excluded[x] == set()
        assert excluded[y] == set()


def test_official_sealed_draft_locks_only_production_seed(app):
    from web.models import set_production_seed, save_draft_assignments
    with app.app_context():
        x = create_family("Seed Family A", "a@test.invalid")
        y = create_family("Seed Family B", "b@test.invalid")
        set_production_seed("original-prod-seed")
        save_draft_assignments(2026, {x: y, y: x})
        with pytest.raises(ValueError):
            set_production_seed("different-prod-seed")


def test_existing_contact_source_table_schema_is_preserved(app):
    from web.db import get_db
    with app.app_context():
        names = {row["name"] for row in get_db().execute("PRAGMA table_info(family_import_sources)")}
        assert {"family_id", "source_sheet_id", "source_family_id", "imported_at"} <= names


def test_manual_confirmed_history_does_not_lock_seed(app):
    from web.models import save_manual_history_for_year, set_production_seed
    with app.app_context():
        x = create_family("Old History A", "a@test.invalid")
        y = create_family("Old History B", "b@test.invalid")
        save_manual_history_for_year(2024, [(x, y), (y, x)], confidence="confirmed")
        set_production_seed("can-still-select-production-seed")
        assert get_recent_history_disallowed([x, y], 2026, 2000)[x] == {y}


def test_v3_per_email_smtp_statuses_and_dashboard_tracking(app, client):
    """Do not report a household fully sent if one of its addresses failed."""
    from web.db import get_db
    from web.models import get_admin_tracking_data
    from datetime import datetime, timezone
    with app.app_context():
        fam = create_family("Two Mailboxes", "first@example.test, second@example.test")
        create_family("Other Household", "third@example.test")
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        with db:
            db.execute(
                "INSERT INTO email_logs (year,family_id,recipient_email,status,error_message,sent_at) VALUES (?,?,?,?,?,?)",
                (2026, fam, "first@example.test", "sent", "", now),
            )
            db.execute(
                "INSERT INTO email_logs (year,family_id,recipient_email,status,error_message,sent_at) VALUES (?,?,?,?,?,?)",
                (2026, fam, "second@example.test", "failed", "SMTP refused", now),
            )
        row = next(x for x in get_admin_tracking_data(2026) if x["family_id"] == fam)
        assert row["invitation_status"] == "partial"
        assert len(row["email_details"]) == 2
        assert "1/2" in row["invitation_label"]
    login_admin(client)
    page = client.get("/christmas/admin/dashboard")
    assert page.status_code == 200
    assert b"SMTP accepted 1/2" in page.data
    assert b"first@example.test" in page.data and b"second@example.test" in page.data
    assert b"Link Opened" in page.data


def test_mobile_v4_selectable_method_explanations_and_compact_layout(client):
    """The organizer includes contextual method help and mobile-specific affordances."""
    login_admin(client)
    for mode in ("test", "official"):
        page = client.get(f"/christmas/admin/dashboard?mode={mode}")
        assert page.status_code == 200
        assert b'data-method-selector' in page.data
        assert b'data-method-explainer' in page.data
        assert b'data-fixed-config' in page.data
        assert b"Avoid repeat recipients" in page.data
        assert b"Give to everyone" in page.data
    javascript = client.get("/christmas/static/js/app.js")
    assert javascript.status_code == 200
    for label in (b"shuffled_rotation", b"fixed_rotation",
                  b"uniform_random", b"randomized_cycle", b"Example:"):
        assert label in javascript.data
    css = client.get("/christmas/static/css/style.css")
    assert b"@media (max-width: 700px)" in css.data
    assert b".ui-family-row" in css.data


def test_mobile_v4_custom_fixed_test_matches_selected_rotation_without_side_effects(app, client):
    """Fixed rotation in Test mode must not ignore the custom steps."""
    with app.app_context():
        ids = [create_family(f"Sample {letter}", f"{letter}@example.test")
               for letter in ("A", "B", "C", "D", "E")]
        before_seed = get_production_seed()
    login_admin(client)
    csrf = get_csrf(client)
    form = {
        "csrf_token": csrf,
        "assignment_mode": "fixed_rotation",
        "fixed_household_order": ",".join(map(str, ids)),
        "fixed_offset_order": "3,1,4,2",
        "year": "2026",
        "anchor_year": "2026",
        "test_seed": "only-for-test",
        "roster_choice": "current",
        "no_repeat_years": "3",
        "coverage_window_k": "4",
        "enforce_sliding_coverage": "1",
        "sim_type": "single",
    }
    response = client.post("/christmas/admin/test-lab", data=form, follow_redirects=True)
    assert response.status_code == 200
    assert b"TEST" in response.data
    assert b"Sample A" in response.data and b"Sample D" in response.data
    with client.session_transaction() as ses:
        # Test results are simulated and never stored as a real draw.
        assert ses["admin_mode"] == "test"
    with app.app_context():
        assert not get_assignments_for_year(2026)
        assert get_production_seed() == before_seed

    form["fixed_offset_order"] = "1,1,1,1"
    rejected = client.post("/christmas/admin/test-lab", data=form, follow_redirects=True)
    assert rejected.status_code == 200
    assert b"Test" in rejected.data
    assert b"error" in rejected.data.lower()
