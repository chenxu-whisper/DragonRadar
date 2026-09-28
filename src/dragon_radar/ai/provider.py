"""Server-configured provider boundary with a fail-closed default."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from dragon_radar.ai.models import AIProviderResult, TaskType


class ProviderUnavailable(Exception):
    """Raised when no explicitly configured provider adapter is available."""


@dataclass(frozen=True)
class AIProviderLimits:
    deadline: datetime
    timeout_seconds: float
    max_tokens: int
    max_cost: Decimal


class AIProvider(Protocol):
    """Provider and captured state must be serializable by Windows spawn."""

    name: str
    model_version: str
    prompt_version: str

    def generate(
        self,
        task_type: TaskType,
        manifest: dict,
        limits: AIProviderLimits,
    ) -> AIProviderResult | dict:
        """Generate within the supplied wall-clock deadline and budget limits."""


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"Invalid {name}") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"Invalid {name}")
    return value


def _seconds(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"Invalid {name}") from None
    if not 0.1 <= value <= 300:
        raise ValueError(f"Invalid {name}")
    return value


def _cost(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default))
    except InvalidOperation:
        raise ValueError(f"Invalid {name}") from None
    if not value.is_finite() or not Decimal("0") <= value <= Decimal("1000000"):
        raise ValueError(f"Invalid {name}")
    return value


@dataclass(frozen=True)
class AIProviderConfig:
    provider: str
    model_version: str
    prompt_version: str
    concurrency: int
    max_tokens: int
    max_cost: Decimal
    timeout_seconds: float
    token: str | None = field(default=None, repr=False)


def load_provider_config() -> AIProviderConfig:
    provider = os.getenv("DRAGON_AI_PROVIDER", "").strip()
    model = os.getenv("DRAGON_AI_MODEL_VERSION", "").strip()
    prompt = os.getenv("DRAGON_AI_PROMPT_VERSION", "prompt-v1").strip()
    token = os.getenv("DRAGON_AI_TOKEN")
    for name, value in (
        ("DRAGON_AI_PROVIDER", provider or "unavailable"),
        ("DRAGON_AI_MODEL_VERSION", model or "unavailable"),
        ("DRAGON_AI_PROMPT_VERSION", prompt),
    ):
        if not 1 <= len(value) <= 100:
            raise ValueError(f"Invalid {name}")
    return AIProviderConfig(
        provider=provider or "unavailable",
        model_version=model or "unavailable",
        prompt_version=prompt,
        concurrency=_integer("DRAGON_AI_CONCURRENCY", 1, 1, 8),
        max_tokens=_integer("DRAGON_AI_MAX_TOKENS", 4096, 1, 10_000_000),
        max_cost=_cost("DRAGON_AI_MAX_COST", "10"),
        timeout_seconds=_seconds("DRAGON_AI_TIMEOUT_SECONDS", 30),
        token=token if token else None,
    )


class UnavailableProvider:
    def __init__(self, config: AIProviderConfig):
        self.name = config.provider
        self.model_version = config.model_version
        self.prompt_version = config.prompt_version

    def generate(
        self,
        task_type: TaskType,
        manifest: dict,
        limits: AIProviderLimits,
    ):
        raise ProviderUnavailable("AI provider is unavailable")


def provider_from_environment(config: AIProviderConfig | None = None) -> AIProvider:
    config = config or load_provider_config()
    # Provider-specific network adapters are deliberately not inferred. A configured
    # name/token without an installed adapter remains explicitly unavailable.
    return UnavailableProvider(config)


__all__ = [
    "AIProvider",
    "AIProviderConfig",
    "AIProviderLimits",
    "ProviderUnavailable",
    "UnavailableProvider",
    "load_provider_config",
    "provider_from_environment",
]
