# SkyrimNet Provider Bridge - Simplified Proxy
import json
import os
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

load_dotenv()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "4000"))

# Single API Key and Base URL
API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:11434/v1")

app = FastAPI(title="SkyrimNet Provider Bridge", version="0.3.0")

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any

class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False

@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}

@app.get("/v1/models")
async def models() -> Dict[str, Any]:
    return {"object": "list", "data": []}

@app.post("/v1/chat/completions")
@app.post("/v1")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    model = body.get("model")
    if not model:
        raise HTTPException(status_code=400, detail="Model is required")

    raw_messages = body.get("messages", [])
    messages = []
    for m in raw_messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(text_parts)
        messages.append(ChatMessage(role=role, content=content))

    req = ChatCompletionRequest(
        model=model,
        messages=messages,
        temperature=body.get("temperature", 0.7),
        max_tokens=body.get("max_tokens"),
        stream=body.get("stream", False)
    )

    return await _openai_compatible_chat(req, BASE_URL, API_KEY)

async def _openai_compatible_chat(req: ChatCompletionRequest, base_url: str, key: str):
    if not base_url:
        raise HTTPException(status_code=500, detail="Missing BASE_URL in .env")

    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": [m.model_dump() for m in req.messages],
        "temperature": req.temperature if req.temperature is not None else 0.7,
    }
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    url = f"{base_url.rstrip('/')}/chat/completions"

    if req.stream:
        async def event_gen():
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, headers=headers, json={**payload, "stream": True}) as resp:
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        yield f"data: {json.dumps({'error': text.decode('utf-8', errors='ignore')})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n"
        return StreamingResponse(event_gen(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, headers=headers, json=payload)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return JSONResponse(content=r.json())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
