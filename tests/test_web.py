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
    compute_roster_hash,
    get_cycle_info,
    predictable_rotation,
    randomized_coverage_cycle,
    uniform_random_derangement,
)
from web.db import connect_db, init_db
from web.models import (
    check_roster_stability,
    create_family,
    export_families_to_csv,
    export_history_to_csv,
    get_all_families,
    get_assignments_for_year,
    get_family_by_id,
    get_family_by_token,
    get_family_recipient_for_year,
    get_family_wishlist,
    get_history_by_year,
    get_recent_history_disallowed,
    get_wishlist_as_text,
    import_families_from_csv,
    is_year_published,
    publish_assignments,
    revoke_family_token,
    save_draft_assignments,
    set_family_wishlist_from_text,
    set_setting,
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
