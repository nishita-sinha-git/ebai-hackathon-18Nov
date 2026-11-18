"""Groq model handle for the generation router (design ADR-8 fallback)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.routing.errors import UpstreamUnavailable
from app.routing.fallback import ModelHandle

SYSTEM_PROMPT = (
    "Answer concisely only from the supplied evidence. Never invent facts. If evidence is insufficient, abstain."
)


def groq_handle(client: Any, model: str, timeout_ms: float) -> ModelHandle:
    """Wrap the synchronous Groq client. Any provider error becomes a retryable failure."""

    def _complete(prompt: str) -> str:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        return completion.choices[0].message.content or ""

    async def call(prompt: str, timeout_s: float) -> str:
        try:
            return await asyncio.to_thread(_complete, prompt)
        except Exception as error:
            raise UpstreamUnavailable(f"{type(error).__name__}: {error}") from error

    return ModelHandle(name=f"groq:{model}", timeout_ms=timeout_ms, call=call)
