# Contact Automation — Server Migration Roadmap

## Overview
Migrate the local Python scraping script to a multi-user web app hosted on a VPS.
Each co-worker logs in with their own credentials and runs the scraper against their own Empire Today PRPT account.
All contacts are pushed to a shared GoHighLevel (GHL) account.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI (Python) |
| Database | SQLite |
| Auth | Session-based + bcrypt |
| Background Jobs | Python threading |
| Log Streaming | Server-Sent Events (SSE) |
| Frontend | Bootstrap 5 + Vanilla JS |
| Browser Automation | Playwright (existing) |
| Reverse Proxy | Nginx |
| SSL | Let's Encrypt (Certbot) |
| Process Manager | Systemd |
| Hosting | Hetzner CX21 (~$6/month) |

---

## Folder Structure

```
Contact_Automation_Server/
├── main_server.py              # FastAPI app entry point
├── job_runner.py               # Background job logic
├── auth.py                     # User login/session management
├── scraper.py                  # (existing, minor changes)
├── main.py                     # (existing, refactored for per-user config)
├── uploader.py                 # (existing, unchanged)
├── dedupe.py                   # (existing, unchanged)
├── db.sqlite                   # Users + job history
├── data/
│   └── {user_id}/              # Per-user data files
├── sessions/
│   └── auth_{user_id}.json     # Per-user Playwright sessions
└── templates/
    ├── login.html
    ├── dashboard.html
    └── run.html
```

---

## Phase 1 — Foundation (Day 1-2)
**Goal: Project skeleton + user management working**

- [ ] New project structure + folder setup
- [ ] SQLite schema — users, jobs tables
- [ ] FastAPI app skeleton
- [ ] User registration + login (bcrypt passwords)
- [ ] Session-based auth (protected routes)
- [ ] Basic login/dashboard HTML pages

---

## Phase 2 — Refactor Existing Code (Day 2-3)
**Goal: Existing script works per-user, not from a single .env**

- [ ] Config loads from DB/user object instead of `.env`
- [ ] Data files namespaced under `data/{user_id}/`
- [ ] Session files namespaced as `sessions/auth_{user_id}.json`
- [ ] `main.py` accepts a user config dict instead of global CONFIG
- [ ] Test that a single user run works end-to-end

---

## Phase 3 — Job Runner (Day 4-5)
**Goal: Runs happen in the background, status is tracked**

- [ ] Job model in SQLite (job_id, user_id, status, started_at, logs)
- [ ] Background thread spawned per job
- [ ] Job status: `pending → running → waiting_otp → completed / failed`
- [ ] Logs written to DB line by line as the job runs
- [ ] Only one active job per user at a time

---

## Phase 4 — OTP Relay (Day 5-6)
**Goal: User can complete MFA from the browser**

- [ ] Scraper detects when OTP page is reached → pauses, sets job status `waiting_otp`
- [ ] UI shows OTP input modal when status is `waiting_otp`
- [ ] User submits OTP → API endpoint receives it → scraper types it into browser
- [ ] Job resumes automatically after OTP

---

## Phase 5 — Web UI (Day 7-8)
**Goal: Non-tech users can use this without any help**

- [ ] Login page
- [ ] Dashboard — shows last run status, next scheduled run
- [ ] "Run Now" button — triggers a job
- [ ] Live log viewer (SSE stream)
- [ ] OTP modal (auto-appears when needed)
- [ ] Settings page — user updates their PRPT credentials + GHL token

---

## Phase 6 — Deployment (Day 9)
**Goal: Live on server, accessible via URL**

- [ ] Hetzner CX21 VPS setup (Ubuntu)
- [ ] Playwright dependencies installed
- [ ] Nginx + SSL (Let's Encrypt)
- [ ] Systemd service (auto-restart on reboot)
- [ ] Environment hardening (firewall, no root login)

---

## Phase 7 — Testing + Fixes (Day 10)
**Goal: Works reliably for multiple users**

- [ ] Test with 2-3 users simultaneously
- [ ] Test OTP flow end-to-end
- [ ] Test session expiry + re-login
- [ ] Fix any bugs found
- [ ] Hand off to client with a simple user guide

---

## Timeline Summary

| Phase | Days |
|---|---|
| Phase 1 — Foundation | Day 1-2 |
| Phase 2 — Refactor Existing Code | Day 2-3 |
| Phase 3 — Job Runner | Day 4-5 |
| Phase 4 — OTP Relay | Day 5-6 |
| Phase 5 — Web UI | Day 7-8 |
| Phase 6 — Deployment | Day 9 |
| Phase 7 — Testing + Fixes | Day 10 |

**Total: 10 days**
**Suggested client deadline: 12 days (2-day buffer for OTP/server surprises)**

---

## Key Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Empire Today blocks server IP for login | High | Test login on server early (Day 6) |
| OTP flow behaves differently on Linux | Medium | Build OTP relay before UI |
| Playwright memory usage with concurrent users | Medium | Limit to 1 active job per user |
