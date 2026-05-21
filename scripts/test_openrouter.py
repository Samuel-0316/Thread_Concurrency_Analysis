#!/usr/bin/env python3
"""Quick OpenRouter API connectivity test using requests and the OPENROUTER_API_KEY."""
import os
import requests
import json
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

API_URLS = [
    os.getenv('OPENROUTER_API_URL', '').strip(),
    'https://api.openrouter.ai/v1/chat/completions',
    'https://openrouter.ai/api/v1/chat/completions',
]
API_URLS = [u for u in API_URLS if u]
API_KEY = os.getenv('OPENROUTER_API_KEY')
MODEL = os.getenv('OPENROUTER_MODEL', 'inclusionai/ring-2.6-1t:free')

def main():
    if not API_KEY:
        print('OPENROUTER_API_KEY not set in environment')
        return 2

    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
    }

    payload = {
        'model': MODEL,
        'messages': [{'role': 'user', 'content': 'Say hello in one short sentence.'}],
        'temperature': 0.0,
        'max_tokens': 64,
    }

    resp = None
    last_err = None
    for url in API_URLS:
        print('Trying URL:', url)
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            break
        except Exception as e:
            print('Request to', url, 'failed:', e)
            last_err = e
            resp = None

    if resp is None:
        print('All requests failed; last error:', last_err)
        return 3

    print('Status:', resp.status_code)
    try:
        data = resp.json()
    except Exception:
        print('Non-JSON response:', resp.text[:500])
        return 4

    # Print a compact summary
    print('Response keys:', list(data.keys()))
    # Try to extract text
    text = None
    try:
        choices = data.get('choices') or []
        if choices:
            message = choices[0].get('message') or {}
            text = message.get('content') or choices[0].get('text') or None
    except Exception:
        text = None

    if text:
        print('Model reply (snippet):', text.strip()[:300])
    else:
        print('Could not extract model text; full JSON saved to /tmp/openrouter_response.json')
        with open('reports/openrouter_response.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
