import http.client
import json
import time

from pipeline_logger import log, error
from user_context import UserContext

BACKOFF = [2, 4, 8, 16, 32]


def map_contact_to_ghl(contact: dict, ctx: UserContext) -> dict:
    name_parts = contact.get("name", "").split(" ", 1)
    first_name = name_parts[0] if name_parts else ""
    last_name  = name_parts[1] if len(name_parts) > 1 else ""

    payload = {
        "locationId":                ctx.ghl_location_id,
        "firstName":                 first_name,
        "lastName":                  last_name,
        "name":                      contact.get("name", ""),
        "email":                     contact.get("email", ""),
        "phone":                     contact.get("phone", ""),
        "address1":                  contact.get("address", ""),
        "source":                    "Contact Automation Script",
        "createNewIfDuplicateAllowed": False,
    }

    if contact.get("tags"):
        payload["tags"] = list(contact["tags"])

    custom_fields = []
    for field_name in [
        "activity_id", "self_gen", "appointment", "contract_number",
        "quote_option", "is_primary", "contract_total",
        "area", "product_line", "series", "style",
    ]:
        value = contact.get(field_name)
        if value is None or value == "":
            continue
        key = "appointment_date" if field_name == "appointment" else field_name
        if isinstance(value, bool):
            field_value = str(value).lower()
        elif isinstance(value, list):
            field_value = ", ".join(str(v) for v in value if v is not None)
        else:
            field_value = str(value)
        custom_fields.append({"key": key, "field_value": field_value})

    if contact.get("status") not in (None, ""):
        custom_fields.append({"key": "status", "field_value": str(contact["status"])})

    if custom_fields:
        payload["customFields"] = custom_fields

    return {k: v for k, v in payload.items() if v not in (None, "", [])}


def send_to_ghl(contact_data, ctx: UserContext):
    if isinstance(contact_data, list):
        sent = 0
        for contact in contact_data:
            payload = map_contact_to_ghl(contact, ctx)
            if send_single_contact(None, payload, ctx):
                sent += 1
        log(f"✅ Sent {sent}/{len(contact_data)} contacts to GHL")
    else:
        payload = map_contact_to_ghl(contact_data, ctx)
        if send_single_contact(None, payload, ctx):
            log(f"✅ Sent: {contact_data.get('name')}")


def send_single_contact(conn, payload: dict, ctx: UserContext) -> bool:
    headers = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "Version":       "2021-07-28",
        "Authorization": f"Bearer {ctx.ghl_api_token}",
    }
    last_exc = None
    for attempt, delay in enumerate(BACKOFF, start=1):
        try:
            # Re-create connection on every retry since previous may be closed
            conn = http.client.HTTPSConnection("services.leadconnectorhq.com")
            conn.request("POST", "/contacts/upsert", json.dumps(payload), headers)
            res  = conn.getresponse()
            data = res.read()

            if res.status in (200, 201):
                return True

            # 429 = rate limited — always retry
            # 5xx = server error — retry
            # 4xx (except 429) = bad request — no point retrying
            if res.status == 429 or res.status >= 500:
                msg = data.decode("utf-8")
                if attempt < len(BACKOFF):
                    log(f"⚠️ GHL API {res.status} (attempt {attempt}/{len(BACKOFF)}) — retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                error(f"❌ GHL API Error ({res.status}) after {len(BACKOFF)} attempts: {msg}")
                return False

            error(f"❌ GHL API Error ({res.status}): {data.decode('utf-8')}")
            return False

        except Exception as e:
            last_exc = e
            if attempt < len(BACKOFF):
                log(f"⚠️ GHL request failed (attempt {attempt}/{len(BACKOFF)}): {e} — retrying in {delay}s...")
                time.sleep(delay)
            else:
                error(f"❌ GHL request failed after {len(BACKOFF)} attempts: {e}")
                return False

    return False
