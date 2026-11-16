"""Communications workflows: privacy, preview, merge, sends, and history."""

import pytest
from web import create_app
from web.communications import (
    get_preview_family_id, list_templates, render_message, save_template,
    send_campaign,
)
from web.db import get_db
from web.models import (
    create_family, get_family_by_id, save_draft_assignments,
    publish_assignments, set_family_wishlist_from_text, set_setting,
)

@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DB_PATH": str(tmp_path / "communications.db"),
        "SECRET_KEY": "x" * 32,
        "ADMIN_PASSWORD": "Test!Password12345",
        "CHRISTMAS_PUBLIC_BASE_URL": "https://taylorarchibald.com",
    })
    yield app


@pytest.fixture
def families(app):
    with app.app_context():
        set_setting("current_year", "2026")
        ids = [
            create_family("Taylor & Krystalee", "taylor@example.test, other@example.test"),
            create_family("Doug & Amanda", "doug@example.test"),
            create_family("Maria", "maria@example.test"),
        ]
        set_family_wishlist_from_text(ids[1], "Lego\nBooks")
        return ids


def csrf(client):
    with client.session_transaction() as session:
        session["_csrf_token"] = "token-csrf-123"
    return "token-csrf-123"


def login(client):
    result = client.post("/christmas/admin/login", data={
        "password": "Test!Password12345", "csrf_token": csrf(client)
    })
    assert result.status_code == 302


def test_templates_defaults_and_preview_default(app, families):
    with app.app_context():
        from web.communications import ensure_templates
        ensure_templates()
        templates = list_templates()
        assert [t["kind"] for t in templates] == ["welcome", "assignment", "reminder"]
        assert get_preview_family_id([
            get_family_by_id(i) for i in families
        ]) == families[0]


def test_comms_is_admin_only_and_csrf_protected(app, families):
    client = app.test_client()
    response = client.get("/christmas/admin/communications")
    assert response.status_code == 302
    assert "/login" in response.location
    login(client)
    page = client.get("/christmas/admin/communications")
    assert page.status_code == 200
    assert b"Communications" in page.data
    assert b"Year-specific private link" in page.data
    assert b"Previously sent emails" in page.data
    assert b"not configured" in page.data
    assert b"Taylor &amp; Krystalee" in page.data
    result = client.post("/christmas/admin/communications/preview", data={
        "preview_family_id": families[0], "subject": "Hello",
        "body": "test",
    })
    assert result.status_code == 400


def test_before_release_fields_and_preview_are_safe(app, families):
    with app.app_context():
        from web.communications import ensure_templates
        ensure_templates()
        subject, body = render_message(
            2026, families[0], "Christmas {{year}}",
            "Hi {{family_name}}! Request: {{request_url}}"
        )
        assert subject == "Christmas 2026"
        assert "Hi Taylor & Krystalee!" in body
        assert "https://taylorarchibald.com/christmas/f/" in body
        assert "?year=2026" in body
        with pytest.raises(ValueError, match="unavailable"):
            render_message(2026, families[0], "Your assignment", "{{recipient_name}}")
        mapping = {families[0]: families[1], families[1]: families[2], families[2]: families[0]}
        save_draft_assignments(2026, mapping)
        with pytest.raises(ValueError, match="unavailable"):
            render_message(2026, families[0], "Your assignment", "{{recipient_name}}")
        publish_assignments(2026)
        subject, body = render_message(
            2026, families[0], "Gift for {{recipient_name}}",
            "{{assignment_sentence}}\n{{recipient_address}}\n{{recipient_wishlist}}"
        )
        assert subject == "Gift for Doug & Amanda"
        assert "giving a gift to Doug & Amanda" in body
        assert "Lego\nBooks" in body


