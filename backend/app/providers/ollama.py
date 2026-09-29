import httpx

from app.providers.base import AIProvider, ProviderError


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str = "llama3.1") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def _chat(self, messages: list) -> str:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                return resp.json()["message"]["content"]
        except Exception as exc:
            raise ProviderError(
                f"Ollama is not reachable at {self.base_url}: {exc}"
            ) from exc

    async def aclassify_intent(self, text: str) -> dict:
        import json

        content = await self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Classify intent as JSON only: "
                        '{"intent": str, "requires_memory": bool, '
                        '"requires_tools": bool, "tools": [...], '
                        '"response_mode": str}.'
                    ),
                },
                {"role": "user", "content": text},
            ]
        )
        try:
            return json.loads(content)
        except Exception as exc:
            raise ProviderError(f"Ollama returned invalid JSON: {exc}") from exc

    async def agenerate(self, text: str, context: str = "") -> str:
        user_text = f"{context}\n\nUser: {text}" if context else text
        return await self._chat([{"role": "user", "content": user_text}])
