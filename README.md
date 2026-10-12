# Alberich

A spending pet. You split what you can spend into pouches (coffee, takeout, a night out); Alberich, a dwarf who guards the gold, stays happy while you spend at a steady pace and grumbles when you rush it.

- **Phone app (Android)**: the whole app runs on the phone, with no server (`web/` is the UI, `android/` wraps it). Pouches, credits, the pet, reminders and the home-screen widget all work alone. Built without Gradle, see `android/build.sh`; `android/publish.sh` signs it and keeps a copy at `release/alberich.apk`.
- **Computer companion (optional)**: `app/finapp` (FastAPI + SQLite) reads bank and card statements, files receipts, and syncs pouches with the phone over the home network (QR pairing, certificate pinned by the phone). `desktop/start.py` runs it anywhere with Python 3.10+.
- **Site**: `site/build.py` builds the landing page, a demo, a browser copy and the downloads into `site/dist`; `site/wrangler.jsonc` deploys it to alberich.jessevriend.com.

The credit, pet and reminder rules exist twice, in `app/finapp` (Python) and `web/core.js` (JavaScript). A parity test keeps them identical.

Tests:
- `cd app && python -m pytest`
- `cd web && TZ=America/Toronto node --test tests/parity.test.js` (regenerate with `tests/gen_parity.py`)

Deploy notes for the author's own machine: `deploy/`.
