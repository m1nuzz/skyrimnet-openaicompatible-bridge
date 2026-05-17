# SkyrimNet Master Debug Proxy - The Definitive Version
# Audited and consolidated from 46+ commits. Enhanced with line-buffered filtering.
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

# Persistent session
SESSION = requests.Session()
LOCK = threading.Lock()
ACTIVE_REQUESTS = 0

def get_ts():
    return datetime.now().strftime("%H:%M:%S.%f")

def safe_fix_mojibake(s):
    """Recursively and resiliently fix Russian mojibake in a string."""
    if not isinstance(s, str): return s
    if s.startswith("data:image"): return s # Efficiency: skip image data
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

def is_leakage_line(line, skip=False):
    """Check if a single line contains technical AI leakage."""
    if not line or skip: return False
    leakage_patterns = [
        r'^(Character|Setting|Context|Tone|Situation|Interlocutor|Current NPC|Current Interlocutor|Roleplay|Draft|Option|Scenario|Action|Dialogue|Thought|Thinking|Note|Note to self):\s*.*$',
        r'^(Ответ|Реакция|Действие|Мысли|План|Заметка):\s*.*$',
        r'^\d+[\.\)]\s.*$', # Numbered/bullet reasoning
        r'^\d+\.\s*\*\*.*?\*\*.*$', 
        r'^thought\.?$',
        r'^thinking\.?$'
    ]
    l_strip = line.strip()
    for pattern in leakage_patterns:
        if re.search(pattern, l_strip, re.IGNORECASE):
            return True
    return False

def apply_immersion_filter(text, skip=False):
    """Strip technical AI leakage line-by-line from a full block of text."""
    if not text or skip: return text
    lines = text.split('\n')
    filtered_lines = [l for l in lines if not is_leakage_line(l, skip)]
    return '\n'.join(filtered_lines).strip()

class ProxyHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Cache-Control, X-Title, HTTP-Referer')
        self.end_headers()

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
            stream_requested = False
            try:
                request_json = json.loads(decoded_body.decode('utf-8'))
                stream_requested = request_json.get('stream', False)
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

            # 3. Request Preparation
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ['content-length', 'content-encoding', 'host', 'authorization']}
            headers['Content-Type'] = 'application/json'
            headers['Connection'] = 'close'
            if API_KEY_LOCAL:
                headers['Authorization'] = f"Bearer {API_KEY_LOCAL}"
            
            url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
            if API_KEY_LOCAL: url += f"?key={API_KEY_LOCAL}"

            # 4. Request to API
            resp = SESSION.post(url, headers=headers, data=forward_data, timeout=120, stream=stream_requested)
            
            # Prepare Response Headers
            self.send_response(resp.status_code)
            for h, v in resp.headers.items():
                if h.lower() not in ['content-encoding', 'transfer-encoding', 'content-length', 'connection']:
                    self.send_header(h, v)
            self.send_header('Connection', 'close')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            full_raw_response_content = []
            full_clean_response_text = []
            full_reasoning_text = []
            is_thinking = False

            if stream_requested:
                # 5a. STREAMING HANDLER with Line Buffering
                line_buffer = ""
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
                                            # Strip hidden reasoning field
                                            reasoning = target_node.pop("reasoning_content", "")
                                            if reasoning: full_reasoning_text.append(reasoning)

                                            content = target_node.get("content", "") or ""
                                            
                                            # Stateful Thought Stripping
                                            if not is_json_task and content:
                                                if not is_thinking and ("<thought>" in content or "<thinking>" in content):
                                                    is_thinking = True
                                                    content = re.sub(r'<(thought|thinking)>.*', '', content, flags=re.DOTALL)
                                                if is_thinking:
                                                    if "</thought>" in content or "</thinking>" in content:
                                                        is_thinking = False
                                                        content = re.sub(r'.*?</(thought|thinking)>', '', content, flags=re.DOTALL)
                                                    else:
                                                        content = ""
                                            
                                            # Line Buffering for Immersion Filter
                                            if content:
                                                line_buffer += content
                                                if "\n" in line_buffer:
                                                    parts = line_buffer.split("\n")
                                                    valid_parts = []
                                                    for i in range(len(parts) - 1):
                                                        l = parts[i]
                                                        if not is_leakage_line(l, skip=is_json_task):
                                                            valid_parts.append(l)
                                                            full_clean_response_text.append(l + "\n")
                                                    line_buffer = parts[-1]
                                                    target_node["content"] = "\n".join(valid_parts) + ("\n" if valid_parts else "")
                                                else:
                                                    target_node["content"] = "" # Hold until line complete or stream end
                                            else:
                                                target_node["content"] = ""

                                processed_line = f"data: {json.dumps(chunk_json, ensure_ascii=False)}"
                            except: pass
                    
                    if processed_line.strip() != "data: {}":
                        try:
                            self.wfile.write((processed_line + "\n").encode('latin-1', errors='replace'))
                        except: break
                
                # Final Flush of line buffer
                if line_buffer and not is_leakage_line(line_buffer, skip=is_json_task):
                    full_clean_response_text.append(line_buffer)
                    final_chunk = {"choices": [{"delta": {"content": line_buffer}}]}
                    try:
                        self.wfile.write(f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n".encode('latin-1'))
                        self.wfile.write(b"data: [DONE]\n")
                    except: pass

            else:
                # 5b. NON-STREAMING HANDLER (JSON)
                try:
                    resp_json = resp.json()
                    for choice in resp_json.get('choices', []):
                        msg = choice.get('message', {})
                        # Strip reasoning
                        reasoning = msg.pop('reasoning_content', '')
                        if reasoning: full_reasoning_text.append(reasoning)
                        
                        # Filter content
                        content = msg.get('content', '')
                        if content:
                            clean_content = apply_immersion_filter(content, skip=is_json_task)
                            msg['content'] = clean_content
                            full_clean_response_text.append(clean_content)
                    
                    # Remangle for Russian support
                    mangled = process_recursive(resp_json, safe_remangle)
                    self.wfile.write(json.dumps(mangled, ensure_ascii=False).encode('latin-1', errors='replace'))
                    full_raw_response_content.append(json.dumps(resp_json))
                except Exception as e:
                    with LOCK: print(f"Non-stream parse error: {e}")

            try: self.wfile.flush()
            except: pass

            # 6. EXHAUSTIVE DIAGNOSTIC LOGGING
            duration = time.time() - start_time
            with LOCK:
                print(f"\n{'='*80}")
                print(f"[{req_id}] INCOMING REQUEST [Active: {current_active}]")
                print(f"Path: {self.path} | Model: {clean_request_json.get('model')} | Latency: {duration:.2f}s")
                
                print("\n--- HEADERS ---")
                for h, v in self.headers.items():
                    print(f"  {h}: {v}")

                print("\n--- STRUCTURED MESSAGES (Redacted) ---")
                for m in clean_request_json.get('messages', []):
                    role, content = m.get('role', 'unknown').upper(), m.get('content', '')
                    if isinstance(content, list):
                        text_parts = [p.get('text', '') for p in content if p.get('type') == 'text']
                        has_img = any(p.get('type') == 'image_url' for p in content)
                        content_str = " ".join(text_parts) + (" [IMAGE REDACTED]" if has_img else "")
                    else: content_str = content
                    print(f"  [{role}]: {content_str}")

                reasoning_sample = "".join(full_reasoning_text).strip()
                if reasoning_sample:
                    print(f"\n--- AI REASONING (HIDDEN FROM MOD) ---")
                    print(reasoning_sample[:1000] + ("..." if len(reasoning_sample) > 1000 else ""))

                print(f"\n--- FINAL CLEAN DIALOGUE ---")
                resp_sample = "".join(full_clean_response_text).strip()
                print(resp_sample if resp_sample else "(EMPTY - AI thinking or filtered)")

                print(f"\n--- RAW API RESPONSE SNIPPET (First 500) ---")
                raw_joined = "".join(full_raw_response_content)
                print(raw_joined[:500] + ("..." if len(raw_joined) > 500 else ""))
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
