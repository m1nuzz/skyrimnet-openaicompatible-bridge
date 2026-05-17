import json
import time

import requests

# Configuration
BRIDGE_URL = "http://127.0.0.1:4000/v1/chat/completions"

def test_russian_encoding():
    print("\n--- Testing Russian Encoding (UTF-8) ---")
    payload = {
        "model": "gemini-1.5-flash",
        "messages": [
            {"role": "user", "content": "Привет, Ралоф! Как дела в Хелгене?"}
        ],
        "stream": False
    }
    try:
        response = requests.post(BRIDGE_URL, json=payload, timeout=10)
        print(f"Status: {response.status_code}")
        data = response.json()
        content = data['choices'][0]['message']['content']
        print(f"Response: {content}")
        if any(c in content for c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"):
            print("✅ SUCCESS: Russian characters detected.")
        else:
            print("❌ FAILURE: No Russian characters or broken encoding.")
    except Exception as e:
        print(f"❌ ERROR: {e}")

def test_streaming_and_filtering():
    print("\n--- Testing Streaming and Thought Filtering ---")
    payload = {
        "model": "gemini-1.5-flash",
        "messages": [
            {"role": "user", "content": "Напиши короткую мысль в тегах <thought> и ответ персонажа."}
        ],
        "stream": True
    }
    try:
        response = requests.post(BRIDGE_URL, json=payload, stream=True, timeout=15)
        print(f"Status: {response.status_code}")

        full_text = ""
        for line in response.iter_lines():
            if line:
                line_text = line.decode('utf-8')
                if line_text.startswith("data: "):
                    data_str = line_text[6:]
                    if data_str != "[DONE]":
                        try:
                            chunk = json.loads(data_str)
                            content = chunk['choices'][0]['delta'].get('content', '')
                            full_text += content
                            if content: print(content, end="", flush=True)
                        except: pass

        print("\n\nFinal Full Text Analysis:")
        if "<thought>" in full_text or "<thinking>" in full_text:
            print("❌ FAILURE: <thought> tags leaked into output.")
        else:
            print("✅ SUCCESS: Technical tags filtered.")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")

def test_action_preservation():
    print("\n--- Testing ACTION: Command Preservation ---")
    payload = {
        "model": "gemini-1.5-flash",
        "messages": [
            {"role": "user", "content": "Ответь фразой 'Я иду в атаку!' и добавь экшен ACTION: Attack(Player)."}
        ],
        "stream": False
    }
    try:
        response = requests.post(BRIDGE_URL, json=payload, timeout=10)
        data = response.json()
        content = data['choices'][0]['message']['content']
        print(f"Response: {content}")
        if "ACTION:" in content:
            print("✅ SUCCESS: ACTION command preserved.")
        else:
            print("❌ FAILURE: ACTION command was filtered out.")
    except Exception as e:
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    print("SkyrimNet Bridge Verification Tool")
    print("Make sure server.py is running on port 4000 before starting.")
    time.sleep(1)

    test_russian_encoding()
    test_action_preservation()
    test_streaming_and_filtering()