def test_year_specific_private_link_never_redirects_to_new_year(app, families):
    with app.app_context():
        mapping = {families[0]: families[1], families[1]: families[2], families[2]: families[0]}
        save_draft_assignments(2025, mapping)
        publish_assignments(2025)
        token = get_family_by_id(families[0])["access_token"]
    client = app.test_client()
    old = client.get(f"/christmas/f/{token}?year=2025")
    assert old.status_code == 200
    assert b"2025 Gift Exchange" in old.data
    assert b"Doug" in old.data
    assert b"2026 Assignments Coming Soon" not in old.data
    assert client.get(f"/christmas/f/{token}?year=2027").status_code == 404


def test_preview_json_renders_unsaved_content_without_sending(app, families):
    client = app.test_client()
    login(client)
    response = client.post("/christmas/admin/communications/preview", data={
        "csrf_token": csrf(client),
        "preview_family_id": families[0],
        "subject": "Welcome {{family_name}}",
        "body": "My link: {{private_url}}",
    })
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["subject"] == "Welcome Taylor & Krystalee"
    assert "taylor@example.test" in payload["to"]
    assert "christmas/f/" in payload["body"]
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM communication_messages").fetchone()[0] == 0


def test_mail_merge_blocks_unknown_tokens_and_template_edits(app, families):
    with app.app_context():
        from web.communications import ensure_templates
        ensure_templates()
        with pytest.raises(ValueError, match="Unknown mail-merge"):
            save_template(None, "Nope", "custom", "{{unknown_field}}", "text")
        with pytest.raises(ValueError, match="Invalid merge-field"):
            save_template(None, "Nope", "custom", "Hello", "I forgot {{")
        old = list_templates()[0]
        changed = save_template(old["id"], "Welcome & requests", "welcome", "New subject", "Updated")
        assert changed == old["id"]
        assert list_templates()[0]["subject"] == "New subject"


def test_send_confirmation_and_history_snapshots_and_no_duplicates(app, families, monkeypatch):
    import web.communications as comm
    sent = []
    def fake_send(address, subject, body):
        sent.append((address, subject, body))
    monkeypatch.setattr(comm, "_smtp_send", fake_send)
    app.config["SMTP_HOST"] = "fake-smtp"
    app.config["SMTP_FROM"] = "gift@example.test"
    client = app.test_client()
    login(client)
    with app.app_context():
        comm.ensure_templates()
        assignment = next(t for t in comm.list_templates() if t["kind"] == "assignment")
        welcome = next(t for t in comm.list_templates() if t["kind"] == "welcome")
        with pytest.raises(ValueError, match="unavailable"):
            comm.send_campaign(2026, assignment["id"], [families[0]], "0" * 32)
        assert sent == []
        dispatch_key = "f" * 32
        result = comm.send_campaign(2026, welcome["id"], [families[0]], dispatch_key)
        assert result == {"accepted": 2, "failed": 0}
        assert len(sent) == 2
        assert "Welcome" in sent[0][1] or "welcome" in sent[0][1]
        rows = get_db().execute(
            "SELECT subject, body, status, recipient_email FROM communication_messages ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert all(row["status"] == "accepted" for row in rows)
        assert all("christmas/f/" in row["body"] for row in rows)
        with pytest.raises(ValueError, match="already submitted"):
            comm.send_campaign(2026, welcome["id"], [families[0]], dispatch_key)
        assert len(sent) == 2

    no_confirm = client.post("/christmas/admin/communications/send", data={
        "csrf_token": csrf(client), "template_id": welcome["id"],
        "scope": "all", "dispatch_id": "c" * 32,
    })
    assert no_confirm.status_code == 302
    assert len(sent) == 2

    history = client.get("/christmas/admin/communications")
    assert history.status_code == 200
    assert b"Previously sent emails" in history.data
    assert b"taylor@example.test" in history.data


def test_default_household_setting_is_persistent(app, families):
    client = app.test_client()
    login(client)
    result = client.post("/christmas/admin/communications/preview-family", data={
        "csrf_token": csrf(client), "preview_family_id": families[1],
    })
    assert result.status_code == 302
    with app.app_context():
        from web.communications import get_preview_family_id
        assert get_preview_family_id([
            get_family_by_id(i) for i in families
        ]) == families[1]
