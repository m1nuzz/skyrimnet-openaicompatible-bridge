# SkyrimNet Master Provider Bridge - UTF-8 Clean Edition (audited)
# Implements P0/P1/P2 fixes from Fix.md:
#  - Safe mojibake repair (strict encode, no errors='replace' destruction)
#  - Rolling-buffer tag stripping that survives split chunks
#  - Force Content-Type charset (UTF-8) on outgoing SSE and JSON
#  - response_format / tools / tool_choice respected as structural-task signal
#  - Hardened immersion filter (no longer destroys numbered lists, Option:, Note:)
#  - Cancel upstream on client disconnect / error
#  - Specific exceptions instead of bare except:
#  - Shared HTTPAdapter pool with bumped concurrency
#  - Cross-platform kill_port (Windows / Linux / macOS)
#  - HTTP/1.1 protocol_version
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import zlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter

# --------------------------------------------------------------------- config

load_dotenv()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", 4000))
FORWARD_TO = (
    os.getenv("CUSTOM_BASE_URL")
    or os.getenv("BASE_URL")
    or "https://generativelanguage.googleapis.com/v1beta/openai/"
)
API_KEY_LOCAL = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY", "")

# Append ?key=... to the upstream URL only for Gemini-compatible endpoints.
# For OpenAI-compatible providers the Authorization header alone is enough,
# and exposing the key in the URL pollutes logs / referers.
USE_QUERY_KEY = "generativelanguage.googleapis.com" in FORWARD_TO

# --------------------------------------------------------------------- logging

logger = logging.getLogger("skyrimnet-bridge")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------- session

SESSION = requests.Session()
# Bump pool to absorb 10+ simultaneous NPC streams without head-of-line block.
_adapter = HTTPAdapter(
    pool_connections=64,
    pool_maxsize=64,
    pool_block=False,
    max_retries=0,
)
SESSION.mount("http://", _adapter)
SESSION.mount("https://", _adapter)

LOCK = threading.Lock()
ACTIVE_REQUESTS = 0


def get_ts():
    return datetime.now().strftime("%H:%M:%S.%f")


# --------------------------------------------------------------------- mojibake

# Mojibake signature: U+00D0 / U+00D1 (Ð / Ñ) followed by a Latin-1 byte that
# is also a valid UTF-8 continuation byte (0x80-0xBF). This is what happens
# when Russian UTF-8 bytes are mis-decoded as Latin-1.
_MOJIBAKE_RE = re.compile(r"[\u00D0\u00D1][\u0080-\u00BF]")
_DATA_URI_PREFIXES = (
    "data:image",
    "data:audio",
    "data:video",
    "data:application",
)


def safe_fix_mojibake(s):
    """Repair Latin-1-misdecoded UTF-8 without destroying clean text.

    The previous implementation used ``errors='replace'`` while encoding to
    Latin-1, which silently replaced *every* real Cyrillic codepoint with
    ``?`` -- the exact ``?????`` bug the function claimed to fix.

    This version:
      1. Bails on non-strings and base64 data: URIs.
      2. Requires the mojibake signature `Ð[\\x80-\\xBF]` or `Ñ[\\x80-\\xBF]`.
      3. Encodes to Latin-1 in *strict* mode; falls back to original on error.
      4. Decodes UTF-8 in *strict* mode; falls back to original on error.
      5. Falls back to original if the repair introduced ``U+FFFD``.
    """
    if not isinstance(s, str) or not s:
        return s
    if s.startswith(_DATA_URI_PREFIXES):
        return s
    if not _MOJIBAKE_RE.search(s):
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    if "\ufffd" in fixed:
        return s
    return fixed


def process_recursive(obj, func):
    """Apply *func* to every string in a nested JSON-like structure."""
    if isinstance(obj, str):
        return func(obj)
    if isinstance(obj, list):
        return [process_recursive(i, func) for i in obj]
    if isinstance(obj, dict):
        return {k: process_recursive(v, func) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------- leakage

# Module-level compiled patterns. Each one requires:
#   - the prefix anchored at start of the stripped line
#   - a colon directly after the prefix
#   - whitespace + at least one non-whitespace char (so a bare "Note:" or
#     "Option:" by itself never matches and dialogue like "Note this down"
#     never matches either).
_LEAKAGE_PATTERNS = (
    re.compile(
        r"^(?:Character|Setting|Context|Tone|Situation|Interlocutor|"
        r"Current NPC|Current Interlocutor|Roleplay|Draft|Scenario|"
        r"Thought|Thinking|Note to self):\s+\S",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:Реакция|Мысли|План|Заметка):\s+\S",
        re.IGNORECASE,
    ),
    # Numbered + bold list item ("1. **Aela** -- does X."): usually an AI
    # option-enumeration leak. Plain numbered lists ("1. Iron sword") are no
    # longer stripped -- they used to be killed by the old `^\d+[\.\)]\s` rule.
    re.compile(r"^\d+\.\s*\*\*.+?\*\*", re.IGNORECASE),
    # Bare "thought" / "thinking" tokens that sneak through.
    re.compile(r"^(?:thought|thinking)\.?$", re.IGNORECASE),
)


