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

# Shared state
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
        
        log_buffer = []
        log_buffer.append(f"\n" + "="*80)
        log_buffer.append(f"[{req_id}] INCOMING REQUEST [Active: {current_active}]")
        log_buffer.append(f"Path: {self.path}")
        
        try:
            # 1. Read and Decode Body
            content_length = int(self.headers.get('Content-Length', 0))
            raw_data = self.rfile.read(content_length)
            
            data = raw_data
            if self.headers.get('Content-Encoding') == 'gzip':
                data = zlib.decompress(raw_data, 16+zlib.MAX_WBITS)
            
            try:
                body = json.loads(data.decode('utf-8'))
            except:
                body = {"raw": "Binary or Malformed JSON"}

            # 2. Log Request Details
            if isinstance(body, dict):
                log_buffer.append(f"Model: {body.get('model')}")
                log_buffer.append(f"Settings: Temp={body.get('temperature')}, MaxTokens={body.get('max_tokens')}, Stream={body.get('stream')}")
                
                messages = body.get('messages', [])
                log_buffer.append(f"Messages ({len(messages)}):")
                for m in messages:
                    role = m.get('role', 'unknown').upper()
                    content = m.get('content', '')
                    if isinstance(content, list):
                        # Multimodal
                        text_parts = [p.get('text', '') for p in content if p.get('type') == 'text']
                        has_img = any(p.get('type') == 'image_url' for p in content)
                        content_str = " ".join(text_parts)
                        if has_img: content_str += " [IMAGE ATTACHED]"
                    else:
                        content_str = content
                    
                    log_buffer.append(f"  [{role}]: {content_str}")
            
            # 3. Clean and Forward
            forward_data = raw_data
            if isinstance(body, dict):
                cleaned = body.copy()
                unsupported = ["provider", "reasoning", "frequency_penalty", "presence_penalty", "logit_bias"]
                for f in unsupported: cleaned.pop(f, None)
                forward_data = json.dumps(cleaned).encode('utf-8')

            # Headers
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            headers['Connection'] = 'close'
            if API_KEY_LOCAL:
                headers['Authorization'] = f"Bearer {API_KEY_LOCAL}"

            url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
            if API_KEY_LOCAL: url += f"?key={API_KEY_LOCAL}"
            
            # Print request block before starting network wait
            with LOCK:
                print("\n".join(log_buffer))
                print("-" * 40)

            # 4. Perform Request
            resp = requests.post(url, headers=headers, data=forward_data, timeout=120)
            
            # 5. Clean Response
            response_text = resp.text
            if "<thought>" in response_text:
                response_text = re.sub(r'<thought>.*?</thought>', '', response_text, flags=re.DOTALL).strip()
            response_bytes = response_text.encode('utf-8')

            # 6. Send Back & Final Log
            with LOCK:
                print(f"[{req_id}] API STATUS: {resp.status_code}")
                # Optional: print first 200 chars of response
                # print(f"[{req_id}] RESPONSE PREVIEW: {response_text[:200]}...")
                print("="*80 + "\n")

            try:
                self.send_response(resp.status_code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response_bytes)))
                self.send_header('Connection', 'close')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(response_bytes)
            except:
                pass

        except Exception as e:
            with LOCK:
                print(f"[{req_id}] ERROR: {e}")
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
    print(f"Starting Debug Proxy on port {PORT}")
    print(f"Forwarding to: {FORWARD_TO}")
    
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
