import threading
import traceback
from db import create_job, update_job_status, append_job_log
from pipeline_logger import set_logger
from pipeline import run_pipeline
from user_context import build_context

# ── OTP coordination ──────────────────────────────────────────────────────────
# Each job that reaches the OTP step blocks on an Event until the web UI
# calls submit_otp() with the code.

_lock       = threading.Lock()
_otp_events = {}   # job_id -> threading.Event
_otp_values = {}   # job_id -> str (the code)


def submit_otp(job_id: int, otp_code: str):
    """Called from the web route when the user submits their OTP code."""
    with _lock:
        _otp_values[job_id] = otp_code
        event = _otp_events.get(job_id)
    if event:
        event.set()


def _make_otp_fn(job_id: int):
    """
    Returns an otp_fn(page, needs_code) callable for the scraper.

    needs_code=True  → TOTP input shown on page; block until user submits code via browser.
    needs_code=False → Push notification MFA; wait up to 90s for phone approval.
    """
    def otp_fn(page, needs_code: bool = True):
        if needs_code:
            # ── TOTP path: wait for user to enter 6-digit code ────────────────
            update_job_status(job_id, "waiting_otp")
            append_job_log(job_id, "⏳ Enter the OTP code shown in your browser...")

            event = threading.Event()
            with _lock:
                _otp_events[job_id] = event

            got_otp = event.wait(timeout=300)   # 5-minute window

            with _lock:
                otp = _otp_values.pop(job_id, None)
                _otp_events.pop(job_id, None)

            update_job_status(job_id, "running")

            if got_otp and otp:
                append_job_log(job_id, "✅ OTP received — completing login...")
                try:
                    otp_selector = (
                        'input[name="otpCode"], '
                        'input[type="tel"], '
                        '#idTxtBx_SAOTCC_OTC'
                    )
                    page.locator(otp_selector).first.fill(otp)
                    page.locator('input[type="submit"]').click()
                    page.wait_for_timeout(3000)
                except Exception as e:
                    append_job_log(job_id, f"⚠️ OTP entry error: {e}")
            else:
                append_job_log(job_id, "⏰ OTP timed out after 5 minutes — login may have failed")

        else:
            # ── Push notification path: just wait for phone approval ──────────
            update_job_status(job_id, "waiting_push")
            append_job_log(job_id, "📱 Push notification sent — approve on your phone (up to 90s)...")

            try:
                # Wait for the MFA page to navigate away (approval detected)
                page.wait_for_url(
                    lambda url: "login.microsoftonline.com" not in url,
                    timeout=90_000,
                )
                append_job_log(job_id, "✅ Phone approval detected.")
            except Exception:
                append_job_log(job_id, "⚠️ Push approval timed out — login may have failed.")

            update_job_status(job_id, "running")

    return otp_fn


# ── Job thread ────────────────────────────────────────────────────────────────

def _run_job(job_id: int, user_dict: dict):
    """Runs entirely in a background daemon thread."""

    # Wire the pipeline logger to write into DB for this thread
    def log_to_db(msg: str):
        append_job_log(job_id, msg)

    set_logger(log_to_db)
    update_job_status(job_id, "running")

    try:
        ctx    = build_context(user_dict)
        otp_fn = _make_otp_fn(job_id)
        run_pipeline(ctx, otp_fn=otp_fn)
        update_job_status(job_id, "completed")
    except Exception as e:
        tb = traceback.format_exc()
        append_job_log(job_id, f"❌ Job crashed: {e}")
        for line in tb.splitlines():
            append_job_log(job_id, line)
        update_job_status(job_id, "failed")


# ── Public API ────────────────────────────────────────────────────────────────

def start_job(user_id: int, user_dict: dict) -> int:
    """
    Create a DB job record, spawn a background thread, and return the job_id.
    Caller must ensure no active job already exists for this user.
    """
    job_id = create_job(user_id)
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, dict(user_dict)),
        daemon=True,
        name=f"job-{job_id}",
    )
    thread.start()
    return job_id
