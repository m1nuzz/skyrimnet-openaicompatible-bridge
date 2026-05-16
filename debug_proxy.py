# SkyrimNet Debug Proxy - Logs all requests to see exactly what SkyrimNet sends
import json
import os
import re
import threading
import zlib
import base64
from datetime import datetime
import subprocess
import time
import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dotenv import load_dotenv

# Load keys from .env
load_dotenv()

# Configuration
PORT = 4000
FORWARD_TO = "https://generativelanguage.googleapis.com/v1beta/openai/"
API_KEY_LOCAL = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY", "")

# Shared state for logging
ACTIVE_REQUESTS = 0
LOCK = threading.Lock()

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/v1" or self.path == "/v1/chat/completions":
            self.handle_chat_completions()
        else:
            self.send_error(404, "Not Found")

    def handle_chat_completions(self):
        global ACTIVE_REQUESTS
        req_id = datetime.now().strftime("%H:%M:%S.%f")
        
        with LOCK:
            ACTIVE_REQUESTS += 1
            current_active = ACTIVE_REQUESTS
        
        print(f"[{req_id}] [Active: {current_active}] Inbound POST {self.path}")
        
        try:
            # 1. Read body
            content_length = int(self.headers.get('Content-Length', 0))
            raw_data = self.rfile.read(content_length)
            
            # 2. Decode for logging/cleaning
            data = raw_data
            if self.headers.get('Content-Encoding') == 'gzip':
                data = zlib.decompress(raw_data, 16+zlib.MAX_WBITS)
            
            try:
                request_body = json.loads(data.decode('utf-8'))
            except:
                request_body = {"raw": "Binary/Malformed"}

            # 3. Strip incompatible fields
            forward_data = raw_data
            if isinstance(request_body, dict):
                cleaned_body = request_body.copy()
                unsupported = ["provider", "reasoning", "frequency_penalty", "presence_penalty", "logit_bias"]
                removed = [f for f in unsupported if cleaned_body.pop(f, None) is not None]
                if removed:
                    print(f"[{req_id}] INFO: Stripped {', '.join(removed)}")
                forward_data = json.dumps(cleaned_body).encode('utf-8')

            # 4. Forward to API
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            headers['Connection'] = 'close'
            
            if API_KEY_LOCAL:
                headers['Authorization'] = f"Bearer {API_KEY_LOCAL}"

            url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
            if API_KEY_LOCAL:
                url += f"?key={API_KEY_LOCAL}"
            
            print(f"[{req_id}] Forwarding...")
            resp = requests.post(url, headers=headers, data=forward_data, timeout=120)
            print(f"[{req_id}] API Response: {resp.status_code}")

            # 5. Clean AI thoughts
            response_text = resp.text
            if "<thought>" in response_text:
                response_text = re.sub(r'<thought>.*?</thought>', '', response_text, flags=re.DOTALL).strip()
                print(f"[{req_id}] INFO: Stripped thought block.")
            
            response_bytes = response_text.encode('utf-8')

            # 6. Send back
            try:
                self.send_response(resp.status_code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response_bytes)))
                self.send_header('Connection', 'close')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(response_bytes)
                print(f"[{req_id}] Done.")
            except (ConnectionAbortedError, ConnectionResetError):
                print(f"[{req_id}] INFO: Client disconnected early.")

        except Exception as e:
            print(f"[{req_id}] ERROR: {e}")
            try:
                self.send_error(500, str(e))
            except:
                pass
        finally:
            with LOCK:
                ACTIVE_REQUESTS -= 1

    def log_message(self, format, *args):
        return

def kill_port(port):
    print(f"Cleaning port {port}...")
    # Robust cleanup for all connections on the specific port
    cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}\""
    subprocess.run(cmd, shell=True)
    time.sleep(1)

if __name__ == "__main__":
    kill_port(PORT)
    print(f"Starting Robust Debug Proxy on port {PORT}")
    print(f"Target: {FORWARD_TO}")
    
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
