# SkyrimNet Provider Bridge — Project Instructions

## Architecture & Infrastructure
- **Purpose**: Bridge between the SkyrimNet mod and any OpenAI-compatible AI
  provider (Gemini's OpenAI-compat endpoint, OpenAI, Mistral, OpenRouter,
  Groq, Ollama, LM Studio, ...).
- **Implementation**: `ThreadingHTTPServer` in `server.py` with HTTP/1.1
  protocol_version, a shared `requests.Session` HTTP-pool, rolling-buffer
  tag stripping for SSE and strict mojibake repair.
- **Ports**:
  - **8080**: Occupied by `SkyrimSE.exe` (SkyrimNet mod internal server/UI).
  - **4000**: This bridge.
- **Workflow**: `Skyrim mod` -> `Bridge (4000)` -> `AI Provider API`.

## Key Features & Logic
- **Multimodal Support**: Bridge forwards complex requests (lists of content
  with base64 images) unchanged; data: URIs are excluded from mojibake repair.
- **Strict Mojibake Repair (Russian)**: Inbound text only — if the request
  body looks like Latin-1-misdecoded UTF-8 (signature `Ð[\x80-\xBF]` /
  `Ñ[\x80-\xBF]`) it is repaired with strict `encode('latin-1')` +
  `decode('utf-8')`. There is **no** outbound "Latin-1 back-encoding"; the
  bridge always emits clean UTF-8 with an explicit `charset=utf-8` in
  `Content-Type`. Earlier docs claiming "two-way conversion" were inaccurate.
- **Stateful Thought Stripping**: `<thought>...</thought>` and
  `<thinking>...</thinking>` blocks are stripped from dialogue responses,
  including the cases where the tag straddles an SSE chunk boundary
  (`"<tho"` + `"ught>..."`). Unterminated thoughts at end-of-stream are
  dropped, not leaked.
- **Structural-task bypass**: Requests that look like JSON / tool calls
  (any of `response_format`, `tools`, `tool_choice`, or well-known
  keyword indicators) skip both tag-stripping and the immersion filter so
  AI Actions and JSON outputs are never corrupted.
- **Model passthrough**: The bridge does **not** map / rewrite model
  names — whatever the client sends is forwarded as-is. There is no
  `MODEL_MAP` and no Gemini-fallback in code (earlier docs claimed
  otherwise).
- **Endpoint aliasing**: Both `POST /v1/chat/completions` and `POST /v1`
  are accepted.

## How to Run
- **Windows**: Double-click `run_bridge.bat` (uses `uv` for venv + deps).
- **Linux / macOS**: `./run.sh` (creates `.venv`, installs deps,
  runs `python server.py`).
- **Manual**: `uv run python server.py` or `python server.py`.

## Automated E2E Testing
- **Tool**: Playwright (Python).
- **Venv**: `.venv_e2e` (`.\.venv_e2e\Scripts\python.exe click_test.py`).
- **Mechanism**: Opens a browser, navigates to `http://localhost:8080/test`,
  clicks the **Test LLM** button.
- **Verification**: Success is confirmed via a `Success` indicator on the
  SkyrimNet test page (text scoped to the result region, not the whole
  HTML — the previous full-page search produced false positives).
- **Requirement**: Skyrim must be running with the mod active on port 8080.

## Workflow Hooks
- **Location**: `.gemini/settings.json`.
- **Trigger**: `AfterTool` (write_file, replace).
- **Action**: Runs `run_e2e.ps1`, which starts the bridge, polls port 4000
  until LISTENING (up to 20 s), executes the Playwright test, then kills
  whatever is bound to port 4000 (so descendant Python processes spawned by
  `uv run` are not orphaned).
- **Golden Rule**: `run_e2e.ps1` must only output JSON to `stdout` for the
  hook to work correctly. Debug info goes to `stderr`.

## Environment Setup (.env)
- `API_KEY`: universal token sent as `Authorization: Bearer ...`.
- `GEMINI_API_KEY`: takes priority over `API_KEY` (still sent in the same
  header, plus as `?key=...` when the upstream is `generativelanguage.googleapis.com`).
- `CUSTOM_BASE_URL`: takes priority over `BASE_URL`; useful for local
  providers (Ollama, LM Studio).
- `HOST`: bind address (default `127.0.0.1`).
- `PORT`: listening port (default `4000`).
