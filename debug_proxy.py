# SkyrimNet Master Debug Proxy - The Definitive Version
# Features merged from all 46+ historical commits.
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

# Load environment variables
load_dotenv()

# Configuration
PORT = int(os.getenv("PORT", 4000))
FORWARD_TO = os.getenv("CUSTOM_BASE_URL") or os.getenv("BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai/"
API_KEY_LOCAL = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY", "")

# Persistent session for connection pooling
SESSION = requests.Session()
LOCK = threading.Lock()
ACTIVE_REQUESTS = 0

def get_ts():
    return datetime.now().strftime("%H:%M:%S.%f")

def safe_fix_mojibake(s):
    """Recursively and resiliently fix Russian mojibake in a string."""
    if not isinstance(s, str): return s
    if not ('Ð' in s or 'Ñ' in s or '├Р' in s or '├С' in s): return s
    try:
        b = s.encode('latin-1', errors='replace')
        return b.decode('utf-8')
    except:
        return s

def safe_remangle(s):
    """Convert clean UTF-8 back to broken Latin-1/Cyrillic for Skyrim engine compatibility."""
    if not isinstance(s, str): return s
    try:
        b = s.encode('utf-8')
        return b.decode('latin-1')
    except:
        return s

def process_recursive(obj, func):
    """Apply func to all strings in a nested JSON-like structure."""
    if isinstance(obj, str): return func(obj)
    if isinstance(obj, list): return [process_recursive(i, func) for i in obj]
    if isinstance(obj, dict): return {k: process_recursive(v, func) for k, v in obj.items()}
    return obj

def immersion_filter(text, skip=False):
    """Strip technical AI leakage line-by-line."""
    if not text or skip: return text
    leakage_patterns = [
        r'^(Character|Setting|Context|Tone|Situation|Interlocutor|Current NPC|Current Interlocutor|Roleplay|Draft|Option|Scenario):\s*.*$',
        r'^\d+\.\s*\*\*.*?\*\*.*$', 
        r'^thought\.?$',
        r'^thinking\.?$'
    ]
    lines = text.split('\n')
    filtered_lines = []
    for line in lines:
        is_leakage = False
        l_strip = line.strip()
        if not l_strip: continue
        for pattern in leakage_patterns:
            if re.search(pattern, l_strip, re.IGNORECASE):
                is_leakage = True
                break
        if not is_leakage:
            filtered_lines.append(line)
    return '\n'.join(filtered_lines).strip()

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path in ["/v1", "/v1/chat/completions"]:
            self.handle_chat_completions()
        else:
            self.send_error(404)

    def handle_chat_completions(self):
        global ACTIVE_REQUESTS
        start_time = time.time()
        req_id = get_ts()
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
            
            is_json_task = False
            try:
                request_json = json.loads(decoded_body.decode('utf-8'))
                full_prompt_text = str(request_json.get('messages', []))
                if any(kw in full_prompt_text.lower() for kw in ["json", "memory", "mood", "query", "impression", "describe", "visible", "screenshot", "camera"]):
                    is_json_task = True

                clean_request_json = process_recursive(request_json, safe_fix_mojibake)
                
                unsupported = ["provider", "reasoning", "frequency_penalty", "presence_penalty", "logit_bias"]
                stripped_fields = [f for f in unsupported if clean_request_json.pop(f, None) is not None]
                
                forward_data = json.dumps(clean_request_json).encode('utf-8')
            except:
                clean_request_json = {}
                forward_data = raw_body
                stripped_fields = []

            # 3. Request to API
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            headers['Connection'] = 'close'
            if API_KEY_LOCAL:
                headers['Authorization'] = f"Bearer {API_KEY_LOCAL}"
            
            url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
            if API_KEY_LOCAL: url += f"?key={API_KEY_LOCAL}"

            resp = SESSION.post(url, headers=headers, data=forward_data, timeout=120, stream=True)
            
            # Send initial response to mod
            self.send_response(resp.status_code)
            for h, v in resp.headers.items():
                if h.lower() not in ['content-encoding', 'transfer-encoding', 'content-length', 'connection']:
                    self.send_header(h, v)
            self.send_header('Connection', 'close')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            full_raw_response_content = []
            full_clean_response_text = []
            is_thinking = False

            # 4. Process Stream
            for line in resp.iter_lines():
                if not line: continue
                line_text = line.decode('utf-8', errors='replace')
                full_raw_response_content.append(line_text)
                
                processed_line = line_text
                if line_text.startswith("data: "):
                    data_payload = line_text[6:].strip()
                    if data_payload != "[DONE]":
                        try:
                            chunk_json = json.loads(data_payload)
                            if "choices" in chunk_json:
                                for choice in chunk_json["choices"]:
                                    target_node = choice.get("delta") or choice.get("message")
                                    if target_node:
                                        content = target_node.get("content", "") or target_node.get("reasoning_content", "") or ""
                                        
                                        if not is_json_task:
                                            if not is_thinking and ("<thought>" in content or "<thinking>" in content):
                                                is_thinking = True
                                                content = re.sub(r'<(thought|thinking)>.*', '', content, flags=re.DOTALL)
                                            if is_thinking:
                                                if "</thought>" in content or "</thinking>" in content:
                                                    is_thinking = False
                                                    content = re.sub(r'.*?</(thought|thinking)>', '', content, flags=re.DOTALL)
                                                else:
                                                    content = ""
                                        
                                        if content:
                                            content = immersion_filter(content, skip=is_json_task)
                                            if content:
                                                full_clean_response_text.append(content)
                                        
                                        target_node["content"] = content

                            mangled_chunk = process_recursive(chunk_json, safe_remangle)
                            processed_line = f"data: {json.dumps(mangled_chunk, ensure_ascii=False)}"
                        except:
                            pass
                
                if processed_line.strip() != "data: {}":
                    try:
                        self.wfile.write((processed_line + "\n").encode('latin-1', errors='replace'))
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                        break
            
            try: self.wfile.flush()
            except: pass

            # 5. VERBOSE LOGGING
            duration = time.time() - start_time
            with LOCK:
                print(f"\n{'='*80}")
                print(f"[{req_id}] INCOMING REQUEST [Active: {current_active}]")
                print(f"Path: {self.path} | Model: {clean_request_json.get('model')} | Latency: {duration:.2f}s")
                
                if stripped_fields:
                    print(f"INFO: Stripped fields: {', '.join(stripped_fields)}")

                print("\n--- HEADERS ---")
                for h, v in self.headers.items():
                    print(f"  {h}: {v}")

                print("\n--- STRUCTURED MESSAGES ---")
                messages = clean_request_json.get('messages', [])
                for m in messages:
                    role = m.get('role', 'unknown').upper()
                    content = m.get('content', '')
                    if isinstance(content, list):
                        text_parts = [p.get('text', '') for p in content if p.get('type') == 'text']
                        has_img = any(p.get('type') == 'image_url' for p in content)
                        content_str = " ".join(text_parts)
                        if has_img: content_str += " [IMAGE REDACTED]"
                    else:
                        content_str = content
                    print(f"  [{role}]: {content_str}")

                print(f"\n--- FULL RAW REQUEST JSON ---")
                print(json.dumps(clean_request_json, indent=2, ensure_ascii=False))

                print(f"\n--- CLEAN DIALOGUE RESPONSE ---")
                resp_sample = "".join(full_clean_response_text).strip()
                if resp_sample:
                    print(resp_sample)
                else:
                    print("(EMPTY - AI thinking or filtered)")

                print(f"\n--- RAW API SNIPPET (First 500) ---")
                raw_joined = "".join(full_raw_response_content)
                print(raw_joined[:500] + "...")
                print("="*80 + "\n")

        except Exception as e:
            with LOCK: print(f"[{req_id}] ERROR: {e}")
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
    print(f"Starting Master Debug Proxy on port {PORT}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
