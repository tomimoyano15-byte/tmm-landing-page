#!/usr/bin/env python3
"""
TMM Landing Page Server
Serves static landing pages and handles form submissions -> HubSpot Contacts API.
No external dependencies required.

Usage: python3 server.py [port]
Default port: 3470
"""

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3470
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value

    env_files = (
        os.path.expanduser("~/.openclaw/.env"),
        os.path.expanduser("~/Openclaw/gateway-secrets.env"),
        os.path.expanduser("~/Openclaw/env.sh"),
    )

    for name in names:
        for env_file in env_files:
            if not os.path.exists(env_file):
                continue
            try:
                with open(env_file) as f:
                    for raw in f:
                        line = raw.strip()
                        if not line or line.startswith("#"):
                            continue
                        prefixes = (f"{name}=", f"export {name}=")
                        if line.startswith(prefixes):
                            return line.split("=", 1)[1].strip().strip("'\"")
                        marker = f"${{{name}:="
                        if marker in line:
                            return line.split(marker, 1)[1].split("}", 1)[0].strip().strip("'\"")
            except Exception:
                continue
    return ""


HUBSPOT_TOKEN = load_env_value("HUBSPOT_WRITE_TOKEN")


class LandingPageHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_POST(self):
        if self.path == "/api/submit":
            self.handle_form_submission()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        if "POST" in str(args[0]):
            super().log_message(format, *args)

    def handle_form_submission(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Invalid JSON"})
            return

        firstname = data.get("firstname", "").strip()
        lastname = data.get("lastname", "").strip()
        email = data.get("email", "").strip()
        phone = data.get("phone", "").strip()
        message = data.get("message", "").strip()
        utm_source = data.get("utm_source", "meta")
        utm_medium = data.get("utm_medium", "paid")
        utm_campaign = data.get("utm_campaign", "")
        utm_content = data.get("utm_content", "")

        if not email and not phone:
            self.send_json(400, {"error": "Email or phone required"})
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        contact_id = "local-only"

        if HUBSPOT_TOKEN:
            source_marker = f"[utm_source={utm_source} utm_medium={utm_medium} utm_campaign={utm_campaign} utm_content={utm_content}]"
            hubspot_payload = {
                "properties": {
                    "firstname": firstname,
                    "lastname": lastname,
                    "email": email,
                    "phone": phone,
                    "message": f"{source_marker} {message}".strip(),
                    "hs_lead_status": "New Lead",
                    "lifecyclestage": "lead",
                }
            }
            try:
                req = urllib.request.Request(
                    "https://api.hubapi.com/crm/v3/objects/contacts",
                    data=json.dumps(hubspot_payload).encode(),
                    headers={
                        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    result = json.loads(resp.read())
                contact_id = result.get("id", "unknown")
                print(f"[LEAD] {firstname} ({email}) -> HubSpot #{contact_id}", flush=True)
            except urllib.error.HTTPError as e:
                error_body = e.read().decode(errors="ignore")
                if e.code == 409:
                    contact_id = "existing"
                    print(f"[LEAD] {email} already exists in HubSpot", flush=True)
                else:
                    print(f"[WARN] HubSpot write failed: {e.code} {error_body}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[WARN] HubSpot error: {e}", file=sys.stderr, flush=True)

        log_line = (
            f"[{timestamp}] LEAD: {firstname} {lastname} | {email} | {phone} | {message[:80]} | "
            f"hubspot={contact_id} | src={utm_source}/{utm_campaign}\n"
        )
        with open(os.path.join(STATIC_DIR, "leads.log"), "a") as f:
            f.write(log_line)

        json_path = os.path.join(STATIC_DIR, "leads.json")
        leads = []
        if os.path.exists(json_path):
            try:
                with open(json_path) as f:
                    leads = json.load(f)
            except (json.JSONDecodeError, IOError):
                leads = []

        leads.append(
            {
                "timestamp": timestamp,
                "firstname": firstname,
                "lastname": lastname,
                "email": email,
                "phone": phone,
                "message": message,
                "hubspot_id": contact_id,
                "utm_source": utm_source,
                "utm_medium": utm_medium,
                "utm_campaign": utm_campaign,
                "utm_content": utm_content,
            }
        )
        with open(json_path, "w") as f:
            json.dump(leads, f, indent=2)

        self.send_json(200, {"success": True, "contact_id": contact_id})


if __name__ == "__main__":
    print(f"TMM Landing Page Server starting on port {PORT}")
    print(f"Serving from: {STATIC_DIR}")
    print(f"HubSpot: {'connected' if HUBSPOT_TOKEN else 'NOT CONNECTED'}")
    print(f"Open: http://localhost:{PORT}")

    server = http.server.HTTPServer(("0.0.0.0", PORT), LandingPageHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.shutdown()
