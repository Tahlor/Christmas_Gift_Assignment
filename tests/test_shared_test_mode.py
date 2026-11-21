"""One official configuration, accurate same-year trials and future projection UX."""
from web.db import get_db
from web.models import (
    create_family, get_production_seed, get_setting, get_assignments_for_year,
    save_manual_history_for_year,
)
from web.requests import save_request
from tests.test_web import app, client, login_admin, get_csrf


def five(app):
    with app.app_context():
        return [create_family("Trial family " + str(i), f"trial{i}@example.test")
                for i in range(5)]


def form(client, **overrides):
    payload = {
        "csrf_token": get_csrf(client),
        "apply_shared_config": "1",
        "assignment_mode": "random_lookahead",
        "no_repeat_years": "1",
        "coverage_window_k": "4",
        "enforce_sliding_coverage": "1",
        "year": "2026",
        "anchor_year": "2026",
        "roster_choice": "current",
        "test_seed": "",
        "sim_type": "trials_100",
    }
    payload.update(overrides)
    return payload


def test_same_fields_rendered_in_both_modes_and_explained(client,app):
    five(app)
    login_admin(client)
    t = client.get("/christmas/admin/dashboard?mode=test").get_data(as_text=True)
    o = client.get("/christmas/admin/dashboard?mode=official").get_data(as_text=True)
    for field in ("assignment_mode", "coverage_window_k", "no_repeat_years",
                  "enforce_sliding_coverage", "fixed_household_order", "fixed_offset_order"):
        assert 'name="' + field + '"' in t
        assert 'name="' + field + '"' in o
    assert 'form="shared-test-form"' in t
    assert "Test 100 possible 2026 draws" in t
    assert "Shared with Test + Official" in o
    assert "Test 100 possible 2026 draws" not in o
    assert "Advanced sandbox" in t
    assert "100 independent" in t


def test_test_submission_saves_shared_rules_but_no_draw_or_seed_change(client,app):
    five(app)
    login_admin(client)
    with app.app_context():
        before = get_production_seed()
    resp = client.post("/christmas/admin/test-lab", data=form(
        client, no_repeat_years="2", coverage_window_k="5"
    ), follow_redirects=True)
    assert resp.status_code == 200
    assert b"100 independent candidate draws for 2026" in resp.data
    assert b"Uses the real roster" in resp.data
    with app.app_context():
        assert get_setting("assignment_mode") == "random_lookahead"
        assert get_setting("no_repeat_years") == "2"
        assert get_setting("coverage_window_k") == "5"
        assert get_setting("enforce_sliding_coverage") == "1"
        assert get_production_seed() == before
        assert not get_assignments_for_year(2026)
        assert get_db().execute("SELECT COUNT(*) FROM email_logs").fetchone()[0] == 0
    official = client.get("/christmas/admin/dashboard?mode=official")
    assert b"2-year no-repeat" in official.data
    assert b"5-year rolling coverage" in official.data


def test_successful_matrix_counts_survive_session_key_conversion(client,app):
    ids = five(app)
    with app.app_context():
        save_request(2026, ids[0], ids[1])
    login_admin(client)
    r = client.post("/christmas/admin/test-lab", data=form(client),follow_redirects=True)
    assert r.status_code == 200
    with client.session_transaction() as sess:
        data = sess["test_sim_data"]
        report = data["batch_result"]
        assert report["successful_runs"] == 100, report["failure_reasons"]
        assert report["infeasible_runs"] == 0
        assert report["distinct_mappings"] >= 1
        assert report["preferences_total"] == 1
        assert report["requests_satisfied"] == 100
        matrix = report["matrix"]
        assert all(isinstance(key,str) for key in matrix)
        assert all(sum(row.values()) == 100 for row in matrix.values())
        assert len(data["family_map"]) == 5
    assert b"Advanced: recipient frequency table" in r.data
    assert b"100 / 100" in r.data
    assert b"Eligible preferences met" in r.data


def test_unchecked_rolling_coverage_shared_and_labeled(client,app):
    five(app)
    login_admin(client)
    data = form(client, assignment_mode="uniform_random")
    data.pop("enforce_sliding_coverage")
    data["enforce_sliding_coverage"] = "0"
    r = client.post("/christmas/admin/test-lab", data=data,follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert get_setting("assignment_mode") == "uniform_random"
        assert get_setting("enforce_sliding_coverage") == "0"
    assert b"coverage target (not guaranteed)" in client.get(
        "/christmas/admin/dashboard?mode=official").data


def test_100_year_projection_is_separate_and_reports_its_scope(client,app):
    five(app)
    login_admin(client)
    r = client.post("/christmas/admin/test-lab", data=form(client,sim_type="batch_100"),
                    follow_redirects=True)
    assert r.status_code == 200
    assert b"100 consecutive simulated years" in r.data
    assert b"not</strong> 100 independent current-year draws" in r.data
    with client.session_transaction() as sess:
        assert sess["test_sim_data"]["sim_type"] == "batch_100"


def test_real_and_test_single_draw_use_same_engine(client,app):
    ids = five(app)
    login_admin(client)
    resp = client.post("/christmas/admin/test-lab",data=form(client,sim_type="single"),
                       follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        sample = {row["giver_id"]:row["receiver_id"]
                  for row in sess["test_sim_data"]["single_result"]["mapping"]}
    official = client.post("/christmas/admin/assignments/prepare",data={
        "csrf_token":get_csrf(client),"assignment_mode":"random_lookahead",
        "no_repeat_years":"1","coverage_window_k":"4",
        "enforce_sliding_coverage":"1",
    },follow_redirects=True)
    assert official.status_code == 200
    with app.app_context():
        actual = {r["giver_id"]:r["receiver_id"] for r in get_assignments_for_year(2026)}
        assert actual == sample
        assert len(actual)==len(ids)
        assert all(not r["published"] for r in get_assignments_for_year(2026))
