# Christmas Gift Exchange V2 — Architecture & Design Document

## 1. Executive Overview & System Goals

The **Christmas Gift Exchange V2** patch is a major architectural and user experience upgrade to the family-to-family gift exchange platform. The original application provided mathematical derangement models, family wishlist management, and private token links, but suffered from usability and privacy limitations: True production assignments were exposed prematurely on admin dashboards (spoiling the secret Santa for organizers), algorithm simulations could not be safely isolated from live data, past historical exchanges could not be recorded without database surgery, and the assignment lifecycle lacked a clear sealed draft state.

V2 solves these challenges through clean architectural separation, strict cryptographic safeguards, spoiler protection, and a family guessing game:

- **Isolated Test Lab**: A sandbox environment where organizers can test algorithms, simulate multi-year batches, and evaluate synthetic rosters under arbitrary seeds without writing to production state.
- **Durable Production Seed**: Exactly one cryptographic seed powering official cycle assignments, protected by SHA-256 fingerprinting, with immutability enforced once an official draw is prepared.
- **Spoiler-Free Admin Experience**: Official mappings are hidden by default behind a session-bound spoiler gate unlocked only via explicit POST+CSRF confirmation.
- **Explicit Lifecycle (Draft → Released → Sent)**: Official assignments are generated into a sealed draft state, invisible to family portals, before an irreversible release action.
- **Manual Past-History Ingestion**: Web form and CSV import for historical exchanges (including legacy 2024 and pending 2025) with provenance and confidence tracking.
- **Family Guess-the-Giver Game**: An interactive post-release game on private family portals where families submit ballots predicting secret Santas, concluding with an organizer-triggered answer reveal and scoreboard.

---

## 2. Cryptographic Seed Lifecycle & Secrecy Architecture

### 2.1 Durable Production Seed Invariant
Official randomized cycle assignments require a stable cryptographic seed to maintain aligned $(n-1)$-year permutation guarantees. In V2:
- The seed is stored in the `settings` database table under the key `cycle_secret`.
- If absent, a high-entropy 256-bit hexadecimal string (`secrets.token_hex(32)`) is generated once and committed.
- **Elimination of Fallback Defaults**: Hardcoded fallback strings (e.g. `"fallback-secret"`) are completely eliminated. `get_production_seed()` guarantees a valid persisted seed.

### 2.2 Secrecy & Fingerprinting
To prevent shoulder-surfing and accidental leaks:
- The plaintext production seed is **never** rendered in GET responses, admin HTML, logs, or sent email bodies.
- Instead, a deterministic SHA-256 fingerprint is exposed:
  $$\text{Fingerprint} = \text{"SHA256:"} + \text{SHA256}(\text{seed})[0:16]$$
- The organizer verifies seed continuity by checking this fingerprint.

### 2.3 Mutation Rules & Immutability Lock
- **Pre-Publication**: If no official draft or released exchange exists (`has_published_years() == False`), the organizer can explicitly provide a custom seed or generate a new random seed via `POST /christmas/admin/settings/seed` (CSRF protected).
- **Post-Publication Lock**: Once an official exchange has been prepared or released (`SELECT 1 FROM assignments WHERE provenance = 'official'`), the seed is permanently locked. Any attempt to update or regenerate the production seed raises a `ValueError` and is rejected. This prevents breaking mathematical coverage continuity across consecutive years.

---

## 3. Assignment Algorithms & Mathematical Guarantees

The exchange engine operates on three pure, deterministic mathematical models:

### 3.1 Predictable Rotation
Deterministic modular shift over all $n-1$ non-zero offsets:
$$k = ((year - anchor\_year) \pmod{n - 1}) + 1$$
$$\text{receiver\_index} = (giver\_index + k) \pmod n$$
- **Guarantees**: Exactly zero self-assignments; full coverage of all other $n-1$ families over an aligned $(n-1)$-year block.
- **Lookback Refusal**: When historical constraints are specified, the generated mapping is checked against the disallowed recipient set. If a conflict occurs, the system refuses to create assignments and reports the exact conflicting family, rather than silently altering the deterministic shift.

### 3.2 Randomized Full-Coverage Cycle
Pseudorandom derangement keyed by the durable production seed:
- Combines `cycle_secret`, `cycle_index = (year - anchor_year) // (n - 1)`, and sorted family IDs into a SHA-256 digest to derive an isolated PRNG seed for the cycle.
- Shuffles the family sequence and offset list $[1, \dots, n-1]$ once per cycle block.
- **Guarantees**: Unpredictable pairings while maintaining complete $(n-1)$-year coverage for stable rosters.
- **Lookback Validation**: Evaluates the generated permutation against past history within the requested lookback window. If historical pairings conflict, production generation halts with a descriptive error.

