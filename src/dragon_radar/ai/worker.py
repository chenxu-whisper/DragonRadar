"""Bounded lease worker that invokes only the configured provider interface."""

import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from multiprocessing.reduction import ForkingPickler

from pydantic import ValidationError

from dragon_radar.ai.models import (
    MAX_PROVIDER_LATENCY_MS,
    AIProviderResult,
    AITaskRecord,
)
from dragon_radar.ai.provider import AIProvider, AIProviderLimits, ProviderUnavailable
from dragon_radar.ai.repository import AITaskRepository
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound

AI_LEASE_COMPLETION_MARGIN = timedelta(seconds=1)
DEFAULT_AI_LEASE_DURATION = timedelta(seconds=60)
DEFAULT_AI_PROVIDER_TIMEOUT_SECONDS = 30.0
PROCESS_TERMINATE_JOIN_SECONDS = 0.25
PROCESS_KILL_JOIN_SECONDS = 0.25


class _ProviderCleanupError(Exception):
    """Raised when a provider process survives bounded forced cleanup."""


def _provider_child(send, provider, task_type, manifest, limits) -> None:
    try:
        value = provider.generate(task_type, manifest, limits)
        result = AIProviderResult.model_validate(value)
        send.send(("result", result.model_dump(mode="json")))
    except ProviderUnavailable:
        send.send(("failure", "provider_unavailable"))
    except Exception:
        try:
            send.send(("failure", "provider_failed"))
        except Exception:
            pass
    finally:
        send.close()


