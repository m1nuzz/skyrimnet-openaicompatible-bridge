# SkyrimNet Debug Proxy - Logs all requests to see exactly what SkyrimNet sends
import json
import os
import re
import urllib.parse
import zlib
import base64
from datetime import datetime
import subprocess
import time
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from dotenv import load_dotenv

# Load keys from .env
load_dotenv()

# Configuration
PORT = 4000
FORWARD_TO = "https://generativelanguage.googleapis.com/v1beta/openai/"
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
        req_id = datetime.now().strftime("%H:%M:%S.%f")
        print(f"[{req_id}] incoming request...")
        
        # Read request content
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            raw_data = self.rfile.read(content_length)
        except Exception as e:
            print(f"[{req_id}] Error reading body: {e}")
            return

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
        print(f"=== [{req_id}] REQUEST INTERCEPTED: {self.path} ===")
        # print(json.dumps(request_body, indent=2, ensure_ascii=False)) # Hidden for token saving as requested

        # FIX: Strip fields that Google Gemini doesn't support
        forward_data = raw_data
        if isinstance(request_body, dict):
            cleaned_body = request_body.copy()
            removed = []
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
                print(f"[{req_id}] INFO: Stripped: {', '.join(removed)}")
            
            forward_data = json.dumps(cleaned_body).encode('utf-8')

        # Forward logic
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            
            key_to_use = API_KEY_LOCAL
            if key_to_use:
                headers['Authorization'] = f"Bearer {key_to_use}"

            if FORWARD_TO:
                url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
                if key_to_use:
                    url += f"?key={key_to_use}"
                
                print(f"[{req_id}] Forwarding to Gemini...")
                resp = requests.post(url, headers=headers, data=forward_data, timeout=60)
                print(f"[{req_id}] API Response: {resp.status_code}")

                # Clean up response: Remove <thought> blocks
                response_text = resp.text
                if "<thought>" in response_text:
                    response_text = re.sub(r'<thought>.*?</thought>', '', response_text, flags=re.DOTALL).strip()
                    print(f"[{req_id}] INFO: Stripped <thought> block.")
                
                response_bytes = response_text.encode('utf-8')

                # Send back to client
                try:
                    self.send_response(resp.status_code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(response_bytes)))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(response_bytes)
                    print(f"[{req_id}] Done.")
                except Exception as e:
                    print(f"[{req_id}] INFO: Client closed connection early: {e}")
        except Exception as e:
            print(f"[{req_id}] Forward error: {e}")
            try:
                self.send_error(500, str(e))
            except:
                pass

    def log_message(self, format, *args):
        return

def kill_port(port):
    print(f"Cleaning port {port}...")
    cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}\""
    subprocess.run(cmd, shell=True)
    time.sleep(1)

if __name__ == "__main__":
    kill_port(PORT)
    print(f"Starting Multi-threaded Debug Proxy on port {PORT}")
    print(f"Forwarding to: {FORWARD_TO}")
    
    # Using ThreadingHTTPServer to prevent blocking
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
