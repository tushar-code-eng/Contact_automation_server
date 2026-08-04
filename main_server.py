import os
import sys
import json
import asyncio

# Playwright spawns a subprocess — on Windows the default SelectorEventLoop
# doesn't support subprocesses, so force ProactorEventLoop process-wide.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from db import (
    init_db, create_user, get_all_users, get_user_by_id,
    update_user_prpt, update_user_ghl, delete_user, update_user_password,
    get_active_job_for_user, get_recent_jobs_for_user,
    get_job, get_job_logs_since,
)
from auth import authenticate, hash_password, verify_password
from job_runner import start_job, submit_otp, request_stop

_secret_key = os.getenv("SECRET_KEY", "")
if not _secret_key or _secret_key == "change-me-in-production":
    raise RuntimeError("SECRET_KEY must be set to a strong random string in .env")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=_secret_key)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    os.makedirs("data", exist_ok=True)
    os.makedirs("sessions", exist_ok=True)
    os.makedirs("backups", exist_ok=True)
    init_db()

    from db import get_db
    conn = get_db()
    # Mark stale jobs from previous server session as failed
    conn.execute(
        "UPDATE jobs SET status='failed', finished_at=CURRENT_TIMESTAMP "
        "WHERE status IN ('running', 'waiting_otp', 'waiting_push', 'pending')"
    )
    # Keep only last 10 days of job logs
    conn.execute(
        "DELETE FROM job_logs WHERE created_at < datetime('now', '-10 days')"
    )
    conn.commit()
    conn.close()


# ── Auth helpers ────────────────────────────────────────────────────────────

def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)


def require_user(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return user


def require_admin(request: Request):
    user = current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    return user


# ── Auth routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = authenticate(email, password)
    if not user:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid email or password"}
        )
    request.session["user_id"] = user["id"]
    request.session["role"] = user["role"]
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    active_job   = get_active_job_for_user(user["id"])
    recent_jobs  = get_recent_jobs_for_user(user["id"], limit=10)

    return templates.TemplateResponse("dashboard.html", {
        "request":    request,
        "user":       dict(user),
        "active_job": dict(active_job) if active_job else None,
        "recent_jobs": [dict(j) for j in recent_jobs],
    })


# ── Settings ─────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": dict(user),
        "success": None,
        "error": None,
    })


@app.post("/settings", response_class=HTMLResponse)
def settings_save(
    request: Request,
    prpt_username: str = Form(...),
    prpt_password: str = Form(...),
    prpt_rep_id: str = Form(...),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    update_user_prpt(user["id"], prpt_username, prpt_password, prpt_rep_id)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": {**dict(user), "prpt_username": prpt_username, "prpt_rep_id": prpt_rep_id},
        "success": "Settings saved successfully.",
        "error": None,
    })


@app.post("/settings/change-password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    def render(error=None, success=None):
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "user": dict(user),
            "success": success,
            "error": error,
        })

    if not verify_password(current_password, user["password_hash"]):
        return render(error="Current password is incorrect.")
    if len(new_password) < 6:
        return render(error="New password must be at least 6 characters.")
    if new_password != confirm_password:
        return render(error="New passwords do not match.")

    update_user_password(user["id"], hash_password(new_password))
    return render(success="Password changed successfully.")


# ── Admin panel ───────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = require_admin(request)
    if isinstance(user, RedirectResponse):
        return user

    users = get_all_users()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "users": [dict(u) for u in users],
        "success": None,
        "error": None,
    })


@app.post("/admin/create-user", response_class=HTMLResponse)
def admin_create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    is_admin: str = Form(""),
    ghl_api_token: str = Form(""),
    ghl_location_id: str = Form(""),
):
    user = require_admin(request)
    if isinstance(user, RedirectResponse):
        return user

    users = get_all_users()
    try:
        role = "admin" if is_admin else "user"
        user_id = create_user(email, hash_password(password), role=role)
        if ghl_api_token or ghl_location_id:
            update_user_ghl(user_id, ghl_api_token, ghl_location_id)
        users = get_all_users()
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "users": [dict(u) for u in users],
            "success": f"{role.capitalize()} {email} created successfully.",
            "error": None,
        })
    except Exception:
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "users": [dict(u) for u in users],
            "success": None,
            "error": f"Email {email} already exists.",
        })


