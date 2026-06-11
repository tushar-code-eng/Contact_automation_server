# Contact Automation — Roadmap v2.0

Next version features, to be built after v1.0 is stable and deployed.

---

## Phase 1 — GHL Sub-Account Automation

**Goal:** Remove all manual GHL steps when onboarding a new user.

**Current flow (v1.0):**
1. Admin manually creates sub-account in GHL
2. Admin copies API token + location ID
3. Admin pastes them into the admin panel

**Target flow (v2.0):**
1. Admin creates user in the admin panel
2. App automatically:
   - Creates a GHL sub-account (location) via `POST /locations`
   - Configures the client portal for that location
   - Sends the user a GHL client portal invite email

**What's needed:**
- Agency-level GHL API key (stored in `.env`, not per-user)
- New `ghl_agency.py` module with `create_subaccount(email, name)` helper
- Call it from the `POST /admin/create-user` route
- Store returned `locationId` and generate a location-level API token automatically
- Handle GHL API errors gracefully (show message in admin panel)

**GHL API endpoints:**
- `POST /locations` — create sub-account
- `POST /locations/{locationId}/users` — add user to location
- Client portal invite — send via GHL's built-in workflow or `POST /contacts/{contactId}/send-portal-invite`

---

## Phase 2 — Job Queue for Concurrency (Celery + Redis)

**Goal:** Support 15+ simultaneous users running jobs without crashing the server.

**Problem with v1.0:**
- Each job = one Playwright browser (~400MB RAM)
- No limit on concurrent jobs — 20 users clicking Run Now = server OOM crash

**Target:**
- Max N browsers running at once (configurable, e.g. N=8)
- Extra jobs wait in queue and start as slots free up
- User sees "Position 3 in queue" on the run page

**What's needed:**
- Replace `threading.Thread` in `job_runner.py` with Celery tasks
- Redis as the broker (cheap, ~$5/mo managed or self-hosted)
- `celery -A tasks worker --concurrency=8` as a second systemd service
- Update SSE stream to work with Celery task state

---

## Phase 3 — PostgreSQL (replace SQLite)

**Goal:** Handle higher write concurrency as user count grows.

**Trigger:** Only needed if hitting SQLite lock errors under load.
**Migration:** SQLAlchemy swap — minimal code change since queries are simple.

---

## Phase 4 — Domain + SSL

**Goal:** Serve the app over HTTPS with a proper domain.

**Steps:**
- Point a subdomain (e.g. `app.clientdomain.com`) to the server IP
- Update `nginx.conf` with the domain name
- Run Certbot: `certbot --nginx -d app.clientdomain.com`
- SSL auto-renews via cron

---

## Phase 5 — UX Improvements

- Email notification when job completes / fails
- User can reset their own PRPT password from settings
- Admin can see all users' recent jobs in one view
- Show number of contacts synced per run in job history

---

## Priority Order

| Phase | Effort | Impact | When to build |
|-------|--------|--------|---------------|
| GHL Auto-create | Medium (2 days) | High | When client adds 10+ users |
| Job Queue | Medium (2 days) | High | When hitting RAM issues |
| PostgreSQL | Low (4 hours) | Medium | When hitting SQLite lock errors |
| Domain + SSL | Low (1 hour) | High | As soon as client has a domain |
| UX | Low–Medium | Medium | Ongoing |