### 3.3 Uniform Random Derangement
For ad-hoc years where strict rotation is not required:
- Generates a derangement uniformly at random using permutation rejection sampling.
- **Uniform Exact Fallback**: If rejection sampling fails within 1,500 attempts, the algorithm invokes `_uniform_matching`: a memoized combinatorial bipartite matching counter that computes the exact number of valid completions and samples uniformly across all feasible derangements.
- **Infeasibility Refusal**: If constraints make derangement impossible (e.g. $n=2$ with 1-year lookback, or over-constrained rosters), the algorithm immediately raises `AssignmentInfeasibleError`. It never silently violates constraints.

### 3.4 Arbitrary Lookback Window (nonnegative integer)
- The lookback parameter accepts any nonnegative integer. The application compares it against recorded history rather than iterating every possible missing year.
- Disallowed history targets:
  $$\{yr \in \text{Recorded History} \mid \text{current\_year} - \text{lookback} \le yr < \text{current\_year}\}$$
- Official released assignments and **confirmed** manual historical records are hard lookback constraints by default. Partial or reconstructed evidence is visible in history but does not silently constrain official assignments.

### 3.5 Roster Stability & Anchor Reset
- The active roster fingerprint is tracked via `compute_roster_hash(active_ids)`.
- If families are added or deactivated mid-cycle, `check_roster_stability` flags that the full-coverage guarantee has reset.
- Rotation algorithms refuse execution until the organizer explicitly triggers `POST /christmas/admin/settings/reset-anchor`, rebinding the anchor year to the current active roster.

---

## 4. Test Lab Architecture & Isolation

The Test Lab (`/christmas/admin/test-lab`) is completely decoupled from the production database.

```
+-------------------------------------------------------------+
|                      TEST LAB SANDBOX                       |
|  - User-supplied test seed (arbitrary string)              |
|  - Synthetic roster (4, 5, 6, 8, N) or read-only current    |
|  - Arbitrary nonnegative lookback                             |
|  - Simulation Mode: Single Run vs Batch 100 Runs           |
|                                                             |
|   [ In-Memory Computation Only -- No Database Writes ]      |
|                                                             |
|  Output: Prominent Watermark "TEST - NOT REAL"              |
|          Pairing Frequency Matrix & Uniformity Metrics      |
+-------------------------------------------------------------+
```

### 4.1 Invariants
1. **Zero Database State Mutation**: Running simulations in the Test Lab never inserts or modifies rows in `assignments`, never updates `settings`, never touches `cycle_secret`, and never logs to `email_logs`.
2. **Arbitrary Test Seeds**: Users can input any test seed or random text. This seed is never promoted or written to the production settings table.
3. **Synthetic Rosters**: Supports generating synthetic rosters of sizes 2 through 25 (e.g. "Family Alpine", "Family Balsam", etc.) to explore mathematical edge cases.
4. **Batch Monte Carlo Benchmarking**: Executes 100 sequential simulations, building an $n \times n$ giver-receiver frequency matrix, tracking repeat occurrences, and calculating min/max/average distribution metrics.
5. **Watermark Enforcement**: All test lab outputs are bordered and tagged with prominent warning banners: `TEST LAB SIMULATION — NOT REAL DATA — NEVER SAVED TO PRODUCTION`.
6. **No Conversion Path**: There are no action buttons, hidden form fields, or routes that allow a test simulation to be committed as an official assignment.

---

## 5. Spoiler-Free Admin UX & Session Gate

### 5.1 Privacy Problem
In family exchanges, organizers are also participants. Displaying recipient pairings on the dashboard or history page destroys the surprise for the organizer's own household.

### 5.2 Default Masked State
When an administrator logs in, `session.get("spoilers_unlocked")` defaults to `False`:
- **Dashboard Table**: Displays giver family names, invitation link copy buttons, and email delivery statuses. Recipient family names are replaced with:
  `🔒 Hidden (Spoiler Protection)`
- **History View**: Shows total exchange counts, coverage percentages, and year statuses, while individual giver-receiver pairings remain masked.
- **Family Link Inspection**: Direct "View" buttons to family portals are omitted or gated to prevent accidental viewing.

### 5.3 Session Spoiler Gate
- Unlocking mappings requires an explicit `POST /christmas/admin/spoilers/unlock` with valid CSRF token.
- A confirmation dialog warns: *"Reveal all recipient pairings for this session? This will spoil who your secret Santa is."*
- When unlocked, a high-visibility amber banner alerts the user: `⚠️ Spoiler Protection is UNLOCKED`.
- Organizers can re-lock mappings at any time via `POST /christmas/admin/spoilers/lock`.
- Terminating the session or logging out immediately purges the flag (`session.clear()`).

