import json
import os
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "4000"))
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
API_KEY = os.getenv("API_KEY", "")
DEFAULT_MODEL_ALIAS = os.getenv("DEFAULT_MODEL_ALIAS", "mistral-large")
MODEL_MAP = json.loads(os.getenv("MODEL_MAP", "{}") or "{}")

app = FastAPI(title="SkyrimNet Provider Bridge", version="0.1.0")


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


def _resolve_model(alias: Optional[str]) -> str:
    key = alias or DEFAULT_MODEL_ALIAS
    target = MODEL_MAP.get(key)
    if not target:
        raise HTTPException(status_code=400, detail=f"Unknown model alias: {key}")
    return target


def _join_messages(messages: List[ChatMessage]) -> str:
    parts = []
    for m in messages:
        parts.append(f"{m.role}: {m.content}")
    return "\n".join(parts)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def models() -> Dict[str, Any]:
    data = []
    for alias in MODEL_MAP.keys():
        data.append({"id": alias, "object": "model", "owned_by": "bridge"})
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    target = _resolve_model(req.model)
    provider, model_id = target.split(":", 1)

    if provider == "mistral":
        return await _mistral_chat(req, model_id)
    if provider == "gemini":
        return await _gemini_chat(req, model_id)

    raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")


async def _mistral_chat(req: ChatCompletionRequest, model_id: str):
    key = MISTRAL_API_KEY or API_KEY
    if not key:
        raise HTTPException(status_code=500, detail="Missing API Key (MISTRAL_API_KEY or generic API_KEY)")

    payload: Dict[str, Any] = {
        "model": model_id,
        "messages": [m.model_dump() for m in req.messages],
        "temperature": req.temperature if req.temperature is not None else 0.7,
    }
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if req.stream:

        async def event_gen():
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", "https://api.mistral.ai/v1/chat/completions", headers=headers, json={**payload, "stream": True}
                ) as resp:
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
        r = await client.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=payload)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return JSONResponse(content=r.json())


async def _gemini_chat(req: ChatCompletionRequest, model_id: str):
    key = GEMINI_API_KEY or API_KEY
    if not key:
        raise HTTPException(status_code=500, detail="Missing API Key (GEMINI_API_KEY or generic API_KEY)")

    prompt_text = _join_messages(req.messages)
    body = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": req.temperature if req.temperature is not None else 0.7,
        },
    }
    if req.max_tokens is not None:
        body["generationConfig"]["maxOutputTokens"] = req.max_tokens

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={key}"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    data = r.json()
    text = ""
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        text = json.dumps(data)

    return {
        "id": "chatcmpl-gemini-bridge",
        "object": "chat.completion",
        "created": 0,
        "model": req.model or DEFAULT_MODEL_ALIAS,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
