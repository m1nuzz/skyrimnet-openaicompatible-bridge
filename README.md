# SkyrimNet Provider Bridge (OpenAI-compatible)

Локальный HTTP-bridge, который принимает OpenAI-compatible запросы от мода
SkyrimNet и прозрачно проксирует их к любому OpenAI-совместимому провайдеру
(Google Gemini через OpenAI-compat endpoint, OpenAI, Mistral, OpenRouter,
Groq, локальные модели на Ollama / LM Studio и т.д.).

## Что делает bridge

- Принимает `POST /v1/chat/completions` (а также alias `POST /v1`).
- Чинит mojibake кириллицы во входящих сообщениях (строго, без замены
  валидных UTF-8 символов на `?`).
- Сохраняет UTF-8 на выход с явным `charset=utf-8` в `Content-Type`.
- Вырезает `<thought>...</thought>` / `<thinking>...</thinking>` блоки даже
  когда теги разрезаны границами SSE-чанков (rolling-buffer стриппер).
- Пропускает структурные / tool / JSON запросы мимо фильтрации (детект по
  `response_format`, `tools`, `tool_choice` и keyword-индикаторам).
- Сохраняет `ACTION: ...` команды для AI Actions.

## Быстрый старт

### Windows

```cmd
:: один клик
run_bridge.bat
```

Скрипт сам поднимет `uv venv`, поставит зависимости и запустит сервер на
`http://127.0.0.1:4000`.

### Linux / macOS

```bash
chmod +x run.sh
./run.sh
```

### Вручную

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # отредактируй .env
python server.py
```

Сервис поднимется на `http://127.0.0.1:4000`.

## Эндпоинты

- `POST /v1/chat/completions` — основной OpenAI-совместимый chat endpoint
  (поддерживает `stream: true` и `stream: false`).
- `POST /v1` — alias для совместимости с разными OpenAI-клиентами.
- `OPTIONS *` — CORS preflight.

> `GET /health` и `GET /v1/models` пока не реализованы в `server.py`; ранние
> версии README ошибочно их упоминали.

## Конфигурация

См. [`.env.example`](.env.example). Минимально нужно либо `API_KEY` /
`GEMINI_API_KEY`, либо `CUSTOM_BASE_URL` если ходишь на локальный
провайдер без ключа.

| Переменная        | По умолчанию                                            | Назначение                                              |
| ----------------- | ------------------------------------------------------- | ------------------------------------------------------- |
| `HOST`            | `127.0.0.1`                                             | Адрес для bind                                          |
| `PORT`            | `4000`                                                  | Порт                                                    |
| `CUSTOM_BASE_URL` | —                                                       | OpenAI-совместимый upstream (приоритет над `BASE_URL`)  |
| `BASE_URL`        | `https://generativelanguage.googleapis.com/v1beta/openai/` | Fallback upstream (Gemini OpenAI-compat)                |
| `GEMINI_API_KEY`  | —                                                       | Ключ API (приоритет над `API_KEY`)                      |
| `API_KEY`         | —                                                       | Универсальный ключ; ставится в `Authorization: Bearer`  |

Имя модели из запроса передаётся в upstream без изменений — никакого
`MODEL_MAP` в коде нет, ранние версии `GEMINI.md` это ошибочно
утверждали.

## Интеграция со SkyrimNet

1. Подними bridge локально (`http://127.0.0.1:4000`).
2. В SkyrimNet переключи LLM endpoint на `http://127.0.0.1:4000/v1` и
   установи любой непустой `api_key` (например `bridge-local-token`).
3. В пресетах модели используй то имя, которое понимает твой upstream —
   например `gemini-1.5-pro`, `gpt-4o-mini`, `mistral-large-latest`.

Пример конфига SkyrimNet лежит в
[`skyrimnet-config-example.json`](skyrimnet-config-example.json).

## Verbose-режим

`debug_proxy.py` — тонкий шим над `server.py`, который включает
`DEBUG`-логирование. Запускается через `run_debug.bat` или
`python debug_proxy.py`.

## Тесты

`test_bridge.py` — короткий smoke-набор для проверки кодировки,
стриминга, тег-фильтра и сохранения `ACTION:`. Запускается против
работающего bridge на `127.0.0.1:4000`:

```bash
python server.py &
python test_bridge.py
```
