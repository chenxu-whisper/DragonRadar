"""Bounded lease worker that invokes only the configured provider interface."""

import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError

from dragon_radar.ai.models import AIProviderResult, AITaskRecord
from dragon_radar.ai.provider import AIProvider, AIProviderLimits, ProviderUnavailable
from dragon_radar.ai.repository import AITaskRepository
from dragon_radar.storage.repository import Forbidden, NotFound

AI_LEASE_COMPLETION_MARGIN = timedelta(seconds=1)
DEFAULT_AI_LEASE_DURATION = timedelta(seconds=60)
DEFAULT_AI_PROVIDER_TIMEOUT_SECONDS = 30.0


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


def _stop_process(process) -> None:
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
    process.join()


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
        try:
            process.start()
            send.close()
            if not receive.poll(self.provider_timeout):
                _stop_process(process)
                raise TimeoutError
            kind, payload = receive.recv()
            _stop_process(process)
        except TimeoutError:
            raise
        except Exception:
            _stop_process(process)
            raise RuntimeError("provider_failed") from None
        finally:
            receive.close()
            send.close()
        if kind == "failure":
            if payload == "provider_unavailable":
                raise ProviderUnavailable
            raise RuntimeError("provider_failed")
        return AIProviderResult.model_validate(payload), lease

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
        except Forbidden:
            return self.repository.fail(lease, code="input_forbidden")
        except NotFound:
            return self.repository.fail(lease, code="input_unavailable")
        try:
            result, lease = self._invoke(lease, manifest)
        except ProviderUnavailable:
            return self.repository.fail(lease, code="provider_unavailable")
        except TimeoutError:
            return self.repository.retry_or_fail(
                lease,
                code="provider_timeout",
                max_attempts=self.max_attempts,
            )
        except ValidationError:
            return self.repository.fail(lease, code="result_limit_exceeded")
        except Exception:
            return self.repository.retry_or_fail(
                lease,
                code="provider_failed",
                max_attempts=self.max_attempts,
            )
        try:
            return self.repository.succeed(
                lease,
                result,
                provider=self.provider.name,
                model_version=self.provider.model_version,
                prompt_version=self.provider.prompt_version,
                max_tokens=self.max_tokens,
                max_cost=self.max_cost,
            )
        except ValueError:
            return self.repository.fail(lease, code="result_limit_exceeded")

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
