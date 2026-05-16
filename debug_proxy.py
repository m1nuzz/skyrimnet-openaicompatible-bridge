# SkyrimNet Robust Debug Proxy - Optimized for Performance
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

# Persistent session for connection pooling (faster)
SESSION = requests.Session()

# Shared state
ACTIVE_REQUESTS = 0
LOCK = threading.Lock()

def get_ts():
    return datetime.now().strftime("%H:%M:%S.%f")

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/v1" or self.path == "/v1/chat/completions":
            self.handle_chat_completions()
        else:
            self.send_error(404, "Not Found")

    def handle_chat_completions(self):
        global ACTIVE_REQUESTS
        start_time = time.time()
        
        with LOCK:
            ACTIVE_REQUESTS += 1
            current_active = ACTIVE_REQUESTS
        
        try:
            # 1. Read body (Fast)
            content_length = int(self.headers.get('Content-Length', 0))
            raw_data = self.rfile.read(content_length)
            
            # 2. Forward IMMEDIATELY to minimize lag for the mod
            # We clean it before forwarding to avoid 400 errors
            data_to_clean = raw_data
            if self.headers.get('Content-Encoding') == 'gzip':
                data_to_clean = zlib.decompress(raw_data, 16+zlib.MAX_WBITS)
            
            try:
                body = json.loads(data_to_clean.decode('utf-8'))
                body_is_json = True
            except:
                body = {}
                body_is_json = False

            forward_data = raw_data
            if body_is_json:
                cleaned = body.copy()
                unsupported = ["provider", "reasoning", "frequency_penalty", "presence_penalty", "logit_bias"]
                for f in unsupported: cleaned.pop(f, None)
                forward_data = json.dumps(cleaned).encode('utf-8')

            # Headers
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            headers['Connection'] = 'close'
            
            url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
            if API_KEY_LOCAL:
                headers['Authorization'] = f"Bearer {API_KEY_LOCAL}"
                url += f"?key={API_KEY_LOCAL}"
            
            # Perform network call
            resp = SESSION.post(url, headers=headers, data=forward_data, timeout=120)
            
            # 3. Clean Response
            response_text = resp.text
            if "<thought>" in response_text:
                response_text = re.sub(r'<thought>.*?</thought>', '', response_text, flags=re.DOTALL).strip()
            response_bytes = response_text.encode('utf-8')

            # 4. Send back to mod as fast as possible
            try:
                self.send_response(resp.status_code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response_bytes)))
                self.send_header('Connection', 'close')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(response_bytes)
                self.wfile.flush()
            except:
                pass

            # 5. Log everything to console (after the mod got its response)
            duration = time.time() - start_time
            with LOCK:
                print(f"\n{'='*30} REQUEST START {'='*30}")
                print(f"[{get_ts()}] [Active: {current_active}] POST {self.path} ({len(raw_data)} bytes)")
                
                print("HEADERS:")
                for h, v in self.headers.items():
                    print(f"  {h}: {v}")

                if body_is_json:
                    print(f"\nRAW JSON BODY:\n{json.dumps(body, indent=2, ensure_ascii=False)}")
                else:
                    print(f"\nRAW BODY (Not JSON):\n{data_to_clean.decode('utf-8', errors='replace')}")
                
                print(f"\n{'-'*20} API RESPONSE {'-'*20}")
                print(f"[{get_ts()}] STATUS: {resp.status_code} | LATENCY: {duration:.2f}s")
                print(f"RESPONSE BODY:\n{response_text}")
                print(f"{'='*31} REQUEST END {'='*31}\n")

        except Exception as e:
            with LOCK:
                print(f"[{get_ts()}] ERROR: {e}")
            try: self.send_error(500, str(e))
            except: pass
        finally:
            with LOCK:
                ACTIVE_REQUESTS -= 1

    def log_message(self, format, *args): return

def kill_port(port):
    print(f"Cleaning port {port}...")
    cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}\""
    subprocess.run(cmd, shell=True)
    time.sleep(1)

if __name__ == "__main__":
    kill_port(PORT)
    print(f"Starting Optimized Debug Proxy on port {PORT}")
    print(f"Target: {FORWARD_TO}")
    
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
