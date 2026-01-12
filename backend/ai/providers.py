"""LLM Provider Abstraction for AI Pipeline Generation.

Supports OpenAI-compatible APIs (GLM, local LLMs, etc.).
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

logger = logging.getLogger("gst-manager.ai.providers")

# Try to import aiohttp, fallback to requests
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_AIOHTTP = False
    logger.warning("aiohttp not available, using urllib (slower)")


class LLMProvider(ABC):
    """Base class for LLM providers."""

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config

    @abstractmethod
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """Send chat completion request.

        Args:
            messages: List of message dicts with role/content.
            tools: Optional list of tool definitions.
            timeout: Request timeout in seconds.

        Returns:
            Response dict with message and optional tool_calls.
        """
        pass


class OpenAICompatibleProvider(LLMProvider):
    """Provider for OpenAI-compatible APIs (GLM, local LLMs, OpenAI)."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.url = config.get("url", "")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-3.5-turbo")
        self.proxy = config.get("proxy")

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """Send chat completion to OpenAI-compatible API."""

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        payload = {
            "model": self.model,
            "messages": messages
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        logger.debug(f"Sending request to {self.url}, model={self.model}")

        if HAS_AIOHTTP:
            return await self._request_aiohttp(headers, payload, timeout)
        else:
            return await self._request_urllib(headers, payload, timeout)

    async def _request_aiohttp(
        self,
        headers: Dict,
        payload: Dict,
        timeout: int
    ) -> Dict[str, Any]:
        """Make request using aiohttp."""
        connector = None
        if self.proxy:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(self.proxy)

        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                self.url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"API error: {response.status} - {error_text[:200]}")
                    return {
                        "error": f"API returned {response.status}",
                        "detail": error_text[:200]
                    }

                data = await response.json()
                return self._parse_response(data)

    async def _request_urllib(
        self,
        headers: Dict,
        payload: Dict,
        timeout: int
    ) -> Dict[str, Any]:
        """Make request using urllib (fallback)."""
        loop = asyncio.get_event_loop()

        def sync_request():
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(self.url, data=data, headers=headers)

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                return {"error": f"HTTP {e.code}", "detail": str(e)}
            except urllib.error.URLError as e:
                return {"error": "Connection failed", "detail": str(e)}

        result = await loop.run_in_executor(None, sync_request)
        if "error" in result:
            return result
        return self._parse_response(result)

    def _parse_response(self, data: Dict) -> Dict[str, Any]:
        """Parse OpenAI-compatible response."""
        try:
            choices = data.get("choices", [])
            if not choices:
                return {"error": "No response choices"}

            message = choices[0].get("message", {})
            result = {
                "content": message.get("content", ""),
                "role": message.get("role", "assistant")
            }

            # Extract tool calls if present
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tc.get("id"),
                        "name": tc.get("function", {}).get("name"),
                        "arguments": json.loads(
                            tc.get("function", {}).get("arguments", "{}")
                        )
                    }
                    for tc in tool_calls
                ]

            return result

        except Exception as e:
            logger.error(f"Failed to parse response: {e}")
            return {"error": f"Parse error: {e}"}


class ProviderManager:
    """Manages multiple LLM providers."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.providers: Dict[str, LLMProvider] = {}
        self._load_providers()

    def _load_providers(self) -> None:
        """Load providers from config."""
        providers_config = self.config.get("ai_providers", [])

        for p in providers_config:
            name = p.get("name")
            if not name:
                continue

            provider = OpenAICompatibleProvider(name, p)
            self.providers[name] = provider
            logger.info(f"Loaded AI provider: {name}")

    def get_provider(self, name: Optional[str] = None) -> Optional[LLMProvider]:
        """Get a provider by name or the active default.

        Args:
            name: Provider name, or None for active default.

        Returns:
            LLMProvider instance or None.
        """
        if name:
            return self.providers.get(name)

        # Use active provider
        active = self.config.get("active_provider")
        if active:
            return self.providers.get(active)

        # Return first available
        if self.providers:
            return next(iter(self.providers.values()))

        return None

    def list_providers(self) -> List[Dict[str, Any]]:
        """List available providers (without API keys)."""
        return [
            {
                "name": p.name,
                "model": p.model,
                "has_key": bool(p.api_key)
            }
            for p in self.providers.values()
        ]

    def add_provider(
        self,
        name: str,
        url: str,
        api_key: str,
        model: str
    ) -> bool:
        """Add a new provider."""
        config = {
            "name": name,
            "url": url,
            "api_key": api_key,
            "model": model
        }
        provider = OpenAICompatibleProvider(name, config)
        self.providers[name] = provider

        # Update config
        providers_list = self.config.get("ai_providers", [])
        providers_list.append(config)
        self.config["ai_providers"] = providers_list

        logger.info(f"Added AI provider: {name}")
        return True

    def remove_provider(self, name: str) -> bool:
        """Remove a provider."""
        if name not in self.providers:
            return False

        del self.providers[name]

        # Update config
        providers_list = self.config.get("ai_providers", [])
        self.config["ai_providers"] = [
            p for p in providers_list if p.get("name") != name
        ]

        logger.info(f"Removed AI provider: {name}")
        return True
