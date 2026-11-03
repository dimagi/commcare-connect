"""A stand-in for CommCare HQ, for trying HQ-backed features locally.

Answers HQ's case API, which assignment, unassignment, exclusion and the Edit Work Area form call,
and ConnectID's push notification endpoint, and prints every request with its body. Point an
opportunity at it with ``generate_microplanning_data --fake-hq``, and ConnectID at it by setting
``CONNECTID_URL=http://localhost:8001/connectid`` for the server and Celery worker.
"""

import json
import re
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.core.management.base import BaseCommand

DEFAULT_PORT = 8001
FAKE_HQ_URL = f"http://localhost:{DEFAULT_PORT}"

CASE_LIST_PATH = re.compile(r"^/a/(?P<domain>[^/]+)/api/case/v2/?$")
CASE_DETAIL_PATH = re.compile(r"^/a/(?P<domain>[^/]+)/api/case/v2/(?P<case_id>[^/]+)/?$")
CONNECTID_MESSAGING_PATH = re.compile(r"^/connectid/messaging/send(_bulk)?/?$")


class Command(BaseCommand):
    help = "Run a stand-in for CommCare HQ on localhost that answers Connect's case updates and prints them"

    def add_arguments(self, parser):
        parser.add_argument(
            "--port", type=int, default=DEFAULT_PORT, help=f"Port to listen on (default {DEFAULT_PORT})"
        )
        parser.add_argument(
            "--fail", action="store_true", help="Answer case updates with HTTP 500, to try Connect's failure handling"
        )

    def handle(self, *args, **options):
        server = FakeHQServer(port=options["port"], fail_case_updates=options["fail"], output=self.stdout)
        mode = "answering case updates with HTTP 500" if options["fail"] else "answering normally"
        self.stdout.write(f"Fake HQ on http://localhost:{server.server_port}, {mode}. Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


class FakeHQServer(ThreadingHTTPServer):
    def __init__(self, *, port, fail_case_updates, output):
        super().__init__(("127.0.0.1", port), FakeHQHandler)
        self.fail_case_updates = fail_case_updates
        self.output = output
        # external_id -> case_id, so a work area keeps one case across updates, as it does on HQ.
        self.case_ids = {}


class FakeHQHandler(BaseHTTPRequestHandler):
    server: FakeHQServer

    def do_GET(self):
        self._answer()

    def do_POST(self):
        self._answer()

    def do_PUT(self):
        self._answer()

    def _answer(self):
        body = self._read_body()
        status, payload = self._route(body)
        self._print_request(body=body, status=status)
        self._send(status=status, payload=payload)

    def _route(self, body):
        path = self.path.split("?")[0]
        if self.command == "POST" and CONNECTID_MESSAGING_PATH.match(path):
            return 200, {"all_success": True, "responses": []}

        list_match = CASE_LIST_PATH.match(path) if self.command == "POST" else None
        detail_match = CASE_DETAIL_PATH.match(path) if self.command == "PUT" else None
        if not (list_match or detail_match):
            return 404, {"error": f"The fake HQ has no route for {self.command} {path}"}
        if self.server.fail_case_updates:
            return 500, {"error": "The fake HQ was started with --fail"}

        if detail_match:
            case = self._case(domain=detail_match["domain"], item=body, case_id=detail_match["case_id"])
            return 200, {"case": case}
        if isinstance(body, list):
            return 200, {"cases": [self._case(domain=list_match["domain"], item=item) for item in body]}
        return 200, {"case": self._case(domain=list_match["domain"], item=body)}

    def _case(self, *, domain, item, case_id=None):
        """A case with exactly the fields ``commcarehq.api.CommCareCase`` takes; any other set breaks it."""
        item = item if isinstance(item, dict) else {}
        external_id = item.get("external_id")
        case_id = case_id or item.get("case_id") or self.server.case_ids.get(external_id) or str(uuid.uuid4())
        if external_id:
            self.server.case_ids[external_id] = case_id
        now = datetime.now(tz=UTC).isoformat()
        return {
            "domain": domain,
            "case_id": case_id,
            "case_type": item.get("case_type", ""),
            "case_name": item.get("case_name", ""),
            "external_id": external_id,
            "owner_id": item.get("owner_id") or "",
            "date_opened": now,
            "last_modified": now,
            "server_last_modified": now,
            "indexed_on": now,
            "closed": False,
            "date_closed": None,
            "properties": item.get("properties", {}),
            "indices": {},
        }

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode(errors="replace")

    def _print_request(self, *, body, status):
        time = datetime.now(tz=UTC).strftime("%H:%M:%S")
        count = f" ({len(body)} items)" if isinstance(body, list) else ""
        entry = f"[{time}] {self.command} {self.path} -> {status}{count}\n"
        if body is not None:
            entry += json.dumps(body, indent=2) + "\n"
        self.server.output.write(entry)
        self.server.output.flush()

    def _send(self, *, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        # _print_request already prints every request, with its body.
        pass
