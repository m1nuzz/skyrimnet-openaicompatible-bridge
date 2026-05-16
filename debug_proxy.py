# SkyrimNet Debug Proxy - Logs all requests to see exactly what SkyrimNet sends
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import zlib
import base64
from datetime import datetime
import subprocess
import time
import requests

# Configuration
PORT = 4000
FORWARD_TO = "https://generativelanguage.googleapis.com/v1beta/openai/"

# In-memory log of requests
REQUEST_LOG = []

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/v1" or self.path == "/v1/chat/completions":
            self.handle_chat_completions()
        else:
            self.send_error(404, "Not Found")

    def handle_chat_completions(self):
        # Read request content
        content_length = int(self.headers.get('Content-Length', 0))
        raw_data = self.rfile.read(content_length)

        # Try to decode and decompress if needed
        try:
            data = raw_data
            if self.headers.get('Content-Encoding') == 'gzip':
                data = zlib.decompress(raw_data, 16+zlib.MAX_WBITS)

            try:
                data_str = data.decode('utf-8')
                request_body = json.loads(data_str)
            except Exception:
                request_body = {"raw": base64.b64encode(data).decode('ascii'), "error": "Could not decode JSON"}
        except Exception as e:
            request_body = {"raw": base64.b64encode(raw_data).decode('ascii'), "error": str(e)}

        # Log and Print Request
        print(f"\n=== REQUEST INTERCEPTED: {self.path} ===")
        print(json.dumps(request_body, indent=2, ensure_ascii=False))

        # FIX: Strip fields that Google Gemini doesn't support
        if isinstance(request_body, dict):
            cleaned_body = request_body.copy()
            removed = []
            for field in ["provider", "reasoning"]:
                if field in cleaned_body:
                    del cleaned_body[field]
                    removed.append(field)
            if removed:
                print(f"INFO: Removed incompatible fields for Gemini: {', '.join(removed)}")
            
            # Forward the CLEANED body
            forward_data = json.dumps(cleaned_body).encode('utf-8')
        else:
            forward_data = raw_data

        # Forward logic
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host']}
            headers['Content-Type'] = 'application/json'

            if FORWARD_TO:
                url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
                resp = requests.post(url, headers=headers, data=forward_data, timeout=30)

                print(f"\n=== RESPONSE FROM API ({resp.status_code}) ===")
                print(resp.text)

                # Send back to client
                self.send_response(resp.status_code)
                for h, v in resp.headers.items():
                    if h.lower() not in ['content-encoding', 'transfer-encoding', 'content-length']:
                        self.send_header(h, v)
                self.end_headers()
                self.wfile.write(resp.content)
        except Exception as e:
            print(f"Forward error: {e}")
            self.send_error(500, str(e))

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/inspect":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(REQUEST_LOG, indent=2).encode('utf-8'))
        else:
            self.send_error(404)

def kill_port(port):
    print(f"Cleaning port {port}...")
    cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}\""
    subprocess.run(cmd, shell=True)
    time.sleep(1)

if __name__ == "__main__":
    kill_port(PORT)
    print(f"Starting debug proxy on port {PORT}")
    print(f"Forwarding to: {FORWARD_TO}")
    
    server = HTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
