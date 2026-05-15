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

# API Keys
API_KEY = os.getenv("API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Base URLs
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
TOGETHER_BASE_URL = os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MISTRAL_BASE_URL = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
CUSTOM_BASE_URL = os.getenv("CUSTOM_BASE_URL", "")

DEFAULT_MODEL_ALIAS = os.getenv("DEFAULT_MODEL_ALIAS", "mistral-large")
MODEL_MAP = json.loads(os.getenv("MODEL_MAP", "{}") or "{}")

app = FastAPI(title="SkyrimNet Provider Bridge", version="0.2.0")


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
        return await _openai_compatible_chat(req, model_id, MISTRAL_BASE_URL, MISTRAL_API_KEY or API_KEY)
    if provider == "openai":
        return await _openai_compatible_chat(req, model_id, OPENAI_BASE_URL, OPENAI_API_KEY or API_KEY)
    if provider == "groq":
        return await _openai_compatible_chat(req, model_id, GROQ_BASE_URL, GROQ_API_KEY or API_KEY)
    if provider == "openrouter":
        return await _openai_compatible_chat(req, model_id, OPENROUTER_BASE_URL, OPENROUTER_API_KEY or API_KEY)
    if provider == "together":
        return await _openai_compatible_chat(req, model_id, TOGETHER_BASE_URL, TOGETHER_API_KEY or API_KEY)
    if provider == "deepseek":
        return await _openai_compatible_chat(req, model_id, DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY or API_KEY)
    if provider == "gemini":
        return await _gemini_chat(req, model_id)
    if provider == "anthropic":
        return await _anthropic_chat(req, model_id)
    if provider in ["custom", "openai-compatible", "mylocal"]:
        return await _openai_compatible_chat(req, model_id, CUSTOM_BASE_URL, API_KEY)

    raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")


async def _openai_compatible_chat(req: ChatCompletionRequest, model_id: str, base_url: str, key: str):
    if not base_url:
        raise HTTPException(status_code=500, detail="Missing Base URL for provider")
    if not key:
        raise HTTPException(status_code=500, detail="Missing API Key for provider")

    payload: Dict[str, Any] = {
        "model": model_id,
        "messages": [m.model_dump() for m in req.messages],
        "temperature": req.temperature if req.temperature is not None else 0.7,
    }
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
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


async def _gemini_chat(req: ChatCompletionRequest, model_id: str):
    key = GEMINI_API_KEY or API_KEY
    if not key:
        raise HTTPException(status_code=500, detail="Missing GEMINI_API_KEY or generic API_KEY")

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


async def _anthropic_chat(req: ChatCompletionRequest, model_id: str):
    key = ANTHROPIC_API_KEY or API_KEY
    if not key:
        raise HTTPException(status_code=500, detail="Missing ANTHROPIC_API_KEY or generic API_KEY")

    payload: Dict[str, Any] = {
        "model": model_id,
        "messages": [m.model_dump() for m in req.messages],
        "max_tokens": req.max_tokens or 1024,
        "temperature": req.temperature if req.temperature is not None else 0.7,
    }

    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = "https://api.anthropic.com/v1/messages"

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, headers=headers, json=payload)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    data = r.json()
    # Simple transform to OpenAI format
    content = data["content"][0]["text"] if data.get("content") else ""
    return {
        "id": data.get("id", "anthropic-bridge"),
        "object": "chat.completion",
        "created": 0,
        "model": req.model or DEFAULT_MODEL_ALIAS,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
