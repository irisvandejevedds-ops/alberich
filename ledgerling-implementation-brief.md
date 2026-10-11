# Ledgerling: Implementation Brief

*Working title, rename freely. Draft v1 for owner review, 2026-10-09. Written for an implementing model that has not seen the conversation that produced it, so host facts and the owner's intent are stated explicitly.*

---

## 0. Review this section first

### 0.1 Decisions needed from the owner

| # | Decision | Recommended default | Why it matters |
|---|---|---|---|
| 1 | Build order | Credits + notifications + pet **first**, ingestion second | The daily-use loop needs no bank data (it runs on seeded estimates) and it proves the riskiest platform piece (Web Push on the Pixel) early |
| 2 | How unrecognized statement formats become templates | LLM-assisted from a **digit-masked excerpt**, shown to you before it is sent, with a local-only manual mapping mode as the alternative | Statements are the most sensitive data in the system (see 5.1.4) |
| 3 | Partner model | A **label only** (a party in splits), no login in v1; export a settle-up summary | Avoids multi-user auth and a second set of permissions |
| 4 | Default split | 50/50, editable per line, stored in basis points | Learning overrides the default after a few decisions |
| 5 | Currency | CAD only; foreign-currency lines use the CAD amount the bank posted | Avoids FX handling |
| 6 | Budget month | Calendar month, assigned by **transaction date** (not statement period) | Card cycles rarely align with calendar months (see 5.3) |
| 7 | Backup destination | Nightly encrypted backup to somewhere **off this machine** | **Verified: there is no backup tooling and only one disk** (a single NVMe; no restic, borg, rclone, age or rsync installed; the only backup timer is Debian's own package-database one). **Needs an answer: where should backups go** (external USB drive, a second machine over Tailscale, or an encrypted cloud bucket)? |
| 8 | Disk encryption | **Verified: it is not encrypted** (plain ext4 on `nvme0n1p2`, no LUKS devices; the 7.7 GB swap partition is plaintext too) | A stolen laptop would expose every statement and receipt, and OCR/PDF memory can be paged to unencrypted swap. Recommended default: application-level encryption of the files directory and database snapshots in v1 (see section 6), and the owner decides separately whether to reinstall with full-disk encryption |
| 9 | Development data | Build against **digit-masked or synthetic** statements; real ones only ever pass through the running app | Anything the implementing agent reads in-session is processed by Anthropic |
| 10 | Anthropic API for runtime | Use it for masked template drafting and the optional per-receipt "better scan"; monthly spend cap default $5 | Needs a separate API key (not the Claude subscription) |
| 11 | Pet identity | Name, species, look; "calm mode" off by default | Original character art is generated in code |
| 12 | Account inventory | List each account: institution, type, owner (me / partner / joint), statement format (PDF / CSV / OFX), billing-cycle day | Drives template work and the required-statements view |
| 13 | Activity seed list | Starting activities, rough per-occurrence cost for each, and desired credits per month | Cold-start data until real history exists |

### 0.2 What I added or changed relative to the original brief (veto any)

1. **Receipt-to-bank-transaction matching.** A receipt and the bank line it paid are the same spend. Without matching, every receipt double-counts. The bank line is the source of truth for money out; the receipt *explodes* it into line items.
2. **Transfers are not spending.** Credit-card payments, moves between own accounts and settle-up payments to the partner are detected and excluded from spend and budgets.
3. **Statement period is not budget month.** Budgeting uses transaction dates.
4. **Monthly credit audit.** Live credit taps are self-reported. At month close they are reconciled against what the statements actually show, and *statements win*.
5. **Digit-masked template drafting** instead of sending raw statements to an LLM.
6. **Phase order:** credits/notifications/pet before ingestion.
7. **A settle-up balance screen** (who owes whom). The brief implies it; it was not listed.
8. **Tailnet-only exposure.** This app must **not** use Tailscale Funnel.

---

## 1. Product summary

A private household-finance app for one primary user and their partner. It is **retroactive by design**: once a month the user uploads statements and receipts, reviews and splits every line item, closes the month, and plans the next. Day to day, the only interaction is a one-tap "I did a thing" screen that spends **activity credits** (counts, not dollars) and drives customizable push notifications and a virtual pet whose mood reflects financial health.

**Principles**

- Integer cents everywhere. Never floats for money.
- Every automated inference (template, match, split, tag, estimate) is explainable and reviewable. Nothing is silently final.
- Private by default. Data lives on the owner's machine. Any outbound call is opt-in, masked where possible, and logged.
- No shame. The tone is playful; the "evil" pet state is comedic, not punitive.
- Append-only events where possible (activity logs, split decisions) so offline sync is trivial.

---

## 2. Host environment (facts, verify before relying)

- **Machine:** Debian 13 laptop. Intel i3-1215U (8 threads), 7.5 GB RAM with roughly **2.2 GB available** under normal load, no discrete GPU, ~416 GB free disk.
- **Runtimes:** Python 3.13.5 and Node 20 are present; **npm is not installed**. `tesseract-ocr` 5.5 is available via apt but not installed. Prefer a **no-build-step frontend**.
- **Other tenants (do not disturb):** Claude Desktop and agent sessions; an Obsidian web session (Xvnc :5901, websockify :6080, nginx :6081, all loopback) exposed publicly through **Funnel on :443**; Samba (139/445); fail2ban; Tailscale. Choose an app port that is free (suggested 8100, loopback only).
- **Networking:** the wired USB NIC is currently failing and the machine falls back to wifi (a failover watchdog is installed). The app must not assume a stable LAN IP: bind loopback only and reach it via Tailscale.
- **Tailscale:** Serve is enabled on the tailnet; Funnel is also enabled and already used for Obsidian. The phone is node `pixel-9`. The phone must be signed in to Tailscale to **open** the app. Push notifications do **not** need Tailscale.
- **Privilege:** the human user has passwordless sudo, and agent sessions run as that user. Therefore run the app as a dedicated unprivileged system user (`finapp`) with a 0700 data directory, the same pattern used for the existing `vaultweb` user.
- **Permission quirk:** in an earlier session the Claude Code auto-mode classifier blocked `tailscale serve/funnel` and nginx rebinding. Expect to **hand the owner the exposure command** rather than run it yourself.
- **Time zone:** verified `America/Toronto`. Keep the IANA zone in config; all scheduling is zone-aware and DST-safe.
- **Already installed:** `pdftotext` (poppler-utils). **Not installed:** `tesseract`, `rsync`, any backup tool. Ports 8100 and 8443 were free when checked.
- **Storage:** one 477 GB NVMe, plain ext4, **unencrypted**, plus a 7.7 GB plaintext swap partition. No second disk is attached.

---

## 3. Architecture

```
Pixel (Chrome, installed PWA)
   |  HTTPS over tailnet only
   v
tailscale serve :8443  -->  127.0.0.1:8100  FastAPI (finapp-web)
                                   |-- SQLite (WAL)        /var/lib/finapp/db
                                   |-- files               /var/lib/finapp/files   (statements, receipts)
                                   |-- finapp-worker: jobs (parse, OCR) + scheduler (5 min tick)
                                   |
                                   +--> pywebpush --> Google FCM --> Pixel (Chrome can be closed)

Optional outbound (opt-in, audited): Anthropic API
   (a) masked template drafting   (b) per-receipt "better scan"
```

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.13, FastAPI, uvicorn (1 worker) | Already installed; strong PDF/OCR ecosystem |
| DB | SQLite, WAL mode, numbered SQL migrations | Single-user, single-box; trivial backup |
| Jobs | SQLite-backed queue, one worker process | No Redis; serial OCR protects scarce RAM |
| Frontend | Server-rendered templates + htmx + a little vanilla JS; hand-written service worker | No npm needed; PWA works fine without a bundler |
| PDF | `pdfplumber` (pdfminer), poppler-utils for fallback | Positional text extraction for table templates |
| OCR | `tesseract-ocr` + `pytesseract`, languages `eng` **and `fra`** (Canadian receipts) | Local, free |
| Push | `pywebpush` with VAPID keys | Standard Web Push, no vendor SDK |
| LLM | `anthropic` SDK; confirm current model IDs with the claude-api skill at build time | Optional features only |

**Process model:** two systemd services, `finapp-web` and `finapp-worker`, both running as `finapp`, hardened (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp`, explicit `ReadWritePaths`). Memory caps: worker `MemoryMax=1G`, web `MemoryMax=300M`. OCR runs under `nice`.

**Exposure:** `tailscale serve --bg --https=8443 http://127.0.0.1:8100` (tailnet-only). Verify with `tailscale funnel status` that port 8443 is **not** listed, and verify from the phone **on cellular with Tailscale off** that the URL is unreachable.

**Authentication (two layers):**

1. Tailscale identity headers (`Tailscale-User-Login`) checked against an allowlist.
2. An app-level passphrase (argon2) producing an httpOnly, SameSite=Strict session cookie, remembered per device for 90 days.

Layer 2 is needed because any local process, including agent sessions running as the human user, can send a forged identity header to a loopback port. If the installed Tailscale version supports proxying to a Unix socket, prefer a socket readable only by `finapp` and tailscaled; verify support on 1.102 rather than assuming it.


---

## 4. Data model (sketch; refine during build)

Money is `INTEGER` cents (CAD). Dates are ISO-8601. Soft-delete with `deleted_at`. Every derived value (matches, suggestions, estimates) must be reproducible from stored inputs.

| Table | Key columns / notes |
|---|---|
| `accounts` | institution, name, type (chequing / savings / credit_card / line_of_credit), owner (me / partner / joint), last4, cycle_day, require_pdf (bool), active |
| `statement_templates` | institution, format (pdf-text / pdf-scanned / csv / ofx), spec JSON, version, fixture_path, created_by (heuristic / llm / manual) |
| `statements` | account_id, period_start, period_end, file_sha256, file_path, opening_cents, closing_cents, status (received / parsed / reconciled / needs_review / failed), template_id + version |
| `transactions` | account_id, statement_id (nullable), posted_date, amount_cents (spend positive), description_raw, merchant_id, kind (spend / income / transfer / fee / interest / refund / settlement), transfer_pair_id, dedupe_key, source (statement / manual / receipt_only) |
| `merchants` | canonical name, aliases (normalized description patterns), default_category_id |
| `receipts` | image paths (1..n), merchant, date, total, tax, ocr_engine, status, transaction_id (nullable) |
| `line_items` | transaction_id, receipt_id (nullable), text, qty, amount_cents, category_id, activity_id (nullable). **Every transaction has at least one line item** (an implicit single line when there is no receipt) |
| `splits` | line_item_id, payer_party, partner_share_bp (0-10000), manually_confirmed (bool) |
| `split_decisions` | features JSON, suggested_bp, confidence, outcome (accepted / corrected), final_bp, ts (the learning log) |
| `categories` | name, kind (fixed / variable / discretionary / savings / income / transfer) |
| `budgets` | month, category_id, planned_cents; plus `month_plan` (income, savings target, **buffer_cents**) |
| `months` | month, state (open / closed), closed_at, snapshot JSON |
| `activities` | name, icon, estimate_cents, estimate_locked, weight (1-5), credits_per_month, cadence JSON |
| `activity_credit_periods` | month, activity_id, allotted, estimate_cents_at_time, unlock_times JSON |
| `activity_logs` | activity_id, ts, source (live / reconciliation), idempotency_key, undone_at |
| `notification_prefs`, `push_subscriptions`, `notifications_sent` | `notifications_sent` is unique on (rule_key, period_key) for idempotency |
| `pet_state_history` | ts, health, mood, evil_flag, inputs JSON |
| `llm_audit` | ts, purpose, payload_sha256, redaction_level, bytes, cost_cents |
| `idempotency_keys` | key, endpoint, response snapshot |

Split rounding: allocate cents with the **largest-remainder method** so the parts always sum exactly to the line amount.

---

## 5. Feature specifications

### 5.1 Statement ingestion and templates

**5.1.1 Drop zone.** Anywhere in the desktop UI, multiple files at once. On Android, accept files through the PWA **Web Share Target** (manifest `share_target`) and a file picker. Accept PDF, CSV, OFX/QFX. Show per-file outcome:

- **Recognized and complete**: parsed, reconciled, filed, period marked complete.
- **Recognized, reconciliation failed**: stored, flagged `needs_review`, period *not* marked complete.
- **Unrecognized**: starts the template flow (5.1.3).
- **Duplicate** (same SHA-256): ignored with a note.
- **Account ambiguous or new**: ask which account, or whether to create one (match on account-number locator, last 4 only).

**5.1.2 Recognition.** Each template carries identification rules (institution strings, header-row text, layout hints) and returns a match score; accept at or above a threshold, otherwise treat as unrecognized. OFX is standardized and needs no template. CSV templates are column mappings (date format, debit/credit columns versus signed, sign convention, header row).

**5.1.3 Template spec (declarative JSON; the builder designs the final schema).** It must express:

- identification rules and minimum score
- account-number locator (stored masked to last 4)
- statement period start/end locators and accepted date formats
- opening and closing balance locators (credit cards: previous balance / new balance)
- transaction table: page range, column x-ranges or a row regex, multi-line descriptions, skip patterns (headers, footers, "continued")
- **year inference** for rows that omit the year ("Sep 28"), derived from the statement period including the Dec/Jan boundary
- amount conventions: split debit/credit columns, signed, `CR`/`DR` suffix, thousands and decimal separators, sign inversion for cards
- foreign-currency lines (use the posted CAD amount; keep the original text)
- version number, plus the **original file kept as a regression fixture**

**5.1.4 Creating a template for an unrecognized statement.**

1. Extract text with coordinates locally. Find candidate regions by heuristics: repeated date-leading rows for the table; keyword lines ("Opening balance", "Statement period", "Previous balance", "New balance") for periods and balances.
2. Draft a template by local heuristics, optionally refined by an LLM from a **masked excerpt**:
    - every digit becomes `9`;
    - letters inside transaction-row description text become `x`;
    - header rows and keyword lines stay verbatim;
    - coordinates and layout are preserved;
    - only the table header, the first ~8 rows and the keyword lines are sent, never the whole file.

    The exact payload is **shown to the owner and requires consent each time**; it is written to `llm_audit`. Because digits are masked, day/month order is resolved **locally** by testing both orders against the real data and choosing the one consistent with the period.
3. Dry-run the draft on the real file locally. Show a preview table, row count, totals and the reconciliation result. Let the owner fix column mappings by clicking header cells.
4. **Gate:** the template is saved only when reconciliation passes (opening + sum of transactions = closing, within 1 cent), or the owner overrides with a typed reason.
5. Save template v1 plus the fixture, then continue ingesting the file.
6. **Drift detection:** a recognized template that suddenly fails reconciliation, or finds zero rows, flags "layout may have changed" and offers re-templating (new version; old statements keep their version).

**5.1.5 Dedupe.** File-level SHA-256, plus a transaction `dedupe_key` (account, date, amount, normalized description, running-balance or sequence) so overlapping CSV and PDF exports never double-insert.

### 5.2 Required-statements view

A matrix: **rows = accounts, columns = periods (months)**. Cell states:

- *not yet due*
- *missing* (overdue)
- *received, needs review* (parsed but not reconciled)
- *complete* (reconciled **and** opening balance equals the prior statement's closing balance, no gap)
- *covered by CSV* (a CSV spans the period but there is no official statement; matters when `require_pdf` is set)
- *not applicable* (before open / after close)

Expected period boundaries come from the previous statement (end + 1 day) or the account's `cycle_day`. A single file that spans several periods satisfies each of them. A due period turns *missing* after a grace period (default 5 days). Clicking a cell opens the statement or an upload target. This view is the first step of the month-close checklist (5.6).

### 5.3 Transactions: normalization and transfers

- **Normalize** descriptions into `merchants` (aliases learned from the owner's corrections).
- **Transfer detection:** equal-and-opposite amounts across the owner's accounts within +/-3 days become a `transfer_pair`, excluded from spend. Payments to the partner marked as settlement become `settlement`, which feeds the balance screen rather than the budget.
- **Month assignment** by transaction date, so a card statement spanning two calendar months contributes to both.
- Manual entry: add transaction, with the same line-item and split machinery. Cash spending lives on a synthetic "Cash" account.

### 5.4 Receipts and OCR

**Capture.** Phone: camera through `<input type="file" accept="image/*" capture="environment">`, multiple photos per receipt for long ones. Desktop: drag and drop. Photos taken offline are stored in an IndexedDB outbox and uploaded later (5.10).

**Pipeline (serial, low priority, memory-capped):** downscale to a ~2200 px long edge, deskew/crop/threshold (Pillow, add OpenCV headless only if needed), Tesseract with PSM 4 and 6 and keep the better parse score, then a rule-based parser extracting merchant, date, subtotal, tax lines, total and line items. Handle quantity lines, negative discount lines and tip lines. 60 s timeout per receipt.

**Validation:** sum of lines + tax should equal total within 2 cents; otherwise flag for review.

**Review screen:** photo beside the extracted fields. Nothing is saved until confirmed.

**Optional "better scan":** a per-receipt button sends **that one image** to Claude's vision model, returning structured JSON (merchant, date, line items, tax, total) that must pass the same validation. EXIF/location data is stripped before sending. Audited. Subject to the monthly cost cap and a global kill switch.

**Matching a receipt to a bank transaction.** Candidates are spend transactions within +/-5 days (or any date if the receipt has none). Score by amount closeness, merchant similarity and date proximity. Tolerance: exact within 1 cent, or up to +25% above the receipt total for restaurants and bars (tips). Auto-link only when the top score clears a threshold *and* is unique; otherwise ask. When linked, the transaction's implicit line is replaced by the receipt lines plus a balancing "tip / unlisted" line if the bank amount is larger. Matching reruns whenever either side arrives. Unmatched receipts can become a manual cash transaction.

**Tax:** keep tax lines separate and allocate them across lines **proportionally to taxable lines**, using per-line taxable flags where the receipt shows them. Do not hard-code a rate (many grocery items are zero-rated).

**Batching:** one notification per OCR batch ("3 receipts ready to review"), never one per receipt.

### 5.5 Splitting and learning

Each line item gets a split between the two parties, plus a payer (from the account owner, joint accounts default to 50/50 payer). **Settle-up** = what each party paid minus what each owes; show a running balance and a "mark settled" action that records a settlement transaction.

**Suggestion engine (explainable, counting-based, pure Python, no heavy ML):**

- Every confirmed decision is a vote for the chosen `partner_share_bp`. Weight = `0.5^(age_days / 90)` (recency), with **corrections weighted 1.5x**.
- Look up evidence from most specific to least: (merchant, normalized line text) -> merchant -> category -> global default. Use the first level with effective sample size `n_eff >= 2`.
- `confidence = (top_vote_weight / total_weight) * (1 - 1/(1 + n_eff))`.
- Tiers: **A** (`confidence >= 0.85` and `n >= 4`) pre-filled and pre-checked; **B** (`0.5 <= confidence < 0.85`) pre-filled and needs a glance; **C** no suggestion.
- The review UI always shows the reason ("last 9 times at this merchant you did 50/50"). Items are grouped by merchant with bulk actions ("apply to all from this merchant", "confirm all suggested"). Month close requires zero tier-C items; A and B can be bulk-confirmed.
- **Pinned rules** the owner defines ("always 100% me for Game Store") beat learned evidence.
- Display suggestion accuracy ("suggestions were right 87% of the time this month").
- Cold start: nothing is suggested in month one; bulk apply-by-merchant is the fast path.

Optional later: a lightweight classifier over description tokens. Avoid heavy dependencies (scikit-learn plus numpy is large for a RAM-constrained host).


### 5.6 Budgeting, planning and month close

**Categories** carry a kind (fixed / variable / discretionary / savings / income / transfer). Every view toggles between **household total** and **my share**.

**Budget view:** planned versus actual per category for the selected month, variance, a 6-month trend, and the month's explicit **buffer** (`buffer_cents`). **Planning:** copy forward last month, prefill from the trailing 3-month average, add sinking funds (annual costs divided by 12) and savings targets. The **discretionary pool** = planned income - fixed - savings - buffer (my share) and feeds activity credits (5.7). Advanced forecasting is out of scope for v1.

**Month-close state machine:** `open` -> statements incomplete -> all statements reconciled -> receipts matched -> review queue empty -> `closed`. Closing:

1. freezes actuals into `months.snapshot`;
2. runs the **credit audit** (5.7.5);
3. recomputes activity estimates (5.7.3);
4. computes the pet's monthly score (5.9);
5. generates the next month's credit allotments if not already generated;
6. sends the "month closed" notification.

A closed month can be reopened; everything downstream is recomputed from stored inputs.

### 5.7 Activity credits

The owner spends **credits (counts)**, never dollars, for recurring budget-draining behaviors. Example activities: ordered in, drinks out, a toy, shopping, tickets, medium grocery shop, big grocery shop. The list is user-editable.

**5.7.1 Per-activity attributes:** name, icon, `estimate_cents` (my share per occurrence), `weight` (priority 1-5), `credits_per_month`, cadence rules, optional `estimate_locked`.

**5.7.2 Allocation.** `pool_i = pool * weight_i / sum(weights)`; `credits_i = floor(pool_i / estimate_i)`; leftover cents return to the buffer. The owner may override any credit count; the screen shows the resulting total versus the pool and warns when it exceeds it.

**5.7.3 Estimates ("backend numbers from past comparables").** During review, line items are tagged with an activity (the same suggestion machinery as splits; merchant and category predict the tag). Estimate = trimmed mean (drop top and bottom 10%) of tagged **my-share** amounts over the trailing 6 closed months, minimum 3 samples, otherwise the owner's seed. After each close: `new = alpha * observed_mean + (1 - alpha) * old` with `alpha = 0.7` when observed exceeds the old estimate (adjust **up** faster) and `0.3` when below. Show every change with its reason; honor `estimate_locked`. Groceries: tiers (medium/big) default to thresholds from the user's own grocery-transaction quantiles, shown on a histogram and editable.

*Lag to design around:* the owner closes a month only after it ends, so next month's credits are first allotted from last known estimates, and the post-close recompute adjusts **only credits not yet unlocked**.

**5.7.4 Cadence and unlocking.** For `N` credits in a month of `D` days, the ideal unlock time for credit `k` is `start + k * D / N`, snapped forward to the next allowed slot (optional preferred weekdays and time window, e.g. drinks Fri/Sat evening), strictly increasing and within the month. A credit is *available* once unlocked and stays so until used or month end (no rollover unless `rollover_cap` is set). A log consumes the earliest available credit:

- **on pace:** a credit was available;
- **borrowed:** none unlocked yet but allotment remains (allowed, flagged yellow);
- **overdrawn:** allotment exhausted (flagged red).

**5.7.5 Credit audit at close.** For each activity compare *logged occurrences* to *statement-tagged occurrences* (line items for one activity on one calendar day count as one occurrence). Show the discrepancies ("you logged 4 drinks nights; the statements show 6"). The owner can add missing occurrences as retroactive logs (`source = reconciliation`, flagged, counted toward the month's actuals but **not** toward live pace) or mark a tag as mistaken. Estimate updates use statement truth, not taps.

**5.7.6 Quick-log screen.** A grid of large buttons, each with an icon, the credits remaining (e.g. "2 left") and the next unlock ("unlocks Fri"). One tap logs, vibrates and shows a 10-second **Undo** toast. Long-press for backdating or a note. **No dollar amounts and no numeric entry** appear anywhere on this screen. It must work offline (5.10).

### 5.8 Notifications

**Types and defaults**

| Type | Default | Notes |
|---|---|---|
| `credit_unlocked` (per activity) | on | "Yay! You can go out for drinks this week!" Actions: **Log it** / **Not now** |
| `credit_reminder` | off | at most one gentle reminder per credit |
| `pace_warning` | on | sent once on borrow/overdraw, in the pet's voice, never scolding |
| `statements_due` | on | day 1 at 10:00, repeat day 5 if still missing; stops when done |
| `receipts_ready` | on | batched |
| `review_queue` | off | only after the owner has started a review |
| `month_closed` | on | summary plus pet reaction |
| `pet_state_change` | on | |
| `weekly_digest` | off | |

**Wording by cadence:** if credits for the activity unlock every <= 3 days say "today"; otherwise "this week". Provide 3-5 copy variants per type to avoid fatigue. Suppress `credit_unlocked` for an activity that is overdrawn.

**Customization (settings screen):** per-type on/off; per-activity credits, cadence mode (even / preferred weekdays / front-loaded), preferred time of day; global quiet hours; daily cap on non-urgent notifications (default 2); "vacation mode" snooze; tone (pet voice versus plain); and a **Send me a test notification** button. Per-activity settings override global ones.

**Delivery:** a scheduler tick every 5 minutes evaluates rules in the configured time zone. Use `tag` per activity/month so stale notifications are replaced, `renotify: false`, a TTL matching the allowed window, and a deep link in `data.url`. Idempotency via `notifications_sent` (unique on rule key + period key). Retry 5xx with backoff; on 404/410 delete the subscription. **Push health screen:** last success per device, plus an in-app banner if every subscription is dead.

**Payloads carry no dollar amounts.** Content is encrypted end to end but still transits Google's push service.

**Notification actions:** "Log it" in the service worker POSTs the log event using the session cookie; if offline or off-tailnet it goes to the outbox and uses Background Sync.

**Android setup checklist (shown in-app):** install the PWA, allow notifications, set Chrome battery usage to unrestricted, run the test button. Request notification permission only from a user gesture.

### 5.9 The pet

An **original** pixel-art character (not a Tamagotchi lookalike), drawn as SVG generated from pixel matrices in code (no image-generation dependency, no external assets). States: thriving, happy, okay, sad, depressed, **evil** (feral variant with horns and red eyes), plus an optional "sleepy" overlay for overdue statements. Two-frame idle animation using CSS `steps()`; honor `prefers-reduced-motion`. Static PNG per mood (192x192) for notification icons. Reactions: a happy gulp when a credit is used on pace, a wince when borrowing or overdrawing. Include a **mood gallery** page that previews every state. A "calm mode" setting disables the evil visuals and uses neutral copy.

**Inputs (live signal plus monthly truth).** The only live data is self-reported credit taps; monthly closes supply ground truth.

- Overdraw cost `OD = sum over activities of overdrawn_count * estimate`; borrow cost `BC = sum of borrowed_count * estimate * 0.25`.
- Live pace `P = 1 - clamp((OD + BC) / buffer, 0, 1)`; if the buffer is 0, `P = 1` when `OD == 0`, else `0`.
- Closed-month score `S = 1 - clamp(max(0, actual_discretionary - planned_discretionary) / buffer, 0, 1)`.
- `EMA_S` over closed months, decay 0.6 (recent months weigh most); cold start `EMA_S = 0.8` so the pet begins happy.
- **Health `H = 0.4 * P + 0.6 * EMA_S`.**
- Mood bands with +/-0.05 hysteresis: thriving >= 0.85, happy >= 0.65, okay >= 0.45, sad >= 0.25, depressed < 0.25.
- **Evil** when `H < 0.25` **and** `P < 0.25` **and** the last two closed months each had `S < 0.5`. It clears only when `P >= 0.5`, then steps down through depressed to sad.
- All constants are config values with sensible defaults, not hard-coded. Smooth transitions so a single tap cannot flip the mood. Log every state change to `pet_state_history`.

### 5.10 PWA, offline and sync

- Installable PWA: manifest with 192/512 icons and `share_target`, a service worker caching the app shell. Add the pet's mood icons.
- **Server is the single source of truth.** The phone holds only an **outbox** in IndexedDB for two item types: `log_activity` and `receipt_photo` (blobs). Each item has a UUID idempotency key; the server dedupes via `idempotency_keys`. Flush on app open, the `online` event, and Background Sync (`outbox-flush`). Logs are append-only events, so merge is trivial; **Undo** is a compensating event (`undone_at`).
- Everything else (review, splitting, budgeting) is online-only through the tailnet and mostly a desktop task. Do not build offline editing for those.
- Opening the app needs Tailscale on the phone; receiving push does not.

---

## 6. Security and privacy

- **Exposure:** tailnet-only (Serve). Never Funnel. Acceptance test in Phase 0.
- **Process isolation:** dedicated `finapp` user; data dir `0700`; units hardened as in section 3.
- **Web:** strict CSP; SameSite=Strict cookie plus CSRF token on mutations; mask account numbers to last 4 in the UI and logs; no transaction text in logs.
- **Uploads:** sniff content type, cap at 50 MB, store under random filenames, **run PDF parsing in a subprocess with a CPU/memory/time limit**, never execute uploaded content.
- **Outbound calls:** exactly two purposes (masked template drafting, per-receipt better scan), each opt-in, each written to `llm_audit` (hash, redaction level, bytes, cost), with a monthly cost cap and a global kill switch. API key in `/etc/finapp/env` (0600, root:finapp). Strip EXIF before any image leaves the machine.
- **Backups:** nightly `sqlite3 .backup` plus the files directory, encrypted (age or restic), to an off-machine destination (decision 7), with retention and a **documented restore drill that is actually run once**.
- **At rest:** the disk is **verified unencrypted**, and swap is plaintext. Mitigate in v1: encrypt stored statements and receipts at the application layer (per-file authenticated encryption with a key held in `/etc/finapp/` and loaded into the service environment, never in the database), encrypt backups, keep the worker's memory cap low to limit swapping, and set `vm.swappiness` low for the host if the owner agrees. State the residual risk in the README; full-disk encryption requires a reinstall and is the owner's call.
- **Statements contain account numbers.** Treat the whole data directory as secret.

---

## 7. Phases and acceptance criteria

Review at the end of each phase before starting the next.

**Phase 0: Foundation (small).** Repo, config, FastAPI, migrations, `finapp` user and systemd units, Serve exposure (the owner runs the command), two-layer auth, installable PWA shell, push subscribe plus test notification, backup job skeleton.
*Accept:* (1) installs on the Pixel from Chrome; (2) with Chrome **swiped away**, the test push arrives within 30 s; (3) the URL is unreachable from cellular with Tailscale off; (4) `tailscale funnel status` does not list 8443; (5) services survive a reboot; (6) the existing Obsidian URL still works.

**Phase 1: Credits, quick-log, notifications, pet (medium).** Seeded activities and estimates entered by hand; manual pool.
*Accept:* unlock schedules are deterministic under an injected clock, including DST (2026-11-01 and 2027-03-14 in America/Toronto); quick-log works in airplane mode and syncs without duplicates; every notification preference takes effect; the mood gallery renders all states; live pace moves the pet as specified.

**Phase 2: Accounts and statement ingestion (large).** Accounts; CSV and OFX first; then PDF text templates and the template-creation flow; reconciliation; dedupe; required-statements view.
*Accept:* three invented bank layouts ingest and reconcile; an unrecognized file produces a working template via the flow; a deliberately corrupted statement fails reconciliation and is **not** marked complete; the matrix shows all six cell states correctly.

**Phase 3: Transactions and splitting (medium).** Merchants, categories, transfer detection, manual transactions, split review UI and the learning engine, settle-up balance.
*Accept:* card payments are excluded from spend; split rounding always sums exactly; tier A/B/C behavior matches 5.5 on a seeded history; accuracy metric displays.

**Phase 4: Receipts (large).** Capture (camera and drop), outbox upload, serial OCR worker, parser, validation, matching, line-item explosion, optional vision scan.
*Accept:* OCR runs without pushing the host into swap (check `MemoryMax` behavior); a receipt linked to a bank line replaces its implicit line without changing the transaction total; tip handling works; batch notification fires.

**Phase 5: Budget and month close (medium).** Budget and planning views, pool computation, close state machine, estimate updates, credit audit, pet monthly score.
*Accept:* closing a month freezes actuals; reopening recomputes cleanly; estimate adjustment moves up faster than down; audit flags a logged-versus-statement mismatch.

**Phase 6: Polish (small).** Recurring-transaction detection, exports, weekly digest, restore drill, performance pass.

---

## 8. Testing

- `pytest`; a **synthetic statement generator** (e.g. reportlab) producing PDFs and CSVs in at least three invented layouts, including multi-line descriptions, year-less dates across Dec/Jan, credit-card sign conventions and foreign-currency lines.
- Property tests: split allocation sums exactly; reconciliation arithmetic; idempotent outbox replay; transfer pairing never double-counts.
- Scheduler tests with an injectable clock and DST boundaries.
- OCR golden set of 15-20 receipt images with expected JSON (owner supplies a few, redacted).
- Real-phone checks that automation cannot do: install, push with Chrome closed, notification actions, battery-optimization behavior.
- Dataset size target: 12 months x 6 accounts x ~150 transactions with sub-second views.
- **First utility to build:** `finapp mask-sample`, a CLI that digit-masks a real statement so the owner can safely provide development samples (decision 9).

---

## 9. Non-goals (v1)

Live bank feeds (Plaid/Flinks), investments, tax reporting, partner login or multi-user, a native Android app (a TWA wrapper is possible later), multi-currency accounting, complex rollover rules, cloud hosting.

---

## 10. Remaining open questions

- Pay-period budgeting instead of calendar months, if income timing matters.
- Whether the partner wants a read-only view of the balance screen later.
- Retention policy for stored statement and receipt originals.
- Whether estimates should be per-person if the partner's habits differ.
- UPS or other power protection, given the app lives on a laptop acting as a NAS.

---

## Appendix A: tone guide and copy samples

Playful, short, never about shame or debt. Examples:

- Unlock: "Yay! You can go out for drinks this week!" / "Ordering in is back on the menu today!"
- Borrowed: "Cashing in a credit early, fine, but it'll be a leaner week."
- Overdrawn (pet voice): "Hrmph. That was one over. I'll live."
- Statements due: "Money tidy-up time: 4 statements to go."
- Month closed: "Month closed! I'm feeling great." / "Month closed. We have notes."

Calm mode swaps these for neutral phrasing and disables the evil sprite.