def is_leakage_line(line, skip=False):
    """Detect tech-leakage lines while preserving ACTION:, dialogue, lists."""
    if not line or skip:
        return False
    l_strip = line.strip()
    if not l_strip:
        return False
    if l_strip.upper().startswith("ACTION:"):
        return False
    for pat in _LEAKAGE_PATTERNS:
        if pat.search(l_strip):
            return True
    return False


def apply_immersion_filter(text, skip=False):
    """Strip tech-leakage lines from *text* line-by-line."""
    if not text or skip:
        return text
    lines = text.split("\n")
    filtered = [ln for ln in lines if not is_leakage_line(ln, skip)]
    return "\n".join(filtered).strip()


# --------------------------------------------------------------------- tag stripper


class TagStripper:
    """Strip ``<thought>...</thought>`` / ``<thinking>...</thinking>`` across SSE chunks.

    The original implementation suffered three independent bugs:
      A. Prefix before an opening tag was lost when the closing tag didn't
         arrive in the same chunk.
      B. Tags split across chunks (e.g. ``"<tho"`` + ``"ught>secret"``) were
         not recognized and reasoning leaked verbatim to the player.
      C. If the closing tag straddled a chunk boundary the stream got stuck
         in ``is_thinking = True`` forever and dropped all subsequent text.

    This stripper holds a rolling buffer of size (max-tag-len - 1) so any
    incoming partial tag eventually completes. ``flush()`` at end of stream
    emits residual safe text -- or drops it if a thought block was never
    closed.
    """

    OPEN_TAGS = ("<thought>", "<thinking>")
    CLOSE_TAGS = ("</thought>", "</thinking>")
    _MAX_OPEN_LEN = max(len(t) for t in OPEN_TAGS)   # 10: "<thinking>"
    _MAX_CLOSE_LEN = max(len(t) for t in CLOSE_TAGS)  # 11: "</thinking>"

    def __init__(self):
        self._buf = ""
        self._is_thinking = False

    @property
    def is_thinking(self):
        return self._is_thinking

    @staticmethod
    def _find_first(haystack, needles):
        best_idx = -1
        best_tag = None
        for n in needles:
            i = haystack.find(n)
            if i != -1 and (best_idx == -1 or i < best_idx):
                best_idx = i
                best_tag = n
        return best_idx, best_tag

    def feed(self, chunk):
        """Append *chunk* to the buffer and return emitted (visible) text."""
        if not chunk:
            return ""
        self._buf += chunk
        out = []
        while True:
            if self._is_thinking:
                idx, tag = self._find_first(self._buf, self.CLOSE_TAGS)
                if idx == -1:
                    # Keep last (max_close - 1) chars in case the close tag
                    # is split across the upcoming chunk.
                    tail = self._MAX_CLOSE_LEN - 1
                    if len(self._buf) > tail:
                        self._buf = self._buf[-tail:]
                    return "".join(out)
                # Drop everything up to and including the close tag.
                self._buf = self._buf[idx + len(tag):]
                self._is_thinking = False
                continue
            # Not thinking -- look for the next open tag.
            idx, tag = self._find_first(self._buf, self.OPEN_TAGS)
            if idx == -1:
                # Emit everything except the trailing (max_open - 1) chars,
                # which might still grow into an opening tag next chunk.
                tail = self._MAX_OPEN_LEN - 1
                if len(self._buf) > tail:
                    out.append(self._buf[:-tail])
                    self._buf = self._buf[-tail:]
                return "".join(out)
            # Emit prefix, drop the tag, switch into thinking mode.
            if idx > 0:
                out.append(self._buf[:idx])
            self._buf = self._buf[idx + len(tag):]
            self._is_thinking = True

    def flush(self):
        """End-of-stream: emit residual safe text. Drops unterminated thoughts."""
        if self._is_thinking:
            residual = ""
        else:
            residual = self._buf
        self._buf = ""
        self._is_thinking = False
        return residual


# --------------------------------------------------------------------- task detection

