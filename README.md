# SkyrimNet Provider Bridge (OpenAI-compatible)

Локальный bridge-сервис, который принимает OpenAI-compatible запросы от SkyrimNet
и проксирует их к Mistral или Google Gemini через нативные SDK/API.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m bridge.server
```

Сервис поднимется на http://127.0.0.1:4000.

## Эндпоинты

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`

## Конфигурация

См. `.env.example`.

Основная идея:

- SkyrimNet указывает endpoint на этот bridge
- Bridge сам маршрутизирует по model к Mistral или Gemini

Примеры model alias:

- `mistral-large`
- `gemini-1.5-pro`

## Интеграция со SkyrimNet (вариант A)

1. Подними bridge локально (`http://127.0.0.1:4000`).
2. В SkyrimNet переключи LLM endpoint на `http://127.0.0.1:4000/v1` и установи любой непустой `api_key` (например `bridge-local-token`).
3. В model presets используй alias из `MODEL_MAP` (например `mistral-large`, `gemini-1.5-pro`).

Пример конфига лежит в `bridge/skyrimnet-config-example.json`.

## Примечание

Текущая реализация поддерживает базовый chat-completions (non-stream и stream passthrough в OpenAI-like SSE).
Tool-calling и JSON schema можно добавить отдельным слоем адаптации.
