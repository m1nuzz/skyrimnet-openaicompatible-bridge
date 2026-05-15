# SkyrimNet Provider Bridge - Project Instructions

## Architecture
- This is a bridge between SkyrimNet mod and various AI providers.
- It mimics OpenAI API structure but handles multimodal data and specific provider quirks (like Gemini/Anthropic).
- The mod (SkyrimSE.exe) communicates with this bridge on port 4000.

## Workflow Hooks
- **post-apply**: powershell.exe -NoProfile -ExecutionPolicy Bypass -File run_e2e.ps1

## Testing
- E2E tests require SkyrimSE.exe to be running with SkyrimNet mod active on port 8080.
- Automated via Playwright in `click_test.py`.
- Run manually with `.\.venv_e2e\Scripts\python.exe click_test.py`.