_TASK_KEYWORD_INDICATORS = (
    "Output format: `",
    "Respond with ONLY",
    "Generate a memory search query",
    "Determine the next speaker",
    "most appropriate action",
    "emotional state for",
    "Respond now. One line only",
)


def is_json_task(request_json, full_prompt_text):
    """Return True for structural / tool / JSON tasks (must bypass filtering).

    Signals (any one is enough):
      - ``response_format.type`` in {``json_object``, ``json_schema``}
      - non-empty ``tools`` list
      - ``tool_choice`` set to anything other than ``"none"``
      - one of the well-known keyword indicators in the prompt body
    """
    if not isinstance(request_json, dict):
        return False
    rf = request_json.get("response_format")
    if isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema"):
        return True
    tools = request_json.get("tools")
    if isinstance(tools, list) and tools:
        return True
    tc = request_json.get("tool_choice")
    if tc and tc != "none":
        return True
    if any(kw in full_prompt_text for kw in _TASK_KEYWORD_INDICATORS):
        return True
    return False


# --------------------------------------------------------------------- handler


class ProxyHandler(BaseHTTPRequestHandler):
    # HTTP/1.1 so upstream framing stays correct for SSE and chunked transfer.
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Cache-Control, X-Title, HTTP-Referer",
        )
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self):
        if self.path in ("/v1", "/v1/chat/completions"):
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

        upstream_resp = None
        try:
            content_length = int(self.headers.get("Content-Length", 0) or 0)
            raw_body = self.rfile.read(content_length) if content_length else b""

            decoded_body = raw_body
            if self.headers.get("Content-Encoding") == "gzip":
                try:
                    decoded_body = zlib.decompress(raw_body, 16 + zlib.MAX_WBITS)
                except zlib.error as e:
                    logger.warning("gzip decompress failed: %s", e)

            text_body = self._decode_body(decoded_body)

            is_json_task_flag = False
            stream_requested = False
            clean_request_json = {}
            stripped_fields = []
            forward_data = raw_body

            try:
                request_json = json.loads(text_body)
                stream_requested = bool(request_json.get("stream", False))
                full_prompt_text = str(request_json.get("messages", []))
                is_json_task_flag = is_json_task(request_json, full_prompt_text)

                clean_request_json = process_recursive(request_json, safe_fix_mojibake)
                unsupported = (
                    "provider",
                    "reasoning",
                    "frequency_penalty",
                    "presence_penalty",
                    "logit_bias",
                )
                stripped_fields = [
                    f for f in unsupported if clean_request_json.pop(f, None) is not None
                ]
                forward_data = json.dumps(
                    clean_request_json, ensure_ascii=False
                ).encode("utf-8")
            except (json.JSONDecodeError, UnicodeError, TypeError, ValueError) as e:
                logger.warning("Request JSON parse failed (%s); forwarding raw body", e)

            hop_by_hop = {
                "content-length",
                "content-encoding",
                "host",
                "authorization",
                "connection",
                "transfer-encoding",
                "keep-alive",
                "proxy-authenticate",
                "proxy-authorization",
                "te",
                "trailers",
                "upgrade",
            }
            up_headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in hop_by_hop
            }
            up_headers["Content-Type"] = "application/json; charset=utf-8"
            if API_KEY_LOCAL:
                up_headers["Authorization"] = f"Bearer {API_KEY_LOCAL}"

            url = f"{FORWARD_TO.rstrip('/')}/chat/completions"
            if API_KEY_LOCAL and USE_QUERY_KEY:
                url += f"?key={API_KEY_LOCAL}"

            try:
                upstream_resp = SESSION.post(
                    url,
                    headers=up_headers,
                    data=forward_data,
                    timeout=(10, 300),
                    stream=stream_requested,
                )
            except requests.RequestException as e:
                logger.error("[%s] upstream request failed: %s", req_id, e)
                self._send_error_json(
                    502, {"error": {"message": str(e), "type": "upstream_error"}}
                )
                return

            self.send_response(upstream_resp.status_code)
            for h, v in upstream_resp.headers.items():
                if h.lower() in (
                    "content-encoding",
                    "transfer-encoding",
                    "content-length",
                    "connection",
                    "keep-alive",
                    "content-type",
                ):
                    continue
                self.send_header(h, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, no-transform")
            self.send_header("Connection", "close")
            self.close_connection = True

            full_raw_response_content = []
            full_clean_response_text = []
            full_reasoning_text = []
            tag_stripper = TagStripper()

            if stream_requested:
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self._stream_loop(
                    upstream_resp,
                    tag_stripper,
                    is_json_task_flag,
                    full_raw_response_content,
                    full_clean_response_text,
                    full_reasoning_text,
                )
            else:
                self._non_stream(
                    upstream_resp,
                    tag_stripper,
                    is_json_task_flag,
                    clean_request_json,
                    full_raw_response_content,
                    full_clean_response_text,
                    full_reasoning_text,
                )

            duration = time.time() - start_time
            self._log_request(
                req_id,
                current_active,
                duration,
                clean_request_json,
                stripped_fields,
                full_reasoning_text,
                full_clean_response_text,
            )

        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            logger.info("[%s] client disconnected: %s", req_id, e)
        except Exception as e:  # noqa: BLE001 - top-level safety net
            logger.exception("[%s] CRITICAL: %s", req_id, e)
            try:
                self.send_error(500, str(e))
            except (BrokenPipeError, ConnectionError, OSError):
                pass
        finally:
            if upstream_resp is not None:
                try:
                    upstream_resp.close()
                except (requests.RequestException, OSError):
                    pass
            with LOCK:
                ACTIVE_REQUESTS -= 1

    # ---------- helpers ----------

    @staticmethod
    def _decode_body(raw):
        """Decode inbound body as UTF-8 with cp1251 / cp1252 / latin-1 fallback."""
        if not raw:
            return ""
        for enc in ("utf-8", "cp1251", "cp1252", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _send_error_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    def _write_sse_line(self, processed_line):
        """Write one SSE event. Returns False if the client disconnected."""
        try:
            self.wfile.write((processed_line + "\n\n").encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return False

    # ---------- stream branch ----------

    def _stream_loop(
        self,
        upstream_resp,
        tag_stripper,
        is_json_task_flag,
        raw_acc,
        clean_acc,
        reasoning_acc,
    ):
        try:
            for line in upstream_resp.iter_lines():
                if not line:
                    continue
                line_text = line.decode("utf-8", errors="replace")
                raw_acc.append(line_text)

                processed_line = line_text
                emit = True

                if line_text.startswith("data: "):
                    data_payload = line_text[6:].strip()
                    if data_payload == "[DONE]":
                        residual = tag_stripper.flush()
                        if residual and not is_json_task_flag:
                            tail_chunk = {
                                "choices": [{"delta": {"content": residual}}]
                            }
                            tail_line = (
                                f"data: {json.dumps(tail_chunk, ensure_ascii=False)}"
                            )
                            if not self._write_sse_line(tail_line):
                                break
                            clean_acc.append(residual)
                    else:
                        try:
                            chunk_json = json.loads(data_payload)
                        except json.JSONDecodeError:
                            chunk_json = None

                        if chunk_json is not None and "choices" in chunk_json:
                            any_content = False
                            for choice in chunk_json["choices"]:
                                target_node = choice.get("delta") or choice.get("message")
                                if not target_node:
                                    continue
                                reasoning = target_node.pop("reasoning_content", "")
                                if reasoning:
                                    reasoning_acc.append(reasoning)

                                content = target_node.get("content", "") or ""
                                if content:
                                    if is_json_task_flag:
                                        target_node["content"] = content
                                        clean_acc.append(content)
                                        any_content = True
                                    else:
                                        visible = tag_stripper.feed(content)
                                        target_node["content"] = visible
                                        if visible:
                                            clean_acc.append(visible)
                                            any_content = True
                                else:
                                    target_node["content"] = ""

                            has_finish_or_meta = any(
                                c.get("finish_reason") is not None
                                or (c.get("delta") or {}).get("role")
                                or (c.get("delta") or {}).get("tool_calls")
                                or (c.get("message") or {}).get("tool_calls")
                                for c in chunk_json["choices"]
                            )
                            if (
                                not any_content
                                and not has_finish_or_meta
                                and not is_json_task_flag
                            ):
                                emit = False
                            processed_line = (
                                f"data: {json.dumps(chunk_json, ensure_ascii=False)}"
                            )

                if emit and processed_line.strip() != "data: {}":
                    if not self._write_sse_line(processed_line):
                        break
        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.RequestException,
        ) as e:
            logger.warning("upstream stream interrupted: %s", e)

    # ---------- non-stream branch ----------

    def _non_stream(
        self,
        upstream_resp,
        tag_stripper,
        is_json_task_flag,
        clean_request_json,
        raw_acc,
        clean_acc,
        reasoning_acc,
    ):
        try:
            if not upstream_resp.text.strip():
                raise ValueError("Empty upstream response body")
            resp_json = upstream_resp.json()
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("non-stream parse failed: %s", e)
            ctxt = str(clean_request_json).lower()
            fallback_content = (
                "NEUTRAL"
                if "mood" in ctxt
                else "ACTION: None"
                if "action" in ctxt
                else "..."
            )
            fallback = {"choices": [{"message": {"content": fallback_content}}]}
            body = json.dumps(fallback, ensure_ascii=False).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionError, OSError):
                pass
            raw_acc.append(body.decode("utf-8"))
            return

        for choice in resp_json.get("choices", []):
            msg = choice.get("message", {})
            reasoning = msg.pop("reasoning_content", "")
            if reasoning:
                reasoning_acc.append(reasoning)

            content = msg.get("content", "")
            if content:
                if is_json_task_flag:
                    clean_content = content
                else:
                    # Run the entire content through the rolling-buffer
                    # tag-stripper as a single chunk + flush, then immersion
                    # filter. Gives identical semantics to the streaming
                    # path (previously non-stream skipped tag-stripping
                    # entirely).
                    visible = tag_stripper.feed(content) + tag_stripper.flush()
                    clean_content = apply_immersion_filter(visible, skip=False)
                msg["content"] = clean_content
                clean_acc.append(clean_content)

        body = json.dumps(resp_json, ensure_ascii=False).encode("utf-8")
        raw_acc.append(body.decode("utf-8"))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    # ---------- logging ----------

    def _log_request(
        self, req_id, active, duration, req_json, stripped, reasoning, clean
    ):
        try:
            lines = []
            lines.append("=" * 80)
            lines.append(
                f"[{req_id}] INCOMING REQUEST [Active: {active}] "
                f"Path: {self.path} | Model: {req_json.get('model')} | "
                f"Latency: {duration:.2f}s"
            )
            if stripped:
                lines.append(f"Stripped unsupported fields: {stripped}")
            lines.append("--- HEADERS ---")
            for h, v in self.headers.items():
                lines.append(f"  {h}: {v}")
            lines.append("--- STRUCTURED MESSAGES ---")
            for m in req_json.get("messages", []):
                role = m.get("role", "unknown").upper()
                content = m.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        p.get("text", "") for p in content if p.get("type") == "text"
                    ]
                    has_img = any(p.get("type") == "image_url" for p in content)
                    content_str = " ".join(text_parts) + (
                        " [IMAGE REDACTED]" if has_img else ""
                    )
                else:
                    content_str = content
                lines.append(f"  [{role}]: {content_str}")
            reasoning_sample = "".join(reasoning).strip()
            if reasoning_sample:
                lines.append("--- AI REASONING (HIDDEN FROM MOD) ---")
                lines.append(
                    reasoning_sample[:1000]
                    + ("..." if len(reasoning_sample) > 1000 else "")
                )
            resp_sample = "".join(clean).strip()
            lines.append("--- FINAL CLEAN OUTPUT ---")
            lines.append(
                resp_sample if resp_sample else "(EMPTY - AI thinking or filtered)"
            )
            lines.append("=" * 80)
            logger.info("\n%s", "\n".join(lines))
        except Exception as e:  # noqa: BLE001 - logging must never crash a request
            logger.exception("log_request failed: %s", e)

    def log_message(self, format, *args):  # noqa: A002 - silence default access log
        return


