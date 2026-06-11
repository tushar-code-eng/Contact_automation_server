from playwright.sync_api import sync_playwright
import re
import json
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from pipeline_logger import log, error
from user_context import UserContext

SESSION_MAX_AGE_HOURS = int(os.getenv("SESSION_MAX_AGE_HOURS", "6"))
PROGRESS_SAVE_EVERY = int(os.getenv("PROGRESS_SAVE_EVERY", "15"))
PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "120000"))
DETAIL_SCRAPE_THREADS = int(os.getenv("DETAIL_SCRAPE_THREADS", "15"))


# ── Session management ──────────────────────────────────────────────────────

def is_session_expired(ctx: UserContext) -> bool:
    if not os.path.exists(ctx.session_file):
        return True
    try:
        file_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(ctx.session_file))
        if file_age > timedelta(hours=SESSION_MAX_AGE_HOURS):
            log(f"⏰ Session is {file_age.total_seconds()/3600:.1f}h old (max: {SESSION_MAX_AGE_HOURS}h)")
            return True
        log(f"✅ Session is fresh ({file_age.total_seconds()/3600:.1f}h old)")
        return False
    except Exception as e:
        log(f"⚠️ Error checking session age: {e}")
        return True


def delete_expired_session(ctx: UserContext):
    if os.path.exists(ctx.session_file):
        try:
            os.remove(ctx.session_file)
            log(f"🗑️  Deleted expired session")
        except Exception as e:
            log(f"❌ Error deleting session: {e}")


def login_and_save_session(browser_context, page, ctx: UserContext, otp_fn=None):
    """
    Log in to PRPT.
    otp_fn: optional callable(page, needs_code: bool).
            needs_code=True  → TOTP code input is shown, block for user's 6-digit code.
            needs_code=False → push notification MFA, just wait for phone approval.
            If otp_fn is None, falls back to a 45-second blind wait.
    """
    log("🔐 Logging in...")

    page.goto("https://prpt.todaysales.us/login")
    page.wait_for_timeout(2000)

    page.locator("text=Login With Active Directory").click()
    page.wait_for_timeout(3000)

    page.locator('input[type="email"]').fill(ctx.prpt_username)
    page.locator('input[type="submit"]').click()
    page.wait_for_timeout(2000)

    page.locator('input[type="password"]').fill(ctx.prpt_password)
    page.locator('input[type="submit"]').click()
    page.wait_for_timeout(3000)

    # If a verification method selection page appears, click the Text/SMS option
    sms_selector = "div[data-value='OneWaySMS'], li[data-value='OneWaySMS']"
    if page.locator(sms_selector).count() > 0:
        log("📱 Verification method page detected — clicking Text/SMS...")
        page.locator(sms_selector).first.click()
        page.wait_for_timeout(3000)

    # OTP input should now be visible — wait for user to enter it via our website
    log("📲 OTP sent — waiting for code to be entered on website...")

    if otp_fn:
        otp_fn(page, needs_code=True)
    else:
        log("⏳ Waiting 45s for manual MFA completion...")
        page.wait_for_timeout(45000)

    # Dismiss "Stay signed in?" prompt if it appears
    if page.locator("#idSIButton9").count() > 0:
        page.locator("#idSIButton9").click()

    browser_context.storage_state(path=ctx.session_file)
    log("✅ Session saved!")


# ── Helpers ─────────────────────────────────────────────────────────────────

def normalize_phone(phone):
    return re.sub(r"\D", "", phone or "")


def parse_appointment_date(value):
    if not value:
        return None
    for fmt in ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%m-%d-%Y"]:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def load_field_config():
    with open("field_config.json") as f:
        return json.load(f)


def build_report_url(ctx: UserContext, start_date, end_date):
    return (
        f"https://prpt.todaysales.us/reports/salesrep/activities/recent"
        f"?repid={ctx.prpt_rep_id}&startdate={start_date}&enddate={end_date}"
    )


# ── Detail scraping ──────────────────────────────────────────────────────────

