"""Tiny webhook receiver for tests. /ok -> 200 always. /flaky -> 500 on the first request per delivery id, 200 afterwards."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

seen = set()


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        did = self.headers.get("X-ConnectFlow-Delivery")
        first = did not in seen
        seen.add(did)
        code = 500 if (self.path == "/flaky" and first) else 200
        print(json.dumps({"path": self.path, "code": code, "headers": {k: v for k, v in self.headers.items() if k.lower().startswith("x-connectflow")}, "body": body}), flush=True)
        self.send_response(code); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


HTTPServer(("0.0.0.0", 9000), H).serve_forever()