def _stop_process(process) -> bool:
    if process.pid is None:
        process.close()
        return True
    if process.is_alive():
        process.terminate()
    process.join(PROCESS_TERMINATE_JOIN_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(PROCESS_KILL_JOIN_SECONDS)
    stopped = not process.is_alive()
    if stopped:
        process.close()
    return stopped


def _elapsed_ms(started_ns: int) -> int:
    return min(
        max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
        MAX_PROVIDER_LATENCY_MS,
    )


def validate_ai_worker_timing(
    *,
    lease_for: timedelta,
    provider_timeout: float,
) -> None:
    if not 0.1 <= provider_timeout <= 300:
        raise ValueError("Invalid AI provider timeout")
    if not isinstance(lease_for, timedelta) or not (
        timedelta(seconds=1) <= lease_for <= timedelta(hours=1)
    ):
        raise ValueError("Invalid AI lease duration")
    required_lease = timedelta(seconds=provider_timeout) + AI_LEASE_COMPLETION_MARGIN
    if lease_for < required_lease:
        raise ValueError(
            "AI lease duration must cover provider timeout and completion margin"
        )


class AIWorker:
    def __init__(
        self,
        repository: AITaskRepository,
        provider: AIProvider,
        *,
        worker_id: str,
        lease_for: timedelta = DEFAULT_AI_LEASE_DURATION,
        provider_timeout: float = DEFAULT_AI_PROVIDER_TIMEOUT_SECONDS,
        max_attempts: int = 3,
        max_tokens: int = 4096,
        max_cost: Decimal = Decimal("10"),
        concurrency: int = 1,
    ):
        validate_ai_worker_timing(
            lease_for=lease_for,
            provider_timeout=provider_timeout,
        )
        if type(concurrency) is not int or not 1 <= concurrency <= 8:
            raise ValueError("Invalid AI worker concurrency")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid AI max attempts")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 10_000_000:
            raise ValueError("Invalid AI token limit")
        if (
            not isinstance(max_cost, Decimal)
            or not max_cost.is_finite()
            or not Decimal("0") <= max_cost <= Decimal("1000000")
        ):
            raise ValueError("Invalid AI cost limit")
        try:
            ForkingPickler.dumps(provider)
        except Exception:
            raise ValueError(
                "AI provider must support Windows spawn serialization"
            ) from None
        self.repository = repository
        self.provider = provider
        self.worker_id = worker_id
        self.lease_for = lease_for
        self.provider_timeout = provider_timeout
        self.max_attempts = max_attempts
        self.max_tokens = max_tokens
        self.max_cost = max_cost
        self.concurrency = concurrency

    def _invoke(self, lease, manifest) -> tuple[AIProviderResult, object]:
        limits = AIProviderLimits(
            deadline=datetime.now(UTC) + timedelta(seconds=self.provider_timeout),
            timeout_seconds=self.provider_timeout,
            max_tokens=self.max_tokens,
            max_cost=self.max_cost,
        )
        lease = self.repository.renew(lease, lease_for=self.lease_for)
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(
            target=_provider_child,
            args=(
                send,
                self.provider,
                lease.job.task_type,
                dict(manifest),
                limits,
            ),
        )
        process.daemon = True
        try:
            process.start()
            send.close()
            if not receive.poll(self.provider_timeout):
                if not _stop_process(process):
                    raise _ProviderCleanupError
                raise TimeoutError
            kind, payload = receive.recv()
            if not _stop_process(process):
                raise _ProviderCleanupError
        except (TimeoutError, _ProviderCleanupError):
            raise
        except (EOFError, OSError):
            if not _stop_process(process):
                raise _ProviderCleanupError from None
            raise RuntimeError("provider_failed") from None
        finally:
            receive.close()
            send.close()
        if kind == "failure":
            if payload == "provider_unavailable":
                raise ProviderUnavailable
            raise RuntimeError("provider_failed")
        return AIProviderResult.model_validate(payload), lease

    @staticmethod
    def _write(operation, *args, **kwargs) -> AITaskRecord | None:
        try:
            return operation(*args, **kwargs)
        except Conflict:
            return None

    def run_once(self) -> AITaskRecord | None:
        lease = self.repository.claim(
            self.worker_id,
            lease_for=self.lease_for,
            max_attempts=self.max_attempts,
        )
        if lease is None:
            return None
        try:
            manifest = self.repository.load_input(lease)
        except Conflict:
            return None
        except Forbidden:
            return self._write(
                self.repository.fail,
                lease,
                code="input_forbidden",
            )
        except NotFound:
            return self._write(
                self.repository.fail,
                lease,
                code="input_unavailable",
            )
        started_ns = time.monotonic_ns()
        try:
            result, lease = self._invoke(lease, manifest)
        except Conflict:
            return None
        except ProviderUnavailable:
            return self._write(
                self.repository.fail,
                lease,
                code="provider_unavailable",
                latency_ms=_elapsed_ms(started_ns),
            )
        except TimeoutError:
            return self._write(
                self.repository.retry_or_fail,
                lease,
                code="provider_timeout",
                max_attempts=self.max_attempts,
                latency_ms=_elapsed_ms(started_ns),
            )
        except _ProviderCleanupError:
            raise
        except ValidationError:
            return self._write(
                self.repository.fail,
                lease,
                code="result_limit_exceeded",
                latency_ms=_elapsed_ms(started_ns),
            )
        except RuntimeError:
            return self._write(
                self.repository.retry_or_fail,
                lease,
                code="provider_failed",
                max_attempts=self.max_attempts,
                latency_ms=_elapsed_ms(started_ns),
            )
        latency_ms = _elapsed_ms(started_ns)
        try:
            return self._write(
                self.repository.succeed,
                lease,
                result,
                provider=self.provider.name,
                model_version=self.provider.model_version,
                prompt_version=self.provider.prompt_version,
                latency_ms=latency_ms,
                max_tokens=self.max_tokens,
                max_cost=self.max_cost,
            )
        except ValueError:
            return self._write(
                self.repository.fail,
                lease,
                code="result_limit_exceeded",
                latency_ms=latency_ms,
            )

    def run_batch(self) -> tuple[AITaskRecord, ...]:
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [executor.submit(self.run_once) for _ in range(self.concurrency)]
            return tuple(
                result for future in futures if (result := future.result()) is not None
            )

    def run_forever(
        self,
        *,
        poll_seconds: float = 1.0,
        sleep=time.sleep,
        stop=lambda: False,
        max_iterations: int | None = None,
    ) -> None:
        if not 0.1 <= poll_seconds <= 60:
            raise ValueError("Invalid AI poll interval")
        iterations = 0
        try:
            while not stop():
                if max_iterations is not None and iterations >= max_iterations:
                    return
                completed = self.run_batch()
                iterations += 1
                if not completed:
                    sleep(poll_seconds)
        except KeyboardInterrupt:
            return


__all__ = [
    "AI_LEASE_COMPLETION_MARGIN",
    "AIWorker",
    "DEFAULT_AI_LEASE_DURATION",
    "DEFAULT_AI_PROVIDER_TIMEOUT_SECONDS",
    "validate_ai_worker_timing",
]