def scrape_detail(page, activity_id):
    url = f"https://prpt.todaysales.us/reports/salesrep/customerhistory?activityid={activity_id}"
    page.goto(url)
    page.wait_for_timeout(2000)

    return page.evaluate(r"""
        () => {
            function clean(v){ return (v||'').trim(); }

            function getDlField(keyword){
                const rows = Array.from(document.querySelectorAll('dl.row'));
                for(const row of rows){
                    const label = row.querySelector('dt')?.innerText?.toLowerCase() || '';
                    if(label.includes(keyword)){
                        return clean(row.querySelector('dd')?.innerText);
                    }
                }
                return '';
            }

            function getEmail(){ return getDlField('email'); }

            function getCellValue(td){
                if(!td) return '';
                return clean(td.dataset.expValue || td.innerText);
            }

            function getPrimaryQuoteRow(){
                const headers = Array.from(document.querySelectorAll('thead th'))
                    .map(th => th.innerText.replace(/\s+/g,' ').trim().toLowerCase());

                const quoteIdx    = headers.findIndex(h => h.includes('quote option') || h.includes('quote'));
                const primaryIdx  = headers.findIndex(h => h.includes('is primary') || h.includes('primary'));
                const contractIdx = headers.findIndex(h => h.includes('contract total'));

                for(const tr of Array.from(document.querySelectorAll('tbody > tr'))){
                    if(tr.classList.contains('collapse')) continue;
                    const tds = tr.querySelectorAll('td');
                    const isPrimary = primaryIdx >= 0 ? getCellValue(tds[primaryIdx]).toLowerCase() : '';
                    const button = tr.querySelector('button[data-toggle="collapse"]');
                    const collapseTarget = button ? (button.dataset.target || button.getAttribute('data-target')) : '';
                    if(isPrimary.includes('true')){
                        return {
                            quote_option: getCellValue(tds[quoteIdx]),
                            is_primary: getCellValue(tds[primaryIdx]),
                            contract_total: getCellValue(tds[contractIdx]),
                            collapse_target: collapseTarget
                        };
                    }
                }
                return { quote_option:'', is_primary:'', contract_total:'', collapse_target:'' };
            }

            function getQuoteDetailFields(targetSelector){
                const empty = { area:'', product_line:'', series:'', style:'' };
                if(!targetSelector) return empty;
                const collapseRow = document.querySelector(targetSelector);
                if(!collapseRow) return empty;

                const summaryRows = Array.from(
                    collapseRow.querySelectorAll(':scope > td > table > tbody > tr')
                );

                const areas=[], productLines=[], seriesList=[], styles=[];
                for(const tr of summaryRows){
                    const cells = Array.from(tr.querySelectorAll(':scope > td[data-exp-col]'));
                    if(cells.length < 4) continue;
                    const areaButton = cells[0].querySelector('button');
                    const area        = (areaButton ? areaButton.innerText : getCellValue(cells[0])).trim();
                    const productLine = (cells[1].dataset.expValue || cells[1].innerText).trim();
                    const series      = (cells[2].dataset.expValue || cells[2].innerText).trim();
                    const style       = (cells[3].dataset.expValue || cells[3].innerText).trim();
                    if(area) areas.push(area);
                    if(productLine) productLines.push(productLine);
                    if(series) seriesList.push(series);
                    if(style) styles.push(style);
                }
                const unique = arr => [...new Set(arr.filter(Boolean))];
                return {
                    area:         unique(areas).join(' | '),
                    product_line: unique(productLines).join(' | '),
                    series:       unique(seriesList).join(' | '),
                    style:        unique(styles).join(' | ')
                };
            }

            const primaryQuote = getPrimaryQuoteRow();
            const quoteDetails = getQuoteDetailFields(primaryQuote.collapse_target);
            return {
                email:          getEmail(),
                status:         getDlField('status'),
                quote_option:   primaryQuote.quote_option,
                is_primary:     primaryQuote.is_primary,
                contract_total: primaryQuote.contract_total,
                area:           quoteDetails.area,
                product_line:   quoteDetails.product_line,
                series:         quoteDetails.series,
                style:          quoteDetails.style
            };
        }
    """)


