"""Bounded lease worker that invokes only the configured provider interface."""

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import timedelta
from decimal import Decimal
from time import monotonic

from pydantic import ValidationError

from dragon_radar.ai.models import AIProviderResult, AITaskRecord
from dragon_radar.ai.provider import AIProvider, ProviderUnavailable
from dragon_radar.ai.repository import AITaskRepository
from dragon_radar.storage.repository import Forbidden, NotFound


class AIWorker:
    def __init__(
        self,
        repository: AITaskRepository,
        provider: AIProvider,
        *,
        worker_id: str,
        lease_for: timedelta = timedelta(seconds=30),
        provider_timeout: float = 30,
        max_attempts: int = 3,
        max_tokens: int = 4096,
        max_cost: Decimal = Decimal("10"),
        concurrency: int = 1,
    ):
        if not 0.1 <= provider_timeout <= 300:
            raise ValueError("Invalid AI provider timeout")
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
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            self.provider.generate,
            lease.job.task_type,
            dict(manifest),
        )
        deadline = monotonic() + self.provider_timeout
        heartbeat = self.lease_for.total_seconds() / 3
        try:
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise FutureTimeoutError
                try:
                    value = future.result(timeout=min(heartbeat, remaining))
                    return AIProviderResult.model_validate(value), lease
                except FutureTimeoutError:
                    if future.done() or monotonic() >= deadline:
                        raise
                    lease = self.repository.renew(
                        lease,
                        lease_for=self.lease_for,
                    )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

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
        except FutureTimeoutError:
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


__all__ = ["AIWorker"]
