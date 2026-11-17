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


## V2: safe testing, sealed assignments, and historical voting

The 2026 V2 adds a separate Test Lab (independent seed and no writes), a single stored production seed, unlimited nonnegative historical lookback, manual-year history import, spoiler-protected organizer views and exports, sealed assignment preparation and separate family release/send actions, and a private family guessing game with organizer-controlled answer reveal.

See [V2_DESIGN.md](../V2_DESIGN.md) for the full UX proposal, mathematical guarantees, migration notes, and historical evidence status. Live family contact information, private links, and actual assignment records remain outside the repository.

## Optional private recipient requests

Before the current year's official assignment is prepared, the organizer can
save one nonbinding preferred recipient **for any active household** from the
**Official dashboard → Recipient requests** section. Each household has a
separate row with rule-filtered eligible recipients, its saved selection, an
optional clear action, and the reasons other families are excluded. The
dashboard also controls request priorities: a smaller positive number ranks
ahead of a larger one, and unranked households have normal priority.

**Family portal access is disabled by default for all but one household:**
the designated test family, which is **exactly the default email-preview
household** chosen in Communications. If the organizer changes that preview
default, test-form access moves with it. On the Official dashboard, the organizer
can choose one of three year-specific modes:
- **Test family only (default):** all other household portals omit the
  request form and its "closed" message, and their request POSTs return HTTP 403.
- **Admin only:** no household sees or can submit a request; the organizer
  retains full control.
- **All participating families:** family portals show the request form and
  may create, replace or withdraw their own request via their private link.

The form enforces the same self-gift and historical no-repeat exclusions as
organizer entries. Visibility changes do not delete saved preferences. All
requests and priority edits are locked as soon as a real draft is sealed;
published assignments and the original CLI remain unchanged.

For each tied priority tier, the solver maximizes the number of satisfied
requests. Once the best possible satisfaction counts for every tier are fixed,
it breaks ties randomly: uniformly across equally good complete draws for
random assignment, and by unbiased reservoir sampling across equally good
eligible candidate schedules for a newly established rotation.

Matching always treats requests as **soft preferences**:
- For a new shuffled repeating rotation with no saved schedule, the system
  searches deterministic candidate salts, enforcing the *entire* historical
  transition boundary before maximizing requests by priority tier. It persists
  the selected salt, so future years retain rolling coverage and no-repeat
  guarantees. A saved rotation is never changed to accommodate requests.
- For a uniform random draw, an exact constrained matching maximizes requests
  lexicographically by priority tier, using only valid one-to-one, non-self,
  non-repeated matches. With no requests, the existing uniform sampler is used.
- Custom/predictable/legacy schedules are not modified: requests can only be
  fulfilled if the existing schedule independently produces the requested pair.

**Privacy scope:** the authenticated organizer dashboard displays saved
requests per family, and the organizer may enter and change requests on each
family's behalf. Other households cannot inspect those choices; the family
portal deliberately does not echo a submitted selection. These are **not
anonymous from the organizer or database administrators**. The app stores
household IDs and preferences privately for eligibility and scoring. The
family private URLs must remain secret. Never promise a request is guaranteed.

The request/priority tables are added via idempotent schema initialization;
deploy using the existing service restart/deployment procedure. No production
roster, seed, draft or sent messages are modified by this source update.
Tests: python -m pytest -q tests (also run automatically in GitHub Actions).

## Communications and private mail merge (2026)

The organizer navigation has a **Communications** tab at
`/christmas/admin/communications`, with reusable Welcome & requests,
Assignment announcement, and Friendly reminder templates. New templates can
also be created and saved without sending them.

- Click or drag a merge-field chip into the subject or body.
- `{{family_name}}`, `{{year}}`, `{{family_members}}`, `{{family_address}}`,
  `{{private_url}}`, `{{year_portal_url}}` and `{{request_url}}` are
  usable before the draw. The year-specific URL retains the household's
  existing high-entropy token and adds `?year=YYYY`; previous-year links
  display only that year's released recipient.
- `{{recipient_name}}`, `{{assignment_sentence}}`, `{{recipient_address}}`,
  `{{recipient_wishlist}}`, `{{recipient_notes}}` are available only
  after official publication. They cannot be previewed or sent from a sealed draft.
- Preview renders the actual subject and body for a selected active household,
  without sending. Default preview family is Taylor's household when present,
  and the organizer can persist a different default.
- Sending requires a saved template, chosen family/all-family scope, configured
  SMTP provider, the explicit `SEND` confirmation, and an idempotent
  dispatch identifier. Nothing is sent automatically on draft creation,
  release, preview or template save.
- Every new outgoing email stores a snapshot of its personalized subject,
  body, destination and SMTP acceptance/error status in private SQLite tables.
  Older invitations remain visible from the original `email_logs` status
  table, though those earlier messages did not retain their bodies.
- Sending the same message again requires a fresh deliberate dispatch.
  An SMTP timeout might occur after the provider accepted a message, so
  failures are not silently retried.
- Historical recipients and sealed assignments remain protected. Private
  URLs are family-scoped rather than independently revoked per year. Do not
  share links publicly or include their raw values in logs.

**Invitation safety:** a saved Welcome template that offers requests cannot
be dispatched to a household whose request form is hidden or after the
requests window closes. The bulk-send preflight rejects the entire batch
before any SMTP messages are sent. Enable **All participating families**
first, or email the designated test family only. Other email templates
continue to work according to their normal release/merge restrictions.

**Deployment / configuration:** SMTP uses the existing `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, and `SMTP_USE_TLS`
environment variables, loaded from the restricted Christmas service
environment. To override the base URL for private links, set
`CHRISTMAS_PUBLIC_BASE_URL` to the trusted HTTPS origin. Never commit
credentials. The new tables migrate additively when the Flask app starts.
Run `python -m pytest -q tests`; GitHub Actions runs this automatically.

No automatic periodic reminders are configured. Reuse a reminder template
manually when needed; future automated cadences should require separate
organizer opt-in and explicit delivery policies.
