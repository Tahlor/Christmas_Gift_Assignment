"""Private recipient requests never override the existing assignment guarantees."""

import pytest

from web import create_app
from web.assignment import shuffled_repeating_rotation
from web.db import get_db
from web.models import (
    create_family, get_family_by_id, publish_assignments, save_draft_assignments,
    set_setting,
)
from web.requests import (
    best_random_matching, choose_preferred_rotation_salt, get_priorities,
    has_request, request_choices, requests_are_open, save_priorities, save_request,
    valid_requests,
)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DB_PATH": str(tmp_path / "requests.db"),
        "SECRET_KEY": "test-secret" * 4,
        "ADMIN_PASSWORD": "Password123!safe",
    })
    return app


@pytest.fixture
def roster(app):
    with app.app_context():
        set_setting("current_year", "2026")
        set_setting("no_repeat_years", "1")
        ids = [create_family(name, f"{name.lower()}@example.test")
               for name in ("A", "B", "C", "D", "E")]
        tokens = {fid: get_family_by_id(fid)["access_token"] for fid in ids}
        return ids, tokens


def csrf(client):
    with client.session_transaction() as session:
        session["_csrf_token"] = "abc123"
    return "abc123"


def test_private_form_and_withdrawal(app, roster):
    ids, tokens = roster
    client = app.test_client()
    token = tokens[ids[0]]
    response = client.get(f"/christmas/f/{token}")
    assert response.status_code == 200
    assert b"Request a Family to Give To" in response.data
    assert b"your own household" in response.data

    # Requests are bound to the access token, not a forged giver field.
    response = client.post(
        f"/christmas/f/{token}/request",
        data={"csrf_token": csrf(client), "giver_family_id": ids[1],
              "recipient_id": ids[2]},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Your private request was saved" in response.data
    with app.app_context():
        assert has_request(2026, ids[0])
        assert not has_request(2026, ids[1])
        row = get_db().execute(
            "SELECT giver_family_id, recipient_family_id FROM assignment_requests"
        ).fetchone()
        assert row["giver_family_id"] == ids[0]
        assert row["recipient_family_id"] == ids[2]

    response = client.post(f"/christmas/f/{token}/request",
                           data={"csrf_token": csrf(client), "action": "withdraw"},
                           follow_redirects=True)
    assert b"request was removed" in response.data
    with app.app_context():
        assert not has_request(2026, ids[0])


def test_requests_are_bound_by_historical_exclusions(app, roster):
    ids, tokens = roster
    with app.app_context():
        old_map = {ids[i]: ids[(i + 1) % len(ids)] for i in range(len(ids))}
        save_draft_assignments(2025, old_map)
        publish_assignments(2025)
        eligible, excluded = request_choices(2026, ids[0])
        assert ids[1] not in {o["id"] for o in eligible}
        assert ids[1] in {o["id"] for o in excluded}
        with pytest.raises(ValueError, match="excluded"):
            save_request(2026, ids[0], ids[1])
        save_request(2026, ids[0], ids[2])
        assert valid_requests(2026, ids, {ids[0]: {ids[2]}}) == {}
        assert not valid_requests(2026, ids, {ids[0]: {ids[1]}}).get(ids[1])


def test_priority_optimizer_respects_constraints():
    ids = [1, 2, 3, 4, 5]
    prefs = {1: 3, 2: 3}
    priorities = {1: 2, 2: 1}
    mapping = best_random_matching(ids, {}, prefs, priorities)
    assert mapping[2] == 3
    assert mapping[1] != 3
    assert len(mapping) == len(ids)
    assert len(set(mapping.values())) == len(ids)
    assert all(g != r for g, r in mapping.items())
    # A higher-priority desire never cancels a historical hard exclusion.
    mapping = best_random_matching(ids, {2: {3}}, prefs, priorities)
    assert mapping[2] != 3
    assert mapping[1] == 3


def test_rotation_preference_only_chooses_valid_initial_salt():
    ids = [1, 2, 3, 4, 5]
    salt = choose_preferred_rotation_salt(
        ids, 2026, 2026, "test-seed", {}, 3,
        {1: 4, 2: 3}, {1: 1, 2: 2}, max_candidates=10000,
    )
    assert salt is not None
    all_maps = [shuffled_repeating_rotation(ids, y, 2026, "test-seed", salt)
                for y in range(2026, 2030)]
    assert all_maps[0][1] == 4
    assert all_maps[0][2] == 3
    for giver in ids:
        assert {mapping[giver] for mapping in all_maps} == set(ids) - {giver}


def test_sealing_closes_request_and_priority_updates(app, roster):
    ids, _ = roster
    with app.app_context():
        save_request(2026, ids[0], ids[2])
        save_priorities(2026, {fid: (1 if fid == ids[0] else 0) for fid in ids})
        assert get_priorities(2026)[ids[0]] == 1
        save_draft_assignments(2026, {ids[i]: ids[(i + 1) % len(ids)]
                                     for i in range(len(ids))})
        assert not requests_are_open(2026)
        with pytest.raises(ValueError, match="closed"):
            save_request(2026, ids[0], ids[3])
        with pytest.raises(ValueError, match="locked"):
            save_priorities(2026, {fid: 0 for fid in ids})


def test_invalid_csrf_and_token(app, roster):
    ids, tokens = roster
    client = app.test_client()
    response = client.post(f"/christmas/f/{tokens[ids[0]]}/request",
                           data={"recipient_id": ids[2]})
    assert response.status_code == 400
    response = client.post("/christmas/f/not-a-real-token/request",
                           data={"recipient_id": ids[2], "csrf_token": csrf(client)})
    assert response.status_code == 404


def test_admin_priority_route_does_not_publish_request_content(app, roster):
    ids, tokens = roster
    with app.app_context():
        save_request(2026, ids[0], ids[2])
    client = app.test_client()
    login = client.post("/christmas/admin/login", data={
        "password": "Password123!safe", "csrf_token": csrf(client)
    })
    assert login.status_code == 302
    response = client.get("/christmas/admin")
    assert response.status_code == 200
    assert b"1 submitted" in response.data
    priorities = {f"priority_{fid}": "0" for fid in ids}
    priorities[f"priority_{ids[1]}"] = "1"
    response = client.post("/christmas/admin/requests/priorities",
                           data={**priorities, "csrf_token": csrf(client)})
    assert response.status_code == 302
    with app.app_context():
        assert get_priorities(2026)[ids[1]] == 1
