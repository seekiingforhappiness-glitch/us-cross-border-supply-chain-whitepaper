"""Pluggable LLM client (soft work only) with a deterministic no-key fallback.

Discipline (AGENTS §1, §3):
- The LLM never touches money. It is used only for field normalization and
  dispute-letter drafting (see agent/tools.py). Amount math is deterministic.
- No API key -> deterministic fallback. The whole pipeline runs end-to-end
  with zero keys; a key only *upgrades* the soft output, never gates it.
- Every result records provider / model / policy_version so any LLM touch is
  auditable.

Provider resolution (first key wins):
    ANTHROPIC_API_KEY -> anthropic
    OPENAI_API_KEY    -> openai
    (neither)         -> deterministic-fallback

Any error while calling a real provider degrades to the fallback rather than
raising, so an outage can never break the audit run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

POLICY_VERSION = "freight-audit-llm-v1"

_ANTHROPIC_DEFAULT = "claude-3-5-sonnet-latest"
_OPENAI_DEFAULT = "gpt-4o-mini"
_FALLBACK_PROVIDER = "deterministic-fallback"


@dataclass(frozen=True)
class LLMResult:
    """Outcome of a soft-work completion. `used_llm=False` means the caller
    should apply its own deterministic rule (the text is empty/advisory)."""

    text: str
    provider: str
    model: str
    policy_version: str
    used_llm: bool
    detail: str = ""

    def as_meta(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "policy_version": self.policy_version,
            "used_llm": self.used_llm,
        }


def resolve_provider() -> tuple[str, str]:
    """Return (provider, model) from the environment. No network, no import."""
    model_override = os.environ.get("FREIGHT_AUDIT_LLM_MODEL")
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", model_override or _ANTHROPIC_DEFAULT
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", model_override or _OPENAI_DEFAULT
    return _FALLBACK_PROVIDER, "none"


class LLMClient:
    """Thin provider abstraction. `complete()` never raises."""

    def __init__(self, provider: str | None = None, model: str | None = None) -> None:
        resolved_provider, resolved_model = resolve_provider()
        self.provider = provider or resolved_provider
        self.model = model or resolved_model
        self.policy_version = POLICY_VERSION

    @property
    def has_llm(self) -> bool:
        return self.provider in ("anthropic", "openai")

    def _fallback(self, detail: str = "no api key; deterministic rule applies") -> LLMResult:
        return LLMResult("", _FALLBACK_PROVIDER, "none", self.policy_version, False, detail)

    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> LLMResult:
        """Best-effort soft-work completion.

        Returns used_llm=False (empty text) when no key is configured or the
        provider call fails, signalling the caller to fall back to its rule.
        """
        if not self.has_llm:
            return self._fallback()
        try:
            if self.provider == "anthropic":
                return self._complete_anthropic(system, user, max_tokens)
            return self._complete_openai(system, user, max_tokens)
        except Exception as exc:  # never break the run on an LLM error
            return self._fallback(f"provider error, fell back: {type(exc).__name__}")

    def _complete_anthropic(self, system: str, user: str, max_tokens: int) -> LLMResult:
        import anthropic  # lazy: only imported when a key is present

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
        return LLMResult(text, "anthropic", self.model, self.policy_version, True, "ok")

    def _complete_openai(self, system: str, user: str, max_tokens: int) -> LLMResult:
        from openai import OpenAI  # lazy

        client = OpenAI()
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return LLMResult(text, "openai", self.model, self.policy_version, True, "ok")
