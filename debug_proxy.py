# SkyrimNet Debug Proxy - Logs all requests to see exactly what SkyrimNet sends
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import zlib
import base64
from datetime import datetime
import subprocess
import time
import requests
from dotenv import load_dotenv

# Load keys from .env
load_dotenv()

# Configuration
PORT = 4000
FORWARD_TO = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Use GEMINI_API_KEY or generic API_KEY from .env
API_KEY_LOCAL = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY", "")

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
        forward_data = raw_data
        if isinstance(request_body, dict):
            cleaned_body = request_body.copy()
            removed = []
            # Extended list of fields unsupported by Google's OpenAI-compatible shim
            unsupported_fields = [
                "provider", "reasoning", 
                "frequency_penalty", "presence_penalty", 
                "logit_bias", "logprobs", "top_logprobs"
            ]
            for field in unsupported_fields:
                if field in cleaned_body:
                    del cleaned_body[field]
                    removed.append(field)
            if removed:
                print(f"INFO: Removed incompatible fields for Gemini: {', '.join(removed)}")
            
            # Forward the CLEANED body
            forward_data = json.dumps(cleaned_body).encode('utf-8')

        # Forward logic
        try:
            # Prepare headers for forwarding
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            
            # Use our LOCAL API KEY if available
            key_to_use = API_KEY_LOCAL
            if key_to_use:
                print(f"INFO: Using API Key from .env ({key_to_use[:8]}...)")
                headers['Authorization'] = f"Bearer {key_to_use}"
            else:
                # Fallback to whatever client sent if .env is empty
                auth = self.headers.get('Authorization')
                if auth:
                    headers['Authorization'] = auth
                    print("INFO: Forwarding client's Authorization header.")

            if FORWARD_TO:
                # Google OpenAI endpoint can take key in URL or Header
                url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
                if key_to_use:
                    url += f"?key={key_to_use}"
                
                resp = requests.post(url, headers=headers, data=forward_data, timeout=30)

                print(f"\n=== RESPONSE FROM API ({resp.status_code}) ===")
                # Clean up response: Remove <thought> blocks which confuse some parsers
                response_text = resp.text
                if "<thought>" in response_text and "</thought>" in response_text:
                    import re
                    response_text = re.sub(r'<thought>.*?</thought>', '', response_text, flags=re.DOTALL).strip()
                    print("INFO: Stripped <thought> block from response.")
                
                # Convert back to bytes
                response_bytes = response_text.encode('utf-8')

                print(response_text)

                # Send back to client
                try:
                    self.send_response(resp.status_code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(response_bytes)))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(response_bytes)
                except (ConnectionAbortedError, ConnectionResetError):
                    print("INFO: Client closed connection before response was sent.")
        except Exception as e:
            if not isinstance(e, (ConnectionAbortedError, ConnectionResetError)):
                print(f"Forward error: {e}")
                try:
                    self.send_error(500, str(e))
                except:
                    pass

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
    if not API_KEY_LOCAL:
        print("WARNING: No API_KEY found in .env! Proxy will use client's key.")
    
    server = HTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
