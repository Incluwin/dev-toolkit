"""A tiny stand-in server for trying the check runner: `python mock_server.py <dir>`.

Serves GET /status, GET /items and POST /items on a free port, appends a line to <dir>/server.log per
request, and writes <dir>/stack.json (the file check.py reads). Stops when <dir>/stop appears."""
import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
log, token, items = root / "server.log", secrets.token_hex(8), []
log.write_text("INFO boot complete\n", encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        if self.headers.get("Authorization") != f"Bearer {token}":
            return self.reply(401, {"ok": False})
        with log.open("a", encoding="utf-8") as f:
            f.write(f"INFO {self.command} {self.path}\n")
        if self.command == "POST" and self.path == "/items":
            item = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            if "name" not in item:
                return self.reply(200, {"ok": False, "error": "name required"})
            items.append(item)
            return self.reply(200, {"ok": True, "count": len(items)})
        if self.path == "/items":
            return self.reply(200, {"items": items})
        if self.path == "/status":
            return self.reply(200, {"state": "up", "version": [1, 2, 3]})
        self.reply(404, {"ok": False})

    do_GET = do_POST = handle_any

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
(root / "stack.json").write_text(json.dumps({"baseUrl": f"http://127.0.0.1:{server.server_port}", "token": token,
                                             "logFile": str(log), "branch": "selftest", "commit": "mock"}))
threading.Thread(target=server.serve_forever, daemon=True).start()
while not (root / "stop").exists():
    threading.Event().wait(0.2)
