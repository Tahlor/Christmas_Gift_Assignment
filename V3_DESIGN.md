# Christmas Gift Exchange V3 - Organizer Rules and Workflow

## One organizer screen

The main organizer dashboard `/christmas/admin` has one Test / Official switch.

- **Test:** Use any test seed and roster to preview hypothetical matches or run batch checks. Actual assignments, the saved production seed, archived years, and email logs are not changed. Results are marked TEST, NOT REAL.
- **Official:** Use the one saved production seed (display fingerprint, not its secret). Prepare a **sealed** exact matching in the database, with no automatic email or family access; independently **Release** to family portals; independently **Send invitations** via configured SMTP or copy manual invitation links. Official recipient pairings are spoiler-masked until the organizer explicitly unlocks them.
- **Track:** Compact per-household status shows SMTP acceptance/error (not proof of delivery), last private-link open (which may be an automated email scanner), explicit receipt confirmation, wishlist activity/item count, guess ballot submission, and pending mailing-address review. Family secrets are not logged in activity.
- **Reveal:** Wait for a ballot from every active household, including all-"not sure" submissions. Then the organizer explicitly reveals answers to all family portals and locks guessing. There is a separately acknowledged emergency override.

Older `/christmas/admin/test-lab` links lead to the unified dashboard in Test mode. History entry and family/contact administration remain accessible separately.

## Two editable promises

1. **Avoid repeat recipients for L years.** No giver repeats a known recipient from the previous L Christmases; L is a nonnegative integer. Only **confirmed** archived/manual historical facts count as hard constraints.
2. **Give to everyone within every K consecutive years.** Each participating household gives to each of the other n-1 households in *every rolling K-year window*, including crossing cycle boundaries. Can be promised prospectively for a stable roster when K >= n-1, with a periodic full rotation.

For n families, K cannot be less than n-1. A repeating full-coverage rotation has period n-1; no recipient can be excluded for more than n-2 previous years indefinitely. Currently n=5, so the strongest achievable settings are K=4 and L=3. Unfeasible choices are explained and refused. A special explicit opt-in may adjust selected values to the suggested feasible K and L: this does not pretend the original impossible guarantee was met.

Historical missing years and roster changes block retroactive proof of any earlier window; upcoming periodic coverage can still be guaranteed from a known consistent starting point.

## Choice of assignment strategy

- **Shuffled repeating rotation (recommended):** One seed and persistent household order/offset permutation determine an n-1-year full rotation, reused without independently reshuffling at the next block. Exact sliding coverage and cross-boundary non-repeats are guaranteed subject to a stable roster and verified boundary constraints.
- **Custom fixed rotation:** Organizer can set household order and nonzero shift sequence; validates each active household and offset appears exactly once. For a full valid offset permutation, the same prospective guarantees hold.
- **Random each year:** Uniformly samples feasible matchings that satisfy current historical no-repeat restrictions; *cannot* promise a perpetual rolling coverage window.
- **Legacy independently shuffled cycles:** Preserved for compatibility, guarantees coverage within aligned blocks only; *cannot* promise coverage in every sliding K window.

When transitioning from confirmed assignments made under a different system, the deterministic shuffled schedule selects a stable salt by checking not just the first new year, but every future year that still overlaps the selected historical no-repeat window. If none exists, official generation is refused rather than silently waiving history. A previously established official rotation is never silently reseeded.

## Exact permanent archive

Every official giver-recipient pair is stored explicitly for that calendar year. Pairings are never recomputed from a seed as historical truth. At preparation time, archive metadata captures mode, L, K, seed fingerprint, roster fingerprint and rotation configuration; release does not replace this snapshot. An unreleased sealed draft can only be regenerated after an explicit discard. Released years cannot be silently republished or overwritten.

Manual previous-year pairing entry/CSV import distinguishes confirmed and incomplete history. **Current historical archive (November 13, 2026):** the user supplied all five 2025 pairings and they were stored as confirmed. For 2024, the exact original GitHub assignment algorithm with seed 2024 reproduced all five pairings, and archived December 2024 messages independently corroborated Taylor/Krystalee giving to Erik/Emily. The organizer accepted that complete deterministic reconstruction as the actual historical 2024 draw. All five are included as confirmed historical planning facts, while the provenance note explicitly distinguishes the one emailed pairing from the four inferred-by-code matches; no complete Pi3 output or all five sent-message copies were located. The 2024 and 2025 matches repeat two giver-recipient relationships, which must not be erased.

## Family data and address reviews

The six households and their addresses/emails were imported from the **current** connected `Archibald Contact Information (birthdays, addresses, etc.)` Google Sheet, `Family` tab (last updated November 12, 2026), not from the 2024 gift run. Five currently participate; the grandparents household is inactive. Original sheet family IDs are retained in `family_import_sources`.

Doug and Amanda's currently recorded address remains unchanged. Their organizer-only review flags an anticipated move around December 2026 and a December 12, 2026 reconfirmation target before shipment. Source Google Sheet has not been changed.

## Security and operational details

- Administrative pages require Universal SSO or password authentication; all mutations are CSRF-protected. An existing valid shared `universal_sso_token` is verified with the local SSO service and the organizer email allowlist automatically upon entry, so no separate SSO button is necessary while the user remains signed in. Explicit Christmas logout suppresses silent re-login until the user explicitly authenticates again.
- High-entropy private family links grant access only to that household's portal; the organizer does not automatically see official recipients.
- Simulations never send email, alter the production seed, publish assignments, or mutate actual assignment history.
- SMTP acceptance is not proof of delivery. Last-link-open timestamps can include automated security scanners; explicit family confirmation is more meaningful.
- Actual SMTP credentials are **not configured** on the live Christmas app at this writing. Manual invitation links work, but automated email sending needs an operator-configured provider. No real invitations have been sent.
- Database migrations preserve six existing households, contact source mappings, and the current production seed; new tables are additive. The live data is protected by a separate pre-V3 database snapshot.
