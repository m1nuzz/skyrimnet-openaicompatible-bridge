# SkyrimNet Robust Provider Bridge - Resilient Encoding & Stream Support
import json
import os
import re
import threading
import zlib
import subprocess
from datetime import datetime
import time
import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dotenv import load_dotenv

# Load keys from .env
load_dotenv()

# Configuration
PORT = int(os.getenv("PORT", 4000))
FORWARD_TO = "https://generativelanguage.googleapis.com/v1beta/openai/"
API_KEY_LOCAL = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY", "")

# Persistent session
SESSION = requests.Session()
LOCK = threading.Lock()
ACTIVE_REQUESTS = 0

def get_ts():
    return datetime.now().strftime("%H:%M:%S.%f")

def safe_fix_mojibake(s):
    """Resiliently fix Russian mojibake in a string."""
    if not isinstance(s, str): return s
    if not ('Ð' in s or 'Ñ' in s): return s
    try:
        # Step 1: Force to bytes as if it was Latin-1
        b = s.encode('latin-1', errors='replace')
        # Step 2: Decode as UTF-8
        return b.decode('utf-8')
    except:
        return s

def safe_remangle(s):
    """Resiliently convert clean UTF-8 back to broken Latin-1 for SkyrimNet."""
    if not isinstance(s, str): return s
    try:
        # Step 1: Clean UTF-8 string -> UTF-8 bytes
        b = s.encode('utf-8')
        # Step 2: Bytes -> Latin-1 string (this creates characters like Ð)
        return b.decode('latin-1')
    except:
        return s

def process_recursive(obj, func):
    """Apply func to all strings in a nested JSON-like structure."""
    if isinstance(obj, str): return func(obj)
    if isinstance(obj, list): return [process_recursive(i, func) for i in obj]
    if isinstance(obj, dict): return {k: process_recursive(v, func) for k, v in obj.items()}
    return obj

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path in ["/v1", "/v1/chat/completions"]:
            self.handle_chat_completions()
        else:
            self.send_error(404)

    def handle_chat_completions(self):
        global ACTIVE_REQUESTS
        start_time = time.time()
        with LOCK:
            ACTIVE_REQUESTS += 1
            current_active = ACTIVE_REQUESTS
        
        try:
            # 1. Read Raw Input
            content_length = int(self.headers.get('Content-Length', 0))
            raw_body = self.rfile.read(content_length)
            
            # 2. Decode & Fix for AI
            decoded_body = raw_body
            if self.headers.get('Content-Encoding') == 'gzip':
                decoded_body = zlib.decompress(raw_body, 16+zlib.MAX_WBITS)
            
            try:
                request_json = json.loads(decoded_body.decode('utf-8'))
                # Fix mojibake so Gemini understands Russian
                clean_request_json = process_recursive(request_json, safe_fix_mojibake)
                
                # Strip incompatible fields
                unsupported = ["provider", "reasoning", "frequency_penalty", "presence_penalty", "logit_bias"]
                for f in unsupported: clean_request_json.pop(f, None)
                
                forward_data = json.dumps(clean_request_json).encode('utf-8')
            except:
                clean_request_json = {}
                forward_data = raw_body

            # 3. Headers
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            headers['Connection'] = 'close'
            if API_KEY_LOCAL:
                headers['Authorization'] = f"Bearer {API_KEY_LOCAL}"
            
            url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
            if API_KEY_LOCAL: url += f"?key={API_KEY_LOCAL}"

            # 4. Request to API
            resp = SESSION.post(url, headers=headers, data=forward_data, timeout=120, stream=True)
            
            # Prepare for response to client
            self.send_response(resp.status_code)
            for h, v in resp.headers.items():
                if h.lower() not in ['content-encoding', 'transfer-encoding', 'content-length', 'connection']:
                    self.send_header(h, v)
            self.send_header('Connection', 'close')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            full_clean_response_text = []
            is_thinking = False

            # 5. Process Stream / Body
            for line in resp.iter_lines():
                if not line: continue
                line_text = line.decode('utf-8', errors='replace')
                
                processed_line = line_text
                if line_text.startswith("data: "):
                    data_payload = line_text[6:].strip()
                    if data_payload != "[DONE]":
                        try:
                            chunk_json = json.loads(data_payload)
                            
                            # STATEFUL THOUGHT STRIPPING
                            if "choices" in chunk_json:
                                for choice in chunk_json["choices"]:
                                    target_node = None
                                    if "delta" in choice: target_node = choice["delta"]
                                    elif "message" in choice: target_node = choice["message"]
                                    
                                    if target_node and "content" in target_node:
                                        content = target_node["content"]
                                        
                                        # Detect start of thoughts
                                        if not is_thinking:
                                            if "<thought>" in content or "<thinking>" in content:
                                                is_thinking = True
                                                content = re.sub(r'<(thought|thinking)>.*', '', content, flags=re.DOTALL)
                                        
                                        # Detect end of thoughts
                                        if is_thinking:
                                            if "</thought>" in content or "</thinking>" in content:
                                                is_thinking = False
                                                content = re.sub(r'.*?</(thought|thinking)>', '', content, flags=re.DOTALL)
                                            else:
                                                content = "" # Suppress everything while thinking
                                        
                                        # Store clean text for logs
                                        if content:
                                            full_clean_response_text.append(content)
                                        
                                        target_node["content"] = content

                            # REMANGLE for SkyrimNet
                            mangled_chunk = process_recursive(chunk_json, safe_remangle)
                            # CRITICAL: Use ensure_ascii=False to keep raw characters
                            processed_line = f"data: {json.dumps(mangled_chunk, ensure_ascii=False)}"
                        except:
                            pass
                
                # Send to client
                if processed_line.strip() != "data: {}": # Skip truly empty chunks
                    try:
                        self.wfile.write((processed_line + "\n").encode('latin-1', errors='replace'))
                    except:
                        break # Client left
            
            try:
                self.wfile.flush()
            except:
                pass

            # 6. Log to Console
            duration = time.time() - start_time
            with LOCK:
                print(f"\n{'='*30} REQUEST START {'='*30}")
                print(f"[{get_ts()}] [Active: {current_active}] POST {self.path} ({len(raw_body)} bytes)")
                print(f"Model: {clean_request_json.get('model')} | Latency: {duration:.2f}s")
                # Print clean Russian for user to read
                msg_sample = str(clean_request_json.get('messages', []))
                print(f"Prompt Sample: {msg_sample[:500]}..." if len(msg_sample) > 500 else f"Prompt: {msg_sample}")
                resp_sample = "".join(full_clean_response_text)
                print(f"Clean Response: {resp_sample[:500]}..." if len(resp_sample) > 500 else f"Clean Response: {resp_sample}")
                print(f"{'='*31} REQUEST END {'='*31}\n")

        except Exception as e:
            with LOCK: print(f"[{get_ts()}] ERROR: {e}")
            try: self.send_error(500, str(e))
            except: pass
        finally:
            with LOCK: ACTIVE_REQUESTS -= 1

    def log_message(self, format, *args): return

def kill_port(port):
    print(f"Cleaning port {port}...")
    cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}\""
    subprocess.run(cmd, shell=True)
    time.sleep(1)

if __name__ == "__main__":
    kill_port(PORT)
    print(f"Starting SkyrimNet Provider Bridge on port {PORT}")
    print(f"Forwarding to: {FORWARD_TO}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
