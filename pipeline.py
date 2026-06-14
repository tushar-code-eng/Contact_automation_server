import os
import json
import re
from datetime import datetime, timedelta

from scraper import scrape_all, scrape_installations, scrape_detail_parallel
from uploader import send_to_ghl
from dedupe import (
    load_processed, save_processed,
    load_processed_hashes, save_processed_hashes,
    has_record_changed, compute_record_hash,
)
from pipeline_logger import log, error
from user_context import UserContext


# ── File path helpers ────────────────────────────────────────────────────────

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic — prevents half-written files on crash


def _backup(path):
    """Keep one .bak copy of a file before overwriting it."""
    if os.path.exists(path):
        os.replace(path, path + ".bak")


# ── Date helpers ─────────────────────────────────────────────────────────────

def format_query_date(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}/{dt.year}"


def parse_appointment_date(value):
    if not value:
        return None
    date_part = value.strip().split(" ")[0]
    for fmt in ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%m-%d-%Y"]:
        try:
            return datetime.strptime(date_part, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(date_part)
    except ValueError:
        return None


def build_date_range(ctx: UserContext):
    if os.getenv("FULL_LOAD", "false").lower() in ("1", "true", "yes"):
        return "1/1/2024", format_query_date(datetime.now())

    saved = _load_json(ctx.path("last_date.json"), {}).get("last_end_date")
    if saved:
        anchor = parse_appointment_date(saved) or datetime.now()
        start_date = format_query_date(anchor - timedelta(days=90))
    else:
        start_date = "1/1/2024"

    return start_date, format_query_date(datetime.now())


# ── Accumulator helpers ───────────────────────────────────────────────────────

def load_all_records(ctx: UserContext) -> dict:
    records = _load_json(ctx.path("all_records.json"), [])
    if isinstance(records, list):
        return {r.get("email", r.get("phone", "")): r for r in records if r.get("email") or r.get("phone")}
    return records


def save_all_records(ctx: UserContext, records_dict: dict):
    _backup(ctx.path("all_records.json"))
    _save_json(ctx.path("all_records.json"), list(records_dict.values()))
    log(f"💾 Saved {len(records_dict)} accumulated records")


def load_scheduled_records(ctx: UserContext) -> dict:
    return _load_json(ctx.path("scheduled_records.json"), {})


def save_scheduled_records(ctx: UserContext, records_dict: dict):
    _backup(ctx.path("scheduled_records.json"))
    _save_json(ctx.path("scheduled_records.json"), list(records_dict.values()))
    log(f"💾 Saved {len(records_dict)} scheduled records")


def save_backup(ctx: UserContext, data):
    backup_dir = ctx.path("backups")
    os.makedirs(backup_dir, exist_ok=True)
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S.json")
    _save_json(os.path.join(backup_dir, filename), data)
    log(f"💾 Backup saved: {filename}")


# ── Deduplication ─────────────────────────────────────────────────────────────

def filter_unique_by_email_phone(rows: list) -> list:
    unique = {}
    for row in rows:
        email = (row.get("email") or "").strip().lower()
        phone = re.sub(r"\D", "", row.get("phone") or "")
        key   = email if email else phone
        if not key:
            continue
        candidate_date = parse_appointment_date(row.get("appointment"))
        if key not in unique:
            unique[key] = row
        else:
            existing_date = parse_appointment_date(unique[key].get("appointment"))
            if candidate_date and (not existing_date or candidate_date > existing_date):
                unique[key] = row
    return list(unique.values())


def merge_new_with_accumulated(new_records: list, all_records_dict: dict):
    newly_scraped_ids = set()
    for record in new_records:
        email = (record.get("email") or "").strip().lower()
        phone = re.sub(r"\D", "", record.get("phone") or "")
        key   = email if email else phone
        if not key:
            continue
        existing = all_records_dict.get(key)
        if existing:
            existing_date = parse_appointment_date(existing.get("appointment"))
            new_date      = parse_appointment_date(record.get("appointment"))
            if new_date and (not existing_date or new_date >= existing_date):
                all_records_dict[key] = record
        else:
            all_records_dict[key] = record
        aid = record.get("activity_id", "")
        if aid:
            newly_scraped_ids.add(aid)
    return all_records_dict, newly_scraped_ids


# ── Installation merge ────────────────────────────────────────────────────────

def merge_with_installations(sales_records: list, installations_dict: dict):
    merged = []
    max_install_date = None

    for record in sales_records:
        aid = record.get("activity_id", "").strip()
        if aid in installations_dict:
            inst = installations_dict[aid]
            record["status"]      = "completed"
            record["tag"]         = "recent_tag"
            record["appointment"] = inst.get("installation_date", record.get("appointment", ""))
            inst_date = parse_appointment_date(inst.get("installation_date"))
            if inst_date:
                max_install_date = max(max_install_date, inst_date) if max_install_date else inst_date
            log(f"✏️  Updated {record.get('name')} with installation data")
        merged.append(record)

    max_date_str = format_query_date(max_install_date) if max_install_date else None
    return merged, max_date_str


# ── Tags ──────────────────────────────────────────────────────────────────────

def add_tag_field(rows: list) -> list:
    for row in rows:
        status = (row.get("status") or "").strip().lower().replace(" ", "_")
        row["tags"] = [f"{status}_tag" if status else "unknown_tag"]
        row.pop("tag", None)
    return rows


# ── GHL push limits ───────────────────────────────────────────────────────────

def apply_ghl_push_limits(contacts: list, max_records_config) -> list:
    if not max_records_config:
        return contacts

    by_tag = {}
    for contact in contacts:
        for tag in contact.get("tags", ["unknown_tag"]):
            by_tag.setdefault(tag, []).append(contact)

    limited = []
    for tag, tag_contacts in by_tag.items():
        max_for_tag = max_records_config.get(tag)
        if max_for_tag is None:
            log(f"📤 {tag}: not in config, skipping {len(tag_contacts)} records")
        else:
            to_send = tag_contacts[:max_for_tag]
            limited.extend(to_send)
            log(f"📤 {tag}: sending {len(to_send)}/{len(tag_contacts)} (limit {max_for_tag})")
    return limited


# ── Scheduled record re-scrape ────────────────────────────────────────────────

def rescrape_scheduled_records(ctx: UserContext):
    scheduled = load_scheduled_records(ctx)
    if not scheduled:
        log("ℹ️ No scheduled records to re-scrape")
        return

    log(f"🔄 Checking {len(scheduled)} scheduled records individually...")

    still_scheduled = {}
    promoted = []

    for activity_id, old_record in scheduled.items():
        try:
            log(f"🔍 Checking: {activity_id} ({old_record.get('name', '')})")
            detail = scrape_detail_parallel(activity_id, ctx)

            if not detail:
                log(f"⚠️ No data for {activity_id}, keeping as scheduled")
                still_scheduled[activity_id] = old_record
                continue

            new_status = (detail.get("status") or "").strip().lower()
            updated    = {**old_record, **detail}

            if new_status and new_status != "scheduled":
                log(f"✅ Status changed to '{new_status}' for {activity_id} — promoting")
                promoted.append(updated)
            else:
                log(f"⏸️  Still scheduled: {activity_id}")
                still_scheduled[activity_id] = updated

        except Exception as e:
            log(f"❌ Error checking {activity_id}: {e}")
            still_scheduled[activity_id] = old_record

    save_scheduled_records(ctx, still_scheduled)
    log(f"💾 {len(still_scheduled)} remain scheduled, {len(promoted)} promoted")

    if promoted:
        all_records_dict = load_all_records(ctx)
        for record in promoted:
            email = (record.get("email") or "").strip().lower()
            phone = re.sub(r"\D", "", record.get("phone") or "")
            key   = email if email else phone
            if key:
                all_records_dict[key] = record
        save_all_records(ctx, all_records_dict)
        log(f"✅ Added {len(promoted)} promoted records to accumulated records")


# ── Cleanup ───────────────────────────────────────────────────────────────────

def cleanup_old_files(ctx: UserContext, days: int = 7):
    now = datetime.now()
    for directory in [ctx.path("backups"), "logs"]:
        if not os.path.exists(directory):
            continue
        for filename in os.listdir(directory):
            path = os.path.join(directory, filename)
            if os.path.isfile(path):
                age = now - datetime.fromtimestamp(os.path.getctime(path))
                if age > timedelta(days=days):
                    os.remove(path)
                    log(f"🧹 Deleted old file: {filename}")


# ── Main pipeline ─────────────────────────────────────────────────────────────

class JobStoppedError(Exception):
    pass


def run_pipeline(ctx: UserContext, otp_fn=None, stop_fn=None):
    """
    Run the full scrape → merge → GHL push pipeline for a single user.
    otp_fn:  optional callable(page) passed to the scraper for OTP handling.
    stop_fn: optional callable() — returns True if job should be cancelled.
    """
    def check_stop():
        if stop_fn and stop_fn():
            raise JobStoppedError("Job stopped by user")

    ctx.ensure_dirs()
    log("🚀 Starting pipeline...")

    processed_ids    = load_processed(ctx.data_dir)
    new_processed    = set(processed_ids)
    processed_hashes = load_processed_hashes(ctx.data_dir)
    new_hashes       = dict(processed_hashes)

    pending_end_date     = None
    pending_install_date = None

    # ── Step 1: Scrape sales ──────────────────────────────────────────────
    log("\n📍 STEP 1: Scraping sales data...")
    load_from_file = os.getenv("LOAD_FROM_FILE", "false").lower() in ("1", "true", "yes")

    deduped_sales_file = ctx.path("deduped_sales.json")

    if load_from_file and os.path.exists(deduped_sales_file):
        sales_data = _load_json(deduped_sales_file, [])
        log(f"📂 Loaded {len(sales_data)} deduped sales from file")
    else:
        start_date, end_date = build_date_range(ctx)
        log(f"📅 Scraping range: {start_date} → {end_date}")
        raw_sales = scrape_all(ctx, start_date, end_date, otp_fn=otp_fn, stop_fn=stop_fn)
        log(f"📊 Scraped {len(raw_sales)} rows")
        save_backup(ctx, raw_sales)
        pending_end_date = end_date

        new_deduped = filter_unique_by_email_phone(raw_sales)
        log(f"🔎 Deduped to {len(new_deduped)} unique records")

        is_full_load = os.getenv("FULL_LOAD", "false").lower() in ("1", "true", "yes")

        if is_full_load:
            all_records_dict = {}
            for record in new_deduped:
                email = (record.get("email") or "").strip().lower()
                phone = re.sub(r"\D", "", record.get("phone") or "")
                key   = email if email else phone
                if key:
                    all_records_dict[key] = record
            log(f"🔄 FULL_LOAD: using {len(all_records_dict)} records")
        else:
            all_records_dict = load_all_records(ctx)
            log(f"📂 Loaded {len(all_records_dict)} accumulated records")
            all_records_dict, _ = merge_new_with_accumulated(new_deduped, all_records_dict)

        save_all_records(ctx, all_records_dict)
        sales_data = list(all_records_dict.values())

    _save_json(deduped_sales_file, sales_data)

    check_stop()
    # ── Step 1.5: Re-scrape scheduled records ─────────────────────────────
    log("\n📍 STEP 1.5: Re-scraping scheduled records...")
    rescrape_scheduled_records(ctx)

    check_stop()
    # ── Step 2: Scrape installations ──────────────────────────────────────
    log("\n📍 STEP 2: Scraping installations...")
    deduped_inst_file = ctx.path("deduped_installations.json")

    if load_from_file and os.path.exists(deduped_inst_file):
        inst_list = _load_json(deduped_inst_file, [])
        installations_dict = {i["activity_id"]: i for i in inst_list if i.get("activity_id")}
        log(f"📂 Loaded {len(installations_dict)} installations from file")
    else:
        installations_dict = scrape_installations(ctx)
        log(f"📊 Scraped {len(installations_dict)} installations")

    _save_json(deduped_inst_file, list(installations_dict.values()))

    check_stop()
    # ── Step 3: Merge ─────────────────────────────────────────────────────
    log("\n📍 STEP 3: Merging installations with sales...")
    merged_data, max_install_date = merge_with_installations(sales_data, installations_dict)
    log(f"🔗 Merged {len(installations_dict)} installations into {len(sales_data)} sales records")

    if max_install_date:
        pending_install_date = max_install_date

    check_stop()
    # ── Step 4: Tags ──────────────────────────────────────────────────────
    log("\n📍 STEP 4: Adding tags...")
    final_data = add_tag_field(merged_data)
    log(f"🏷️  Tagged {len(final_data)} records")
    _save_json(ctx.path("merged_final.json"), final_data)

    # ── Step 4.5: Extract scheduled records ───────────────────────────────
    log("\n📍 STEP 4.5: Extracting scheduled records...")
    non_scheduled = []
    scheduled_dict = {}

    for record in final_data:
        if (record.get("status") or "").strip().lower() == "scheduled":
            aid = record.get("activity_id")
            if aid:
                scheduled_dict[aid] = record
            log(f"📋 Extracted scheduled: {record.get('name')} ({aid})")
        else:
            non_scheduled.append(record)

    _save_json(ctx.path("merged_final.json"), non_scheduled)
    save_scheduled_records(ctx, scheduled_dict)
    log(f"💾 {len(scheduled_dict)} scheduled extracted, {len(non_scheduled)} kept for GHL")

    check_stop()
    # ── Step 5: Send to GHL ───────────────────────────────────────────────
    log("\n📍 STEP 5: Sending to GHL...")

    if ctx.enable_ghl_push:
        contacts_to_send = [
            row for row in final_data
            if has_record_changed(row.get("activity_id", ""), row, processed_hashes)
        ]

        max_records_config = None  # No per-tag limits by default

        if contacts_to_send:
            log(f"📊 {len(contacts_to_send)} contacts eligible")
            limited = apply_ghl_push_limits(contacts_to_send, max_records_config)

            if limited:
                send_to_ghl(limited, ctx)
                for contact in limited:
                    aid = contact.get("activity_id", "").strip()
                    new_processed.add(aid)
                    new_hashes[aid] = compute_record_hash(contact)

                save_processed(ctx.data_dir, new_processed)
                save_processed_hashes(ctx.data_dir, new_hashes)
                log(f"✅ Sent {len(limited)}/{len(contacts_to_send)} contacts to GHL")
            else:
                log("ℹ️ No contacts after applying limits")
        else:
            log("ℹ️ No new or changed contacts to send")
    else:
        log("⏸️ GHL push disabled")

    # ── Save date checkpoints only on full success ────────────────────────
    if pending_end_date:
        _save_json(ctx.path("last_date.json"), {"last_end_date": pending_end_date})
        log(f"🗓️  Saved last end date: {pending_end_date}")

    if pending_install_date:
        _save_json(ctx.path("installation_last_date.json"), {"max_installation_date": pending_install_date})
        log(f"🗓️  Saved max installation date: {pending_install_date}")

    cleanup_old_files(ctx)
    log(f"\n✅ COMPLETE. Processed {len(final_data)} final records")