# --------------------------------------------------------------------- port cleanup


def kill_port(port):
    """Best-effort: kill any process bound to *port*. Cross-platform."""
    try:
        if sys.platform == "win32":
            cmd = (
                "powershell -NoProfile -Command "
                f"\"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty OwningProcess -Unique | "
                "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }\""
            )
            subprocess.run(cmd, shell=True, timeout=10, check=False)
        else:
            for cmd in (
                f"lsof -ti tcp:{port} 2>/dev/null | xargs -r kill -9 2>/dev/null",
                f"fuser -k {port}/tcp 2>/dev/null",
            ):
                try:
                    subprocess.run(cmd, shell=True, timeout=10, check=False)
                except (subprocess.TimeoutExpired, OSError):
                    continue
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("kill_port(%s) failed: %s", port, e)
    time.sleep(0.5)


# --------------------------------------------------------------------- entrypoint


class _ThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(host=None, port=None, debug=False):
    """Run the bridge. Reused by ``debug_proxy.py`` to avoid a duplicate file."""
    host = host or HOST
    port = port or PORT
    if debug:
        logger.setLevel(logging.DEBUG)
    kill_port(port)
    logger.info(
        "Starting SkyrimNet Bridge on %s:%s -> %s%s",
        host,
        port,
        FORWARD_TO,
        " (debug)" if debug else "",
    )
    server = _ThreadingHTTPServer((host, port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
