# Family Christmas Gift Exchange - Web Subsystem

A modern, secure, and mathematically fair family-to-family Christmas gift exchange web application built with Python Flask, SQLite, and Waitress.

## Key Features

- **True Family-to-Family Exchange**: Manages assignments between participating family units (not individual derangements).
- **Mathematical Coverage Guarantees**:
  - **Predictable Rotation**: Deterministic cyclic derangement over all $n-1$ offsets.
  - **Randomized Full-Coverage Cycle**: Per-cycle pseudo-randomly permuted roster and offset order, guaranteeing every family gives to every other family exactly once per complete aligned cycle ($n-1$ years) as long as roster is stable.
  - **Uniform Random Derangement**: Uniformly rejection-sampled random derangements with optional 1–2 year lookback to prevent immediate recipient repeats.
  - **Coverage Limits**: With $n \le 6$, complete coverage is guaranteed within $\le 5$ years. With $n > 6$, complete coverage within 5 years is mathematically impossible (Pigeonhole Principle), requiring at least $n - 1$ years.
  - **Roster Change Detection**: Explicitly tracks roster stability against the cycle anchor year, warning the organizer if families join or leave mid-cycle.
- **Strict Privacy & Security**:
  - High-entropy unpredictable tokens for family private links (no sequential IDs).
  - Families can view only their own assigned recipient and wish list; givers and other families remain secret.
  - Tokens can be instantly revoked and reissued by the organizer on demand.
  - Admin authentication with constant-time password verification (`hmac.compare_digest`), IP-based rate limiting (5 failed attempts per 15-minute window), and session CSRF protection on all mutating actions.
  - No external CDNs, fonts, or tracking assets; strict Content Security Policy, `Cache-Control: no-store`, and `Referrer-Policy: no-referrer`.
- **Admin Workflow**:
  - Separate Generate/Preview, Save Draft, and Confirmed Publication stages.
  - Confirmed publication is locked (cannot be silently reshuffled).
  - One-click "Copy Link" and pre-filled "Mailto" invitation links.
  - Optional batch SMTP email dispatch with explicit confirmation and failure retries.
  - CSV roster import/export and assignment history export.

---

## Architecture & Technology Stack

- **Backend**: Python 3.12+ / Flask 3.x
- **WSGI Production Server**: Waitress 3.x
- **Database**: SQLite 3 with WAL (`journal_mode=WAL`), `busy_timeout=5000`, and `foreign_keys=ON`.
- **Permissions**: Directory created with `0700`, database file created and enforced with `0600`.
- **Process Supervision**: Archimedes Service Manager (`/home/ubuntu/Projects/utils/service_manager.sh`) via systemd unit `app-christmas.service`.
- **Reverse Proxy**: Nginx on Archimedes serving `https://taylorarchibald.com/christmas/`.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `CHRISTMAS_ADMIN_PASSWORD` | **Yes** | *(None)* | Password for organizer authentication. Login is disabled if unset. |
| `CHRISTMAS_SESSION_SECRET` | **Yes** (Prod) | *(None)* | Strong secret key for signing session cookies (minimum 16 chars). |
| `CHRISTMAS_DB_PATH` | No | `workspace/data/christmas.db` | Absolute path to the SQLite database file. |
| `CHRISTMAS_COOKIE_SECURE` | No | `false` | Set to `true` when serving behind HTTPS in production. |
| `CHRISTMAS_PORT` | No | `13046` | TCP port Waitress listens on (bound to `127.0.0.1`). |
| `SMTP_HOST` | No | *(Empty)* | SMTP server hostname (e.g. `smtp.sendgrid.net`). |
| `SMTP_PORT` | No | `587` | SMTP server port (587 for STARTTLS, 465 for SSL). |
| `SMTP_USER` | No | *(Empty)* | SMTP authentication username. |
| `SMTP_PASS` | No | *(Empty)* | SMTP authentication password. |
| `SMTP_FROM` | No | *(Empty)* | From header address (e.g. `gifts@taylorarchibald.com`). |
| `SMTP_USE_TLS` | No | `true` | Whether to initiate STARTTLS on port 587. |

---

## Reverse Proxy Configuration (Nginx on Archimedes)

The application listens on `127.0.0.1:13046` and serves all application routes under the `/christmas` prefix, with `/health` and `/christmas/health` endpoints available for health checks.

```nginx
# Nginx snippet for https://taylorarchibald.com/christmas/
location /christmas/ {
    proxy_pass http://127.0.0.1:13046/christmas/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /christmas;
    proxy_redirect off;

    # Timeouts
    proxy_connect_timeout 10s;
    proxy_read_timeout 30s;
}

# Optional root health check route
location = /health {
    proxy_pass http://127.0.0.1:13046/health;
    proxy_set_header Host $host;
}
```

---

## Service Management & Commands

The Archimedes service manager handles systemd installation, log streaming, and lifecycle management:

```bash
# Check service status
./web/APP.sh status

# Start or restart the service
./web/APP.sh restart

# Stream application logs
./web/APP.sh log

# Manual standalone execution
python3 -m web.app --host 127.0.0.1 --port 13046
```

---

## Deployment Checklist

1. [ ] Set production environment variables in `.env` (or Archimedes environment):
   - `CHRISTMAS_ADMIN_PASSWORD`
   - `CHRISTMAS_SESSION_SECRET`
   - `CHRISTMAS_COOKIE_SECURE=true`
   - `CHRISTMAS_DB_PATH=/home/ubuntu/Projects/christmas_exchange_workspace/data/christmas.db`
2. [ ] Ensure database directory permissions: `chmod 700 data`
3. [ ] Run automated test suite: `pytest -v tests/test_web.py`
4. [ ] Verify Nginx reverse proxy configuration and reload Nginx.
5. [ ] Execute `./web/DEPLOY.sh` or register systemd service via `./web/APP.sh install`.
6. [ ] Verify health endpoints:
   - `curl -I http://127.0.0.1:13046/health` (HTTP 200)
   - `curl -I https://taylorarchibald.com/christmas/health` (HTTP 200)
7. [ ] Log in to Admin Dashboard at `https://taylorarchibald.com/christmas/admin/login`.
8. [ ] Import or enter families via CSV or manual entry.
9. [ ] Generate, review, save, and publish assignments.
