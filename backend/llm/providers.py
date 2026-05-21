from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


def _classify_error_text(text: str) -> str:
    lowered = (text or '').lower()
    if 'rate limit' in lowered or '429' in lowered or 'quota' in lowered or 'free-models-per-day' in lowered:
        return 'quota_error'
    if 'timeout' in lowered or 'timed out' in lowered or 'read timeout' in lowered:
        return 'timeout'
    if 'connection refused' in lowered or 'dns' in lowered or 'failed to establish' in lowered or 'connection error' in lowered:
        return 'transport_failure'
    return 'transport_failure'


@dataclass
class ProviderResponse:
    text: str
    status: str = 'success'
    error: Optional[str] = None
    raw: Optional[Any] = None


class BaseLLMProvider:
    name = 'base'

    def __init__(self, model: str, temperature: float = 0.3):
        self.model = model
        self.temperature = temperature

    def generate_content(self, contents: str, generation_config: Dict[str, Any]) -> ProviderResponse:
        raise NotImplementedError()

    def generate_stream(self, contents: str, generation_config: Dict[str, Any]):
        response = self.generate_content(contents, generation_config)
        yield response.text


class GeminiProvider(BaseLLMProvider):
    name = 'gemini'

    def __init__(self, model: str, temperature: float = 0.3):
        super().__init__(model=model, temperature=temperature)
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError('google-generativeai package not installed. Install with: pip install google-generativeai') from exc

        api_key = os.getenv('GOOGLE_API_KEY')
        if not api_key:
            raise ValueError('GOOGLE_API_KEY not set in environment')
        genai.configure(api_key=api_key)
        self.genai = genai
        self.client = self.genai.GenerativeModel(model)

    def generate_content(self, contents: str, generation_config: Dict[str, Any]) -> ProviderResponse:
        try:
            response = self.client.generate_content(
                contents=contents,
                generation_config={
                    'temperature': generation_config.get('temperature', self.temperature),
                    'max_output_tokens': generation_config.get('max_output_tokens', 2048),
                },
            )
            return ProviderResponse(text=getattr(response, 'text', '') or '', status='success', raw=response)
        except Exception as exc:
            status = _classify_error_text(str(exc))
            return ProviderResponse(text=f'[gemini_error] {exc}', status=status, error=str(exc))


class OpenRouterProvider(BaseLLMProvider):
    name = 'openrouter'
    api_url = 'https://openrouter.ai/api/v1/chat/completions'

    def __init__(self, model: str, temperature: float = 0.3, base_url: Optional[str] = None):
        super().__init__(model=model, temperature=temperature)
        self.api_url = base_url or os.getenv('OPENROUTER_BASE_URL', self.api_url)
        self.api_key = os.getenv('OPENROUTER_API_KEY')
        if not self.api_key:
            raise ValueError('OPENROUTER_API_KEY not set in environment')

    def generate_content(self, contents: str, generation_config: Dict[str, Any]) -> ProviderResponse:
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.model,
            'messages': [{'role': 'user', 'content': contents}],
            'temperature': generation_config.get('temperature', self.temperature),
            'max_tokens': generation_config.get('max_output_tokens', 2048),
        }
        try:
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
            if response.status_code == 429:
                return ProviderResponse(text=f'[openrouter_error] {response.text}', status='quota_error', error=response.text, raw=response)
            response.raise_for_status()
            data = response.json()
            choices = data.get('choices') or []
            if choices:
                message = choices[0].get('message') or {}
                text = message.get('content') or choices[0].get('text', '') or ''
            else:
                text = data.get('text', '') or json.dumps(data)
            return ProviderResponse(text=text, status='success', raw=data)
        except requests.Timeout as exc:
            return ProviderResponse(text=f'[openrouter_error] {exc}', status='timeout', error=str(exc))
        except requests.RequestException as exc:
            return ProviderResponse(text=f'[openrouter_error] {exc}', status=_classify_error_text(str(exc)), error=str(exc))
        except Exception as exc:
            return ProviderResponse(text=f'[openrouter_error] {exc}', status=_classify_error_text(str(exc)), error=str(exc))


class OllamaProvider(BaseLLMProvider):
    name = 'ollama'

    def __init__(self, model: str, temperature: float = 0.3, base_url: Optional[str] = None):
        super().__init__(model=model, temperature=temperature)
        self.base_url = base_url or os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')

    def generate_content(self, contents: str, generation_config: Dict[str, Any]) -> ProviderResponse:
        payload = {
            'model': self.model,
            'prompt': contents,
            'stream': False,
            'options': {
                'temperature': generation_config.get('temperature', self.temperature),
                'num_predict': generation_config.get('max_output_tokens', 2048),
            },
        }
        try:
            response = requests.post(f'{self.base_url.rstrip("/")}/api/generate', json=payload, timeout=180)
            if response.status_code == 429:
                return ProviderResponse(text=f'[ollama_error] {response.text}', status='quota_error', error=response.text, raw=response)
            response.raise_for_status()
            data = response.json()
            text = data.get('response', '') or json.dumps(data)
            return ProviderResponse(text=text, status='success', raw=data)
        except requests.Timeout as exc:
            return ProviderResponse(text=f'[ollama_error] {exc}', status='timeout', error=str(exc))
        except requests.RequestException as exc:
            return ProviderResponse(text=f'[ollama_error] {exc}', status=_classify_error_text(str(exc)), error=str(exc))
        except Exception as exc:
            return ProviderResponse(text=f'[ollama_error] {exc}', status=_classify_error_text(str(exc)), error=str(exc))


def build_provider(provider_name: str, model: str, temperature: float = 0.3) -> BaseLLMProvider:
    normalized = (provider_name or 'auto').lower()
    if normalized == 'openrouter':
        return OpenRouterProvider(model=model, temperature=temperature)
    if normalized == 'ollama':
        return OllamaProvider(model=model, temperature=temperature)
    if normalized == 'gemini':
        return GeminiProvider(model=model, temperature=temperature)

    # Auto-detect by available credentials and model naming.
    if os.getenv('OPENROUTER_API_KEY') and ('/' in model or 'inclusionai' in model.lower()):
        return OpenRouterProvider(model=model, temperature=temperature)
    if os.getenv('GOOGLE_API_KEY') and model.lower().startswith('gemini'):
        return GeminiProvider(model=model, temperature=temperature)
    return OllamaProvider(model=model, temperature=temperature)