---

## 6. Official Exchange Lifecycle

Official assignments progress through an explicit three-stage lifecycle:

```
+---------------------------------------------------------------+
| 1. UNASSIGNED                                                 |
|    - No assignments exist for current year                    |
|    - Action: "Prepare Official Assignments"                   |
+-------------------------------+-------------------------------+
                                |
                                v
+---------------------------------------------------------------+
| 2. SEALED DRAFT (published = 0)                               |
|    - Generated using saved durable production seed            |
|    - Stored in assignments table with published = 0           |
|    - Family portals show "Assignments Coming Soon"            |
|    - Admin mappings masked by spoiler gate                    |
|    - Actions: "Release to Families" OR "Discard Sealed Draft" |
+-------------------------------+-------------------------------+
                                |
                                v
+---------------------------------------------------------------+
| 3. RELEASED / PUBLISHED (published = 1)                       |
|    - Explicit, irreversible visibility step                   |
|    - Family portals reveal secret recipient and wishlist      |
|    - Assignments permanently locked against reshuffle/reseed |
|    - Actions: "Send Invitations via SMTP" (Manual Mailto)     |
|               "Family Guess-the-Giver Game" activated         |
+---------------------------------------------------------------+
```

### 6.1 Sealed Draft Invariants
- `prepare_assignments` generates assignments using `get_production_seed()` and writes them with `published = 0`, `provenance = 'official'`, `confidence = 'confirmed'`.
- Family portals accessing `/christmas/f/<token>` during this stage see a friendly empty state: *"Assignments for this year have not been published yet."*
- The organizer can review masked counts or discard the draft via `POST /christmas/admin/assignments/discard-draft`.

### 6.2 Release Invariants
- `release_assignments` commits `published = 1` and records `published_at = now()`.
- Once released, assignments can **never** be unpublished, reshuffled, or discarded (`unpublish_assignments` raises `ValueError`).
- Invitation emails are **never** dispatched automatically upon release. The organizer must review and explicitly click "Send All Invitations via SMTP" (if SMTP configured) or use the copy link / mailto tools.

---

## 7. Manual Past-History Ingestion

### 7.1 Provenance & Confidence
To support legacy records (e.g. 2024 spreadsheet exchanges) and pending 2025 data without fabricating cycle continuity:
- `provenance`: `'official'` (system generated) vs `'manual'` (organizer entered).
- `confidence`:
  - `'confirmed'`: Full derangement certified; every participating family gives and receives.
  - `'partial'`: Incomplete historical records; certified coverage cannot be claimed, but recorded pairs are enforced in lookback restrictions.

### 7.2 Validation & Immutability Rules
1. **Self-Assignment Ban**: Giver and receiver cannot be identical.
2. **Single Giver / Receiver Ban**: No family can give more than once or receive more than once in the same year.
3. **Official Year Protection**: Manual history entry **strictly rejects** overwriting any official published exchange year.
4. **Safe Overwrite Flag**: Modifying existing manual records requires explicit confirmation (`overwrite = 1`).
5. **Zero Notifications**: Historical additions never trigger email notifications.
6. **CSV Ingestion**: Flexible parsing supports columns `year, giver, recipient` or `giver, recipient` (with default year), matching either family names or IDs.

---

## 8. Family Guess-the-Giver Game

### 8.1 Game Flow
1. **Availability**: Appears on the private family portal (`/christmas/f/<token>`) only **after** assignments are released (`published == 1`).
2. **Optional Participation**: Does not obstruct the primary gift assignment or wishlist editor.
3. **Ballot Submission**:
   - Each household can submit a ballot predicting the giver for each receiving family.
   - For every receiver, a dropdown presents all active families plus an explicit `"Not sure / Abstain"` option.
   - Families can update their ballot repeatedly until answer reveal.
   - **No Information Leaks**: Option labels, default states, and error responses contain no indicators of actual assignments.

### 8.2 Security & Isolation
- **Token Isolation**: The ballot is strictly bound to the family ID associated with the URL access token (`voting_family_id = family['id']`). A family link cannot vote on behalf of another household.
- **Anonymous Blocking**: Anonymous visitors without a valid token receive HTTP 404 and cannot view or submit ballots.
- **Admin Secrecy**: The admin dashboard displays participation counts (e.g. *"3 of 5 families have submitted ballots"*), never individual votes or answers prior to reveal.

