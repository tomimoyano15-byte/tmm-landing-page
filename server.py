#!/usr/bin/env python3
"""
TMM Meta challenger landing server.

Serves static landing pages and handles controlled form submissions:
- HubSpot contact create/update
- HubSpot deal creation + contact association
- Meta Conversions API server Lead event with browser event_id for dedupe
- Local no-secret lead log for proof/debugging
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3470
STATIC_DIR = Path(__file__).resolve().parent
HUBSPOT_BASE = "https://api.hubapi.com"
META_GRAPH = "https://graph.facebook.com/v21.0"
META_PIXEL_ID = "927455176735812"


def load_env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    env_files = (
        Path.home() / ".openclaw/.env",
        Path.home() / "Openclaw/gateway-secrets.env",
        Path.home() / "Openclaw/env.sh",
    )
    for name in names:
        for env_file in env_files:
            if not env_file.exists():
                continue
            try:
                for raw in env_file.read_text(errors="ignore").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    for prefix in (f"{name}=", f"export {name}="):
                        if line.startswith(prefix):
                            return line.split("=", 1)[1].strip().strip("'\"")
            except Exception:
                continue
    return ""


HUBSPOT_TOKEN = load_env_value("HUBSPOT_WRITE_TOKEN")
HUBSPOT_READ_TOKEN = load_env_value("HUBSPOT_TOKEN", "HUBSPOT_WRITE_TOKEN")
META_TOKEN = load_env_value("META_SU_TOKEN", "META_LONG_TOKEN")


def norm_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("0"):
        return "61" + digits[1:]
    return digits


def sha256_norm(value: str) -> str:
    value = (value or "").strip().lower()
    return hashlib.sha256(value.encode()).hexdigest() if value else ""


def hubspot_req(method: str, path: str, token: str, payload: Any | None = None, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    if params:
        path += "?" + urllib.parse.urlencode(params, doseq=True)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(HUBSPOT_BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw[:1000]}
        return e.code, parsed


def meta_capi(data: dict[str, Any], contact_id: str | None) -> tuple[str, dict[str, Any]]:
    if not META_TOKEN:
        return "not_configured", {"error": "META token missing"}
    event_id = data.get("event_id") or f"challenger-{int(time.time())}"
    user_data: dict[str, Any] = {
        "client_ip_address": data.get("client_ip") or "",
        "client_user_agent": data.get("user_agent") or "",
    }
    if data.get("email"):
        user_data["em"] = [sha256_norm(data["email"])]
    if data.get("phone"):
        user_data["ph"] = [hashlib.sha256(norm_phone(data["phone"]).encode()).hexdigest()]
    if data.get("firstname"):
        user_data["fn"] = [sha256_norm(data["firstname"])]
    if data.get("lastname"):
        user_data["ln"] = [sha256_norm(data["lastname"])]
    if data.get("suburb"):
        user_data["ct"] = [sha256_norm(data["suburb"])]
    if data.get("meta_fbp"):
        user_data["fbp"] = data["meta_fbp"]
    if data.get("meta_fbc"):
        user_data["fbc"] = data["meta_fbc"]
    if contact_id:
        user_data["external_id"] = [hashlib.sha256(str(contact_id).encode()).hexdigest()]

    payload = {
        "data": [
            {
                "event_name": "Lead",
                "event_time": int(time.time()),
                "event_id": event_id,
                "action_source": "website",
                "event_source_url": data.get("page_url") or "",
                "user_data": {k: v for k, v in user_data.items() if v},
                "custom_data": {
                    "currency": "AUD",
                    "value": float(data.get("value_aud") or 0),
                    "content_name": "TMM Challenger Quote Request",
                    "landing_page_variant": data.get("landing_page_variant") or "challenger",
                    "utm_source": data.get("utm_source") or "",
                    "utm_campaign": data.get("utm_campaign") or "",
                    "service_type": data.get("service_type") or "",
                    "timing": data.get("timing") or "",
                    "internal_test": bool(data.get("internal_test")),
                },
            }
        ],
        "access_token": META_TOKEN,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{META_GRAPH}/{META_PIXEL_ID}/events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode())
            return f"http_{resp.status}", out
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            out = json.loads(raw)
        except Exception:
            out = {"raw": raw[:1000]}
        return f"http_{e.code}", out
    except Exception as e:
        return "error", {"error": str(e)}


def upsert_contact(data: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    firstname = (data.get("firstname") or "").strip()
    lastname = (data.get("lastname") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    source_detail = " | ".join(
        f"{k}={data.get(k)}" for k in ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "landing_page_variant", "event_id", "meta_fbp", "meta_fbc"] if data.get(k)
    )[:900]
    msg = (
        f"TMM Meta challenger proof lead. Service: {data.get('service_type')} | Property: {data.get('property_type')} | "
        f"Timing: {data.get('timing')} | Suburb: {data.get('suburb')} | Notes: {data.get('message') or ''} | {source_detail}"
    )[:900]
    props = {
        "firstname": firstname,
        "lastname": lastname,
        "email": email,
        "phone": phone,
        "city": data.get("suburb") or "",
        "message": msg,
        "hs_lead_status": "New Lead",
        "lifecyclestage": "opportunity",
        "utm_source": data.get("utm_source") or "meta",
        "utm_medium": data.get("utm_medium") or "paid_social",
        "utm_campaign": data.get("utm_campaign") or "",
        "utm_content": data.get("utm_content") or "",
        "utm_term": data.get("utm_term") or "",
        "hs_facebook_click_id": data.get("meta_fbc") or "",
        "meta_fbp": data.get("meta_fbp") or "",
        "meta_event_id": data.get("event_id") or "",
        "landing_page_variant": data.get("landing_page_variant") or "challenger",
    }
    props = {k: v for k, v in props.items() if v not in (None, "")}
    st, out = hubspot_req("POST", "/crm/v3/objects/contacts", HUBSPOT_TOKEN, {"properties": props})
    if st in (200, 201):
        return "created", str(out.get("id")), out
    if st == 409 and email:
        # Find existing contact by email, then patch it with the latest proof fields.
        body = {"filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}], "properties": list(props.keys()), "limit": 1}
        st2, found = hubspot_req("POST", "/crm/v3/objects/contacts/search", HUBSPOT_READ_TOKEN, body)
        if st2 == 200 and found.get("results"):
            cid = str(found["results"][0]["id"])
            st3, patched = hubspot_req("PATCH", f"/crm/v3/objects/contacts/{cid}", HUBSPOT_TOKEN, {"properties": props})
            return "updated_existing" if st3 in (200, 201) else f"patch_failed_{st3}", cid, patched
    return f"failed_{st}", "", out


def deal_source_props(data: dict[str, Any], dealname: str | None = None) -> dict[str, str]:
    source_detail = " | ".join(
        f"{k}={data.get(k)}" for k in ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "landing_page_variant", "event_id", "meta_fbp", "meta_fbc"] if data.get(k)
    )[:900]
    props: dict[str, str] = {
        "deal_original_source": "Meta Ads",
        "meta_lead_source_detail": source_detail,
        "meta_lead_synced": "true",
        "meta_event_value_aud": str(data.get("value_aud") or ""),
        "contact_original_source": "Meta Ads",
        "contact_source_drilldown_1": data.get("utm_campaign") or "tmm_meta_challenger",
    }
    if dealname:
        props.update({"dealname": dealname, "pipeline": "default", "dealstage": "appointmentscheduled"})
    return {k: v for k, v in props.items() if v not in (None, "")}


def contact_deal_ids(contact_id: str) -> list[str]:
    st, out = hubspot_req("GET", f"/crm/v3/objects/contacts/{contact_id}", HUBSPOT_READ_TOKEN, params={"associations": "deals", "properties": "createdate"})
    if st != 200:
        return []
    return [str(x.get("id")) for x in out.get("associations", {}).get("deals", {}).get("results", []) if x.get("id")]


def patch_existing_deal(data: dict[str, Any], deal_id: str) -> tuple[str, str, dict[str, Any]]:
    st, out = hubspot_req("PATCH", f"/crm/v3/objects/deals/{deal_id}", HUBSPOT_TOKEN, {"properties": deal_source_props(data)})
    if st in (200, 201):
        return "patched_auto_deal", deal_id, out
    return f"patch_auto_failed_{st}", deal_id, out


def create_deal(data: dict[str, Any], contact_id: str) -> tuple[str, str, dict[str, Any]]:
    # HubSpot automation may create the website-lead deal a few seconds after contact creation.
    # Prefer patching that auto-created deal so the challenger does not double-create deals.
    for attempt in range(7):
        ids = contact_deal_ids(contact_id)
        if ids:
            return patch_existing_deal(data, ids[-1])
        if attempt < 6:
            time.sleep(3)

    dealname = f"{data.get('firstname') or 'Meta'} | Challenger Website Lead"
    st, out = hubspot_req("POST", "/crm/v3/objects/deals", HUBSPOT_TOKEN, {"properties": deal_source_props(data, dealname)})
    if st not in (200, 201):
        return f"failed_{st}", "", out
    deal_id = str(out.get("id"))
    assoc_payload = [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]
    st2, assoc = hubspot_req("PUT", f"/crm/v4/objects/deals/{deal_id}/associations/contacts/{contact_id}", HUBSPOT_TOKEN, assoc_payload)
    if st2 not in (200, 201, 204):
        return f"created_assoc_failed_{st2}", deal_id, assoc
    return "created_associated", deal_id, out


class LandingPageHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/api/submit":
            self.handle_form_submission()
        else:
            self.send_error(404)

    def send_json(self, code: int, data: dict[str, Any]):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        if args and "POST" in str(args[0]):
            super().log_message(format, *args)

    def handle_form_submission(self):
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            data = json.loads(body)
        except Exception:
            self.send_json(400, {"success": False, "error": "Invalid JSON"})
            return
        data["client_ip"] = self.client_address[0]
        data["user_agent"] = self.headers.get("User-Agent", "")
        data.setdefault("submitted_at", datetime.now(timezone.utc).isoformat())
        if not (data.get("phone") or data.get("email")):
            self.send_json(400, {"success": False, "error": "Phone or email required"})
            return
        if not HUBSPOT_TOKEN:
            self.send_json(500, {"success": False, "error": "HubSpot write token not configured"})
            return
        contact_status, contact_id, contact_body = upsert_contact(data)
        deal_status = "skipped_no_contact"
        deal_id = ""
        deal_body: dict[str, Any] = {}
        if contact_id:
            deal_status, deal_id, deal_body = create_deal(data, contact_id)
        capi_status, capi_body = meta_capi(data, contact_id or None)
        proof = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "contact_status": contact_status,
            "contact_id": contact_id,
            "deal_status": deal_status,
            "deal_id": deal_id,
            "capi_status": capi_status,
            "capi_body": capi_body,
            "event_id": data.get("event_id"),
            "meta_fbp_present": bool(data.get("meta_fbp")),
            "meta_fbc_present": bool(data.get("meta_fbc")),
            "utm_source": data.get("utm_source"),
            "utm_campaign": data.get("utm_campaign"),
            "landing_page_variant": data.get("landing_page_variant"),
            "internal_test": bool(data.get("internal_test")),
        }
        with (STATIC_DIR / "leads.log").open("a") as f:
            f.write(json.dumps(proof, ensure_ascii=False) + "\n")
        leads_path = STATIC_DIR / "leads.json"
        try:
            leads = json.loads(leads_path.read_text()) if leads_path.exists() else []
        except Exception:
            leads = []
        leads.append(proof)
        leads_path.write_text(json.dumps(leads[-200:], indent=2))
        ok = bool(contact_id and deal_id)
        self.send_json(200 if ok else 500, {"success": ok, "contact_id": contact_id, "contact_status": contact_status, "deal_id": deal_id, "deal_status": deal_status, "capi_status": capi_status, "capi_response": capi_body})


if __name__ == "__main__":
    print(f"TMM Meta challenger server starting on port {PORT}")
    print(f"Serving from: {STATIC_DIR}")
    print(f"HubSpot write: {'connected' if HUBSPOT_TOKEN else 'NOT CONNECTED'}")
    print(f"Meta CAPI token: {'connected' if META_TOKEN else 'NOT CONNECTED'}")
    server = http.server.HTTPServer(("0.0.0.0", PORT), LandingPageHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.shutdown()
