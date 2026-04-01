"""
Thin Anthropic API wrapper shared by commissioning and idle_tuning.

LLMProxy (agent layer) wraps this with token budgeting and memory injection.
Direct callers in commissioning/ and idle_tuning/ use LLMClient.
"""

from __future__ import annotations

import re
import json
from typing import Any, Dict, Optional


class LLMUnavailableError(Exception):
    """Raised when ANTHROPIC_API_KEY is not configured."""


class LLMCallError(Exception):
    """Raised when an API call fails."""


class LLMBudgetExceededError(Exception):
    """Raised when the session token budget is exhausted."""


class LLMClient:
    """Async wrapper around the Anthropic messages API.

    Note: The underlying Anthropic Python SDK is sync, but we expose an async
    interface to keep call sites uniform across the codebase.
    """

    def __init__(self) -> None:
        import os

        self._api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
        self._tokens_used: int = 0

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    async def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 2000,
        timeout: Optional[int] = None,
    ) -> str:
        from manastone.common.config import ManaConfig

        cfg = ManaConfig.get()
        budget = cfg.get_max_tokens_per_session()

        if self._tokens_used + max_tokens > budget:
            raise LLMBudgetExceededError(
                f"Token budget exhausted: used={self._tokens_used}, limit={budget}"
            )

        if not self.available:
            raise LLMUnavailableError("No ANTHROPIC_API_KEY configured")

        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        _timeout = timeout or cfg.get_llm_timeout()

        try:
            response = client.messages.create(
                model=cfg.get_llm_model(),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                timeout=_timeout,
            )
            # Prefer usage if available; otherwise fall back to max_tokens.
            text: str = response.content[0].text  # type: ignore[index]
            try:
                self._tokens_used += response.usage.input_tokens + response.usage.output_tokens
            except Exception:
                self._tokens_used += max_tokens
            return text
        except LLMBudgetExceededError:
            raise
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMCallError(f"LLM call failed: {exc}") from exc

    async def call_json(
        self,
        system: str,
        user: str,
        schema: Dict[str, Any],
        max_tokens: int = 500,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call Claude with structured output (JSON schema) and return parsed JSON.

        Uses Anthropic's structured outputs via output_config.format.
        """
        from manastone.common.config import ManaConfig

        cfg = ManaConfig.get()
        budget = cfg.get_max_tokens_per_session()

        if self._tokens_used + max_tokens > budget:
            raise LLMBudgetExceededError(
                f"Token budget exhausted: used={self._tokens_used}, limit={budget}"
            )

        if not self.available:
            raise LLMUnavailableError("No ANTHROPIC_API_KEY configured")

        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        _timeout = timeout or cfg.get_llm_timeout()

        try:
            response = client.messages.create(
                model=cfg.get_llm_model(),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "format": {"type": "json_schema", "schema": schema},
                },
                timeout=_timeout,
            )

            text: str = response.content[0].text  # type: ignore[index]
            try:
                parsed = json.loads(text)
            except Exception as exc:
                raise LLMCallError(f"Structured output was not valid JSON: {exc}") from exc

            try:
                self._tokens_used += response.usage.input_tokens + response.usage.output_tokens
            except Exception:
                self._tokens_used += max_tokens

            if not isinstance(parsed, dict):
                raise LLMCallError(f"Structured output must be a JSON object, got: {type(parsed)}")

            return parsed
        except (LLMBudgetExceededError, LLMUnavailableError):
            raise
        except Exception as exc:
            raise LLMCallError(f"LLM structured call failed: {exc}") from exc
        except LLMBudgetExceededError:
            raise
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMCallError(f"LLM call failed: {exc}") from exc

    @staticmethod
    def extract_yaml(text: str) -> str:
        """Extract YAML from LLM output, handling code fences."""
        match = re.search(r"```(?:yaml)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            return match.group(1)
        return text.strip()

    @staticmethod
    def extract_json(text: str) -> str:
        """Extract JSON from LLM output, handling code fences."""
        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            return match.group(1)
        return text.strip()