### 8.3 Irreversible Answer Reveal & Scoring
- After gifts are opened, the organizer clicks **"Reveal Answers & Lock Ballots"** (`POST /christmas/admin/game/reveal`).
- This irreversible action:
  1. Sets `guesses_revealed_{year} = '1'`.
  2. Permanently locks all ballots against further submissions.
  3. Displays actual assignments, correct/incorrect badges, and family scores on each participating portal.
  4. Publishes a friendly family leaderboard with scores and percentages.

---

## 9. Database Schema & Non-Destructive Migrations

All migrations are non-destructive and maintain compatibility with existing SQLite databases.

### 9.1 Schema Definitions
```sql
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    emails TEXT NOT NULL,
    mailing_address TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    members TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    access_token TEXT NOT NULL UNIQUE,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wishlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    item_text TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    giver_family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    receiver_family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    published INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    published_at TEXT,
    provenance TEXT NOT NULL DEFAULT 'official',
    confidence TEXT NOT NULL DEFAULT 'confirmed',
    UNIQUE(year, giver_family_id),
    UNIQUE(year, receiver_family_id)
);

CREATE TABLE IF NOT EXISTS family_import_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    details TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historical_years (
    year INTEGER PRIMARY KEY,
    provenance TEXT NOT NULL DEFAULT 'manual',
    confidence TEXT NOT NULL DEFAULT 'confirmed',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guess_ballots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    voting_family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    receiving_family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    guessed_giver_family_id INTEGER REFERENCES families(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(year, voting_family_id, receiving_family_id)
);

CREATE TABLE IF NOT EXISTS email_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    recipient_email TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT DEFAULT '',
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    attempt_time TEXT NOT NULL,
    success INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_year ON assignments(year);
CREATE INDEX IF NOT EXISTS idx_families_token ON families(access_token);
CREATE INDEX IF NOT EXISTS idx_wishlist_family ON wishlist_items(family_id);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, attempt_time);
CREATE INDEX IF NOT EXISTS idx_guess_ballots_year ON guess_ballots(year, voting_family_id);
```

### 9.2 Non-Destructive Column Migrations
When starting with an existing database, `run_migrations(conn)` inspects table info via `PRAGMA table_info(assignments);` and safely adds `provenance` and `confidence` columns if they are not already present. Existing family access tokens and settings remain intact.

---

## 10. Known Limitations & Operational Guidance

1. **Roster Size vs. Lookback Constraints**:
   - For a roster of size $n$, each giver can receive from at most $n - 1$ distinct families.
   - If `lookback >= n - 1` in Uniform Random mode, no valid derangement exists and the system will correctly raise `AssignmentInfeasibleError`.
   - Organizers with small rosters ($n \le 4$) should keep lookback $\le 1$.
2. **Rotation Infeasibility Refusal**:
   - Rotation modes are mathematically determined by offset formulas. If a previous year's assignment conflicts with the offset formula, the system will refuse generation to uphold the requested lookback constraint. Organizers should reset the cycle anchor or reduce the lookback.
3. **Email Credentials**:
   - The application does not store SMTP passwords in the repository or database. SMTP credentials must be supplied via environment variables (`CHRISTMAS_SMTP_HOST`, `CHRISTMAS_SMTP_PORT`, `CHRISTMAS_SMTP_USER`, `CHRISTMAS_SMTP_PASSWORD`).
4. **Reverse Proxy Configuration**:
   - Reverse proxy deployments under `/christmas/` must forward the `Host`, `X-Real-IP`, and `X-Forwarded-Proto` headers, with `no-store` and `no-referrer` caching policies.


---

## 11. Migration and historical-source notes (November 2026)

- The live contacts database was initialized from the user's connected Archibald Contact Information Google Sheet, grouped by the same stable household codes used in the legacy CLI. Six households were imported, of which five are currently active and the grandparents household is inactive. No real assignments were generated or sent during import.
- The existing `family_import_sources` table has columns `family_id`, `source_sheet_id`, `source_family_id`, and `imported_at`; V2 schema must preserve this layout rather than replace the mappings.
- A private **candidate** for Christmas 2024 has been reconstructed by replaying the historical legacy assignment algorithm with seed year 2024 and the five participating household IDs. One household's result was corroborated against three 2024 assignment emails. This verifies one pairing, **not the entire historical draw**. The candidate is stored outside GitHub, not marked confirmed in the app, and must not appear by default in organizer pages.
- Christmas 2025 assignment records have not been located and should be entered manually when available.
- The original randomized-cycle guarantee applies to **aligned blocks** of `n-1` years only. It is not a guarantee for every sliding window when each new cycle is independently randomized. The fixed predictable rotation does give coverage across every rolling `n-1`-year window for a stable roster.
- The app's source code contains no imported household emails, postal addresses, private invitation tokens, or full historical pairings. These stay in the protected service database or private historical-reconstruction file.
