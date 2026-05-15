# SkyrimNet Provider Bridge - Project Instructions

## Architecture & Infrastructure
- **Purpose**: A bridge between the SkyrimNet mod and various AI providers (Gemini, Mistral, OpenAI, Anthropic, etc.).
- **Ports**:
  - **8080**: Occupied by `SkyrimSE.exe` (SkyrimNet mod internal server/UI).
  - **4000**: Used by this Bridge service.
- **Workflow**: `Skyrim mod` -> `Bridge (4000)` -> `AI Provider API`.

## Key Features & Logic
- **Multimodal Support**: SkyrimNet sends complex requests (lists of content with base64 images). `server.py` has robust manual parsing to extract text for providers that need it.
- **Catch-all Model Mapping**: If a requested model alias is not in `MODEL_MAP`, the bridge defaults to `gemini:gemini-1.5-flash` instead of returning a 400 error.
- **Endpoint Aliasing**: The bridge handles both `POST /v1/chat/completions` and `POST /v1` to accommodate SkyrimNet's request format.

## How to Run
- **Windows**: Just double-click `run_bridge.bat`. It will automatically handle `uv venv`, install dependencies, and start the server.
- **Manual**: `uv run python server.py`.

## Automated E2E Testing
- **Tool**: Playwright (Python).
- **Venv**: Use `.venv_e2e` for running tests (`.\.venv_e2e\Scripts\python.exe click_test.py`).
- **Mechanism**: The test script opens a browser, navigates to `http://localhost:8080/test`, and clicks the **🚀 Test LLM** button.
- **Verification**: Success is confirmed when the "Success" state appears on the SkyrimNet test page.
- **Requirement**: Skyrim must be running with the mod active on port 8080 for E2E tests to pass.

## Workflow Hooks
- **Location**: `.gemini/settings.json`.
- **Trigger**: `AfterTool` (write_file, replace).
- **Action**: Runs `run_e2e.ps1`, which starts the bridge, executes the Playwright test, and shuts down the bridge.
- **Golden Rule**: `run_e2e.ps1` must only output JSON to `stdout` for the hook to work correctly. Debugging info goes to `stderr`.

## Environment Setup (.env)
- Use `API_KEY` for a universal token.
- Provider-specific keys (e.g., `GEMINI_API_KEY`) have priority.
- `CUSTOM_BASE_URL` is available for local providers (Ollama, LM Studio).
