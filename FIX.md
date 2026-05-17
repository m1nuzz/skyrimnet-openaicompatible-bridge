# Technical Audit Mandate: SkyrimNet Bridge

## 1. Context & Essence
This bridge is a critical middleware between a legacy game engine (Skyrim) and modern AI. It is NOT a simple proxy; it is a **Real-Time Immersion & Encoding Sanitizer**. 

## 2. Core Logic Blueprints (Audit These Against server.py)

### A. Task Differentiation Logic (`is_json_task`)
*   **Intent:** Identify if a request is a "Background Logic Task" (needs raw output) or "Character Dialogue" (needs filtering).
*   **Current Indicators:**
    *   `Output format: \``
    *   `Respond with ONLY`
    *   `Generate a memory search query`
    *   `Determine the next speaker`
    *   `most appropriate action`
    *   `emotional state for`
    *   `Respond now. One line only`
*   **Audit Task:** Are these indicators too specific? Will a slight change in the mod's prompt bypass this and cause accidental filtering of JSON data?

### B. The Immersion Filter (`is_leakage_line`)
*   **Intent:** Strip "AI Chatter" but **must preserve `ACTION:` commands**.
*   **Current Patterns:**
    *   `Character/Setting/Context/Thought/Note to self:` (and Russian equivalents: `Мысли/Заметка/План`)
    *   Numbered lists: `1.`, `2)`, `1. **...**`
    *   Thinking tokens: `thought.`, `thinking.`
*   **Audit Task:** Does the regex risk catching valid dialogue? For example, if an NPC says "1. First, we take the fort", will it be deleted?

### C. Stateful SSE Filtering (`is_thinking`)
*   **Intent:** Real-time suppression of `<thought>` or `<thinking>` tags during streaming.
*   **Current Flow:** 
    1.  Search for `<thought>`/`<thinking>` in chunk.
    2.  If found, set `is_thinking = True`, discard following text.
    3.  If `is_thinking`, search for `</thought>`/`</thinking>`.
    4.  If found, set `is_thinking = False`, keep following text.
*   **Audit Task:** Check for **Fragment Leakage**. If the tag is delivered as `chunk1: "<th"`, `chunk2: "ought>"`, the current implementation (searching for the full string) will FAIL. Evaluate if a buffer-based or character-by-character approach is needed.

### D. The Russian "Zero-Mojibake" Policy
*   **Intent:** Receive "broken" Latin-1, send "clean" UTF-8.
*   **Algorithm:** `raw_bytes -> latin-1 (replace) -> utf-8 (decode)`.
*   **Audit Task:** In `server.py`, verify that *every* exit point (Streaming and Non-Streaming) uses `.encode('utf-8')` without any intermediate remangling.

## 3. Concurrency Safety
*   **Audit Task:** Evaluate `SESSION = requests.Session()` usage. Is it thread-safe for 10+ concurrent SSE streams? 

---
**Auditor Instruction:** Compare the logic described above with the actual implementation in `server.py`. Identify gaps where the code fails to meet the "Intent" described in these blueprints.