@app.post("/admin/update-ghl/{user_id}", response_class=HTMLResponse)
def admin_update_ghl(
    request: Request,
    user_id: int,
    ghl_api_token: str = Form(...),
    ghl_location_id: str = Form(...),
):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin

    update_user_ghl(user_id, ghl_api_token, ghl_location_id)
    users = get_all_users()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "users": [dict(u) for u in users],
        "success": "GHL settings updated.",
        "error": None,
    })


@app.post("/admin/delete-user/{user_id}")
def admin_delete_user(request: Request, user_id: int):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin

    delete_user(user_id)
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/reset-password/{user_id}", response_class=HTMLResponse)
def admin_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin

    users = get_all_users()

    def render(error=None, success=None):
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "users": [dict(u) for u in users],
            "success": success,
            "error": error,
        })

    if len(new_password) < 6:
        return render(error="Password must be at least 6 characters.")
    if new_password != confirm_password:
        return render(error="Passwords do not match.")

    update_user_password(user_id, hash_password(new_password))
    users = get_all_users()
    return render(success="Password reset successfully.")


@app.post("/admin/set-last-date/{user_id}", response_class=HTMLResponse)
def admin_set_last_date(
    request: Request,
    user_id: int,
    last_date: str = Form(...),
):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin

    users = get_all_users()
    last_date = last_date.strip()
    if not last_date:
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "users": [dict(u) for u in users],
            "success": None,
            "error": "Date cannot be empty.",
        })

    data_dir = os.path.join("data", str(user_id))
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "last_date.json"), "w") as f:
        json.dump({"last_end_date": last_date}, f)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "users": [dict(u) for u in users],
        "success": f"Last scrape date set to {last_date}.",
        "error": None,
    })


# ── Run job ───────────────────────────────────────────────────────────────────

@app.post("/run")
def run_start(request: Request):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    # Block if a job is already active for this user
    if get_active_job_for_user(user["id"]):
        return RedirectResponse("/dashboard", status_code=302)

    # Require PRPT credentials before starting
    if not user["prpt_username"] or not user["prpt_rep_id"]:
        return RedirectResponse("/settings", status_code=302)

    job_id = start_job(user["id"], dict(user))
    return RedirectResponse(f"/run/{job_id}", status_code=302)


@app.get("/run/{job_id}", response_class=HTMLResponse)
def run_view(request: Request, job_id: int):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    job = get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        return RedirectResponse("/dashboard", status_code=302)

    return templates.TemplateResponse("run.html", {
        "request": request,
        "job":     dict(job),
        "user":    dict(user),
    })


@app.get("/run/{job_id}/status")
def run_status(request: Request, job_id: int):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    job = get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        return {"status": "not_found"}

    return {"status": job["status"]}


@app.post("/run/{job_id}/otp")
def run_otp(request: Request, job_id: int, otp_code: str = Form(...)):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    submit_otp(job_id, otp_code)
    return RedirectResponse(f"/run/{job_id}", status_code=302)


@app.post("/run/{job_id}/stop")
def run_stop(request: Request, job_id: int):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    job = get_job(job_id)
    if job and job["user_id"] == user["id"]:
        request_stop(job_id)

    return RedirectResponse(f"/run/{job_id}", status_code=302)


@app.get("/run/{job_id}/logs")
def run_logs(request: Request, job_id: int):
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user

    job = get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        return {"logs": []}

    from db import get_job_logs
    rows = get_job_logs(job_id)
    return {"logs": [r["message"] for r in rows]}


@app.get("/run/{job_id}/stream")
async def run_stream(request: Request, job_id: int):
    """SSE endpoint — streams job logs to the browser as they arrive.

    Supports native SSE reconnection: the browser sends Last-Event-ID after
    a dropped connection and the stream resumes from that log row, so the
    client never sees duplicate or missing lines without a page refresh.
    """
    raw_last_id = request.headers.get("last-event-id", "0")
    try:
        initial_last_id = int(raw_last_id)
    except ValueError:
        initial_last_id = 0

    async def event_generator():
        last_id   = initial_last_id
        done_sent = False

        while not done_sent:
            if await request.is_disconnected():
                break

            rows = get_job_logs_since(job_id, last_id)
            for row in rows:
                last_id = row["id"]
                msg = str(row["message"]).replace("\n", " ")
                yield f"id: {last_id}\ndata: {msg}\n\n"

            job = get_job(job_id)
            if job and job["status"] in ("completed", "failed"):
                yield "data: [DONE]\n\n"
                done_sent = True
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