def scrape_detail_parallel(activity_id, ctx: UserContext):
    """Scrape one activity's detail page in its own Playwright instance."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = (
                browser.new_context(storage_state=ctx.session_file)
                if os.path.exists(ctx.session_file)
                else browser.new_context()
            )
            page = context.new_page()
            try:
                page.set_default_navigation_timeout(PLAYWRIGHT_TIMEOUT_MS)
                page.set_default_timeout(PLAYWRIGHT_TIMEOUT_MS)
            except Exception:
                pass
            detail = scrape_detail(page, activity_id)
            browser.close()
            return detail
    except Exception as e:
        log(f"❌ Error scraping detail for {activity_id}: {e}")
        return {}


# ── Summary table extraction ─────────────────────────────────────────────────

def extract_rows(page):
    return page.evaluate(r"""
        () => {
            const headers = Array.from(document.querySelectorAll('thead th'))
              .map(th => th.innerText.replace(/\s+/g, ' ').trim().toLowerCase());

            const fallback = {
                activity_id: 0, self_gen: 2, opportunity_id: 3,
                contract_number: 4, appointment: 5, status: 6,
                name: 8, phone: 9, alternate_phone: 10, address: 11
            };
            const labels = {
                activity_id: 'activity id', self_gen: 'self gen',
                opportunity_id: 'opportunity id', contract_number: 'contract number',
                appointment: 'appointment date', status: 'status',
                name: 'customer name', phone: 'primary phone',
                alternate_phone: 'alternate phone', address: 'address'
            };
            const getIndex = key => {
                const idx = headers.findIndex(h => h.includes(labels[key]));
                return idx >= 0 ? idx : fallback[key];
            };

            return Array.from(document.querySelectorAll('tbody tr')).map(tr => {
                const tds = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                const selfGenCell = tr.querySelectorAll('td')[getIndex('self_gen')];
                const isSelfGen = selfGenCell
                    ? selfGenCell.querySelector('.bi-check-circle-fill') !== null : false;
                const addressIdx  = getIndex('address');
                const addressCell = tr.querySelectorAll('td')[addressIdx];
                let addressText = '';
                if(addressCell){
                    const span = addressCell.querySelector('span.text-truncate');
                    addressText = span ? span.innerText.trim() : addressCell.innerText.trim();
                }
                return {
                    activity_id:     tds[getIndex('activity_id')] || '',
                    self_gen:        isSelfGen,
                    opportunity_id:  tds[getIndex('opportunity_id')] || '',
                    appointment:     tds[getIndex('appointment')] || '',
                    status:          tds[getIndex('status')] || '',
                    contract_number: tds[getIndex('contract_number')] || '',
                    name:            tds[getIndex('name')] || '',
                    phone:           tds[getIndex('phone')] || '',
                    alternate_phone: tds[getIndex('alternate_phone')] || '',
                    address:         addressText
                };
            });
        }
    """)


# ── Main scrape ──────────────────────────────────────────────────────────────

def scrape_all(ctx: UserContext, start_date, end_date, otp_fn=None):
    field_config = load_field_config()
    progress_backup_dir = os.path.join(ctx.data_dir, "backups")
    latest_scrape_file  = ctx.path("latest_scrape.json")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        context = browser.new_context()
        log("🔐 Need to login")

        page = context.new_page()
        try:
            page.set_default_navigation_timeout(PLAYWRIGHT_TIMEOUT_MS)
            page.set_default_timeout(PLAYWRIGHT_TIMEOUT_MS)
        except Exception:
            pass

        login_and_save_session(context, page, ctx, otp_fn=otp_fn)

        report_url = build_report_url(ctx, start_date, end_date)
        log(f"🌐 Loading activity list: {report_url}")
        page.goto(report_url)

        try:
            page.wait_for_selector("table", timeout=10000)
            log("✅ Table loaded")
        except Exception:
            log("⚠️ Table not found, proceeding anyway")

        try:
            page.wait_for_selector("tbody tr", timeout=2000)
        except Exception:
            log("⚠️ No data rows found on page")

        all_rows = []
        seen_ids = set()
        empty_scrolls = 0

        while empty_scrolls < 5:
            rows = extract_rows(page)
            new_count = 0
            for r in rows:
                aid = r.get("activity_id")
                if aid and aid not in seen_ids:
                    seen_ids.add(aid)
                    all_rows.append(r)
                    new_count += 1
                    log(f"➕ Found row: {aid} — {r.get('name','')} — {r.get('phone','')}")

            log(f"Collected {len(all_rows)} rows so far...")
            empty_scrolls = 0 if new_count else empty_scrolls + 1
            if not new_count:
                log(f"⚠️ No new rows (attempt {empty_scrolls}/5), scrolling...")
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1500)

        log(f"🔎 {len(all_rows)} unique rows before detail scraping")
        results = []
        os.makedirs(progress_backup_dir, exist_ok=True)

        log(f"🚀 Scraping details in batches of {DETAIL_SCRAPE_THREADS} threads...")
        total_rows = len(all_rows)

        for batch_start in range(0, total_rows, DETAIL_SCRAPE_THREADS):
            batch_end  = min(batch_start + DETAIL_SCRAPE_THREADS, total_rows)
            batch_rows = all_rows[batch_start:batch_end]
            batch_num  = (batch_start // DETAIL_SCRAPE_THREADS) + 1
            log(f"📦 Batch {batch_num} ({batch_start+1}-{batch_end}/{total_rows})...")

            details_map = {}
            with ThreadPoolExecutor(max_workers=DETAIL_SCRAPE_THREADS) as executor:
                futures = {
                    executor.submit(scrape_detail_parallel, row["activity_id"], ctx): row
                    for row in batch_rows
                }
                for future, row in futures.items():
                    aid = row["activity_id"]
                    try:
                        details_map[aid] = future.result()
                        log(f"✔️ Scraped detail: {aid}")
                    except Exception as e:
                        log(f"❌ Failed detail for {aid}: {e}")
                        details_map[aid] = {}

            batch_results = []
            for row in batch_rows:
                aid    = row["activity_id"]
                detail = details_map.get(aid, {})
                combined = {
                    "activity_id":     row["activity_id"],
                    "self_gen":        row["self_gen"],
                    "opportunity_id":  row["opportunity_id"],
                    "appointment":     row["appointment"],
                    "status":          row["status"],
                    "contract_number": row["contract_number"],
                    "name":            row["name"],
                    "phone":           normalize_phone(row["phone"]),
                    "alternate_phone": normalize_phone(row.get("alternate_phone", "")),
                    "address":         row["address"],
                    **detail
                }
                filtered = {k: v for k, v in combined.items() if field_config.get(k, False)}
                batch_results.append(filtered)
                results.append(filtered)

            try:
                batch_file = os.path.join(
                    progress_backup_dir,
                    datetime.now().strftime(f"%Y-%m-%d_%H-%M-%S_batch_{batch_num}.json")
                )
                with open(batch_file, "w") as f:
                    json.dump(batch_results, f, indent=2)
                log(f"💾 Batch {batch_num} saved ({len(batch_results)} records)")

                with open(latest_scrape_file, "w") as f:
                    json.dump(results, f, indent=2)
                log(f"📋 latest_scrape.json updated: {len(results)} total records")
            except Exception as e:
                log(f"⚠️ Failed to persist progress: {e}")

        browser.close()
        return results


# ── Installations ────────────────────────────────────────────────────────────

def extract_installation_rows(page):
    return page.evaluate(r"""
        () => {
            return Array.from(document.querySelectorAll('tbody tr')).map(tr => {
                const tds = Array.from(tr.querySelectorAll('td'));
                const cells = tds.map(td => td.getAttribute('data-exp-value') || td.innerText.trim());
                return { cells };
            });
        }
    """)


def scrape_installations(ctx: UserContext, start_date=None, end_date=None):
    installations = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        context = browser.new_context()
        log("🔐 Logging in for installations...")

        page = context.new_page()
        try:
            page.set_default_navigation_timeout(PLAYWRIGHT_TIMEOUT_MS)
            page.set_default_timeout(PLAYWRIGHT_TIMEOUT_MS)
        except Exception:
            pass

        login_and_save_session(context, page, ctx)

        try:
            rep = ctx.prpt_rep_id
            if start_date and end_date:
                url = (
                    f"https://prpt.todaysales.us/reports/salesrep/installations/upcoming"
                    f"?repid={rep}&startdate={start_date}&enddate={end_date}"
                )
            else:
                url = (
                    f"https://prpt.todaysales.us/reports/salesrep/installations/upcoming"
                    f"?repid={rep}&startdate=1%2F4%2F2024&enddate=6%2F11%2F2026"
                )

            log(f"📍 Loading installations...")
            page.goto(url, wait_until="networkidle")

            try:
                page.wait_for_selector("table", timeout=10000)
                log("✅ Installations table loaded")
            except Exception:
                log("⚠️ Installations table not found")

            page.wait_for_timeout(2000)
            raw_rows = extract_installation_rows(page)
            log(f"📊 Extracted {len(raw_rows)} installation records")

            for row in raw_rows:
                cells = row.get("cells", [])
                installation_date = (cells[0] if len(cells) > 0 else "").strip()
                order_id          = (cells[1] if len(cells) > 1 else "").strip()
                order_status      = (cells[2] if len(cells) > 2 else "").strip()
                contract_number   = (cells[3] if len(cells) > 3 else "").strip()
                opportunity_id    = (cells[4] if len(cells) > 4 else "").strip()
                activity_id       = (cells[5] if len(cells) > 5 else "").strip()

                if not activity_id or "completed" not in order_status.lower():
                    continue

                new_row = {
                    "activity_id":      activity_id,
                    "order_status":     order_status,
                    "installation_date": installation_date,
                    "order_id":         order_id,
                    "contract_number":  contract_number,
                    "opportunity_id":   opportunity_id,
                }

                existing = installations.get(activity_id)
                if existing:
                    existing_date = parse_appointment_date(existing.get("installation_date", ""))
                    new_date      = parse_appointment_date(installation_date)
                    if new_date and existing_date and new_date > existing_date:
                        installations[activity_id] = new_row
                    elif new_date and not existing_date:
                        installations[activity_id] = new_row
                else:
                    installations[activity_id] = new_row

            log(f"✅ {len(installations)} completed installations (deduped)")

        except Exception as e:
            error(f"❌ Error scraping installations: {e}")
        finally:
            browser.close()

    return installations
