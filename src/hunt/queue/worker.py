"""Job execution: timeouts, async handlers, and the worker loop.

``hunt queue:work`` is a thin wrapper around :func:`run_worker`.
"""

from __future__ import annotations

import asyncio
import inspect
import signal
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from hunt.queue._logging import log_error, log_warning
from hunt.queue.job import Job
from hunt.queue.serialization import (
    InvalidPayload,
    derive_job_id,
    due_time,
    import_job_class,
    open_payload,
    rebuild_job,
    valid_job_id,
)

_HAS_SIGALRM = hasattr(signal, "SIGALRM")

# Indirection so tests can run the retry and idle paths without real sleeping.
_pause = time.sleep

# Seconds between the worker's housekeeping passes (pruning old processed ids,
# and checking again for a processed-id store that was missing).
_MAINTENANCE_INTERVAL = 300.0

# Seconds a delivery is postponed while another delivery of the same job id is running.
_BUSY_DELAY = 5


class JobTimeout(BaseException):
    """Raised inside a running job when it exceeds its timeout.

    A ``BaseException`` so that a broad ``except Exception`` in job code
    cannot swallow it.
    """


class MaxAttemptsExceeded(RuntimeError):
    """The job was delivered more often than its ``tries`` allows."""


class WorkerError(RuntimeError):
    """The worker could not continue (only raised in ``--once`` mode)."""


def _default_output(message: str, err: bool = False) -> None:
    import sys

    print(message, file=sys.stderr if err else sys.stdout)


# ---------------------------------------------------------------------------
# Running a handler
# ---------------------------------------------------------------------------


def run_awaitable(awaitable: Any) -> Any:
    """Run *awaitable* to completion from synchronous code and return its result.

    Without a running event loop this is ``asyncio.run``. Inside a running loop
    (for example the sync queue driver called from an async controller) the
    awaitable runs on its own loop in a helper thread, which matches how a
    worker would run it.
    """

    async def _await() -> Any:
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await())

    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = asyncio.run(_await())
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=_target, name="hunt-job-async")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def call_handler(instance: Any, *args: Any) -> Any:
    """Call ``instance.handle(*args)``, awaiting the result if it is a coroutine."""
    result = instance.handle(*args)
    if inspect.isawaitable(result):
        result = run_awaitable(result)
    return result


def run_with_timeout(fn: Callable[[], Any], timeout_seconds: int, *, thread_fallback: bool = True) -> None:
    """Run *fn()* and raise :class:`JobTimeout` if it takes longer than *timeout_seconds*.

    ``SIGALRM`` is used on the main thread. Elsewhere (or on platforms without
    it) a watchdog thread is used when *thread_fallback* is true; otherwise
    *fn* simply runs without a timeout.
    """
    if timeout_seconds <= 0:
        fn()
        return

    if _HAS_SIGALRM and threading.current_thread() is threading.main_thread():

        def _handler(signum, frame):
            raise JobTimeout(f"Job timed out after {timeout_seconds}s")

        started = time.monotonic()
        old = signal.signal(signal.SIGALRM, _handler)
        # alarm() returns the seconds left on an outer alarm (a job dispatched
        # from inside another job); it is re-armed when this one finishes.
        outer_remaining = signal.alarm(timeout_seconds)
        try:
            fn()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
            if outer_remaining:
                elapsed = int(time.monotonic() - started)
                signal.alarm(max(1, outer_remaining - elapsed))
        return

    if not thread_fallback:
        fn()
        return

    exc_box: list[BaseException | None] = [None]

    def _target():
        try:
            fn()
        except BaseException as e:
            exc_box[0] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout_seconds)
    if t.is_alive():
        raise JobTimeout(f"Job timed out after {timeout_seconds}s")
    if exc_box[0] is not None:
        raise exc_box[0]


# ---------------------------------------------------------------------------
# Worker helpers
# ---------------------------------------------------------------------------


def record_history(job_class: str, queue: str, duration_ms: int, status: str) -> None:
    """Write a completed/failed record to jobs_history if the table exists."""
    try:
        from hunt.database.connection import raw as db_raw

        db_raw(
            "INSERT INTO jobs_history (job_class, queue, duration_ms, finished_at, status)"
            " VALUES (:job_class, :queue, :duration_ms, :finished_at, :status)",
            {
                "job_class": job_class,
                "queue": queue,
                "duration_ms": duration_ms,
                "finished_at": int(time.time()),
                "status": status,
            },
        )
    except Exception:
        pass  # Table may not exist; don't crash the worker


def backoff_delay(backoff: Any, attempt: int) -> int:
    """Seconds to wait before retry number *attempt* (0 when unset or empty)."""
    try:
        if isinstance(backoff, (list, tuple)):
            if not backoff:
                return 0
            idx = max(0, min(attempt - 1, len(backoff) - 1))
            return max(0, int(backoff[idx]))
        return max(0, int(backoff or 0))
    except (TypeError, ValueError):
        return 0


def resolve_tries(cls: type | None, body: dict, default_tries: int) -> int:
    """Max attempts for a job.

    The job's own ``tries`` wins when the class (or the instance) sets it;
    otherwise the worker's ``--tries`` applies.
    """
    explicit = "tries" in (body.get("data") or {})
    if cls is None:
        explicit = "tries" in body
    elif not explicit:
        explicit = any("tries" in vars(klass) for klass in cls.__mro__ if klass is not Job and klass is not object)
    value = body.get("tries", default_tries) if explicit else default_tries
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default_tries))


class _RecentIds:
    """Bounded memory of the job ids this worker has already completed."""

    def __init__(self, maxlen: int = 10000) -> None:
        self._order: deque[str] = deque()
        self._seen: set[str] = set()
        self._maxlen = maxlen

    def add(self, job_uid: str) -> None:
        if job_uid in self._seen:
            return
        self._seen.add(job_uid)
        self._order.append(job_uid)
        if len(self._order) > self._maxlen:
            self._seen.discard(self._order.popleft())

    def __contains__(self, job_uid: object) -> bool:
        return job_uid in self._seen


_GUARD_METHODS = ("processed_store_ready", "claim_run", "release_run", "complete")


class ReplayGuard:
    """Cross-worker replay protection, backed by the driver's processed-id store.

    ``active`` is true only while the driver has such a store and it answered
    the last time it was asked. Otherwise the worker falls back to its
    in-process memory of completed ids, and says so once (never silently).
    """

    def __init__(self, driver: Any, output: Callable[..., None] | None = None) -> None:
        self.driver = driver
        self.output = output
        self.supported = all(callable(getattr(driver, name, None)) for name in _GUARD_METHODS)
        self.active = False
        self._warned = False
        self._last_maintenance: float | None = None

    @property
    def retention(self) -> Any:
        return getattr(self.driver, "processed_retention", None)

    def _say(self, message: str) -> None:
        if self.output is not None:
            self.output(f"  WARNING: {message}", err=True)
            log_warning(f"Queue worker: {message}")

    def check(self) -> bool:
        """Ask the driver whether its store is usable; warn once when it is not."""
        was_active = self.active
        try:
            self.active = bool(self.supported and self.driver.processed_store_ready())
        except Exception:
            self.active = False
        if self.active:
            if self._warned and not was_active and self.output is not None:
                self.output("  Cross-worker replay protection is now active.")
                self._warned = False
            return True
        if not self._warned:
            self._warned = True
            if self.supported:
                hint = getattr(self.driver, "processed_store_hint", None)
                reason = hint() if callable(hint) else "the processed-id store is unavailable"
            else:
                reason = f"the {type(self.driver).__name__} queue driver has no processed-id store"
            self._say(
                f"cross-worker replay protection is NOT active: {reason}. Until then a job id that already "
                "finished is only recognised by the worker process that ran it."
            )
        return False

    def maintain(self, force: bool = False) -> None:
        """Every few minutes: prune old processed ids, or look for the store again."""
        now = time.monotonic()
        if not force and self._last_maintenance is not None and now - self._last_maintenance < _MAINTENANCE_INTERVAL:
            return
        self._last_maintenance = now
        if not self.check():
            return
        prune = getattr(self.driver, "prune_processed", None)
        if callable(prune):
            try:
                prune()
            except Exception as exc:
                log_error(f"Queue worker: could not prune old processed job ids: {exc}")


def _reconnect(driver: Any) -> None:
    reconnect = getattr(driver, "reconnect", None)
    if callable(reconnect):
        try:
            reconnect()
        except Exception:
            pass


def _guarded(
    driver: Any,
    action: Callable[[], Any],
    label: str,
    output: Callable[..., None],
    attempts: int = 3,
    result: list | None = None,
) -> bool:
    """Run a queue-store call, retrying transient errors. Never raises ``Exception``.

    The call's return value is appended to *result* when given.
    """
    for attempt in range(1, attempts + 1):
        try:
            value = action()
            if result is not None:
                result.append(value)
            return True
        except Exception as exc:
            output(f"  WARNING: could not {label} (attempt {attempt}/{attempts}): {exc}", err=True)
            if attempt < attempts:
                _reconnect(driver)
                _pause(min(0.5 * attempt, 2))
    log_error(f"Queue worker could not {label} after {attempts} attempts.")
    return False


def _idle(seconds: float, should_stop: Callable[[], bool]) -> None:
    """Sleep in short slices so a stop request is honoured promptly."""
    remaining = float(seconds)
    while remaining > 0 and not should_stop():
        step = min(0.25, remaining)
        _pause(step)
        remaining -= step


# ---------------------------------------------------------------------------
# Processing one job
# ---------------------------------------------------------------------------


def process_job(
    driver: Any,
    job_data: dict,
    *,
    queue: str = "default",
    tries: int = 3,
    output: Callable[..., None] = _default_output,
    completed: _RecentIds | None = None,
    guard: ReplayGuard | None = None,
) -> str:
    """Run one popped job and settle it with the driver.

    Returns ``"processed"``, ``"released"`` (will be retried), ``"failed"``
    (moved to the failed store), ``"duplicate"`` (the job id had already
    finished; the delivery was removed without running) or ``"deferred"``
    (another delivery of the same id is running, or the processed-id store
    could not be asked; the job was put back without running). Store errors
    are retried and logged; they never propagate and never cause a finished
    job to re-run.
    """
    if guard is None:
        guard = ReplayGuard(driver)
        guard.check()
    job_id = job_data["id"]
    payload_str = job_data["payload"]
    job_queue = job_data.get("queue", queue)
    try:
        attempts = int(job_data.get("attempts", 1))
    except (TypeError, ValueError):
        attempts = 1
    started = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    # 1. Trust: an unparseable or unverifiable payload can never succeed, so it
    #    is failed immediately instead of being retried.
    try:
        body = open_payload(payload_str, retention=guard.retention)
        if not valid_job_id(body.get("id")):
            raise InvalidPayload("Job payload has no valid job id, so it cannot be protected against replay.")
    except InvalidPayload as exc:
        reason = f"InvalidPayload: {exc}"
        output(f"  Rejected: {exc}", err=True)
        _guarded(driver, lambda: driver.fail(job_id, job_queue, payload_str, reason), "fail job", output)
        record_history("unknown", job_queue, _elapsed_ms(), "failed")
        return "failed"

    job_class = str(body.get("class", "unknown"))
    job_uid = body["id"]
    keep_from = due_time(body)

    def _drop_duplicate() -> str:
        message = f"{job_class} [{job_uid}] already finished; removing the duplicate delivery without running it."
        output(f"  Skipped: {message}", err=True)
        log_warning(f"Queue worker: {message}")
        _guarded(driver, lambda: driver.delete(job_id), "delete duplicate job", output)
        return "duplicate"

    def _put_back(delay: int) -> None:
        defer = getattr(driver, "defer", None)
        if callable(defer):
            _guarded(driver, lambda: defer(job_id, delay), "postpone job", output)
        else:
            _guarded(driver, lambda: driver.release(job_id, delay=delay), "postpone job", output)

    if completed is not None and job_uid in completed:
        if guard.active:
            # This worker ran it but could not record that; try again now.
            _guarded(driver, lambda: driver.complete(job_id, job_uid, keep_from), "record finished job", output)
        return _drop_duplicate()

    # 1b. Replay protection: a job id that finished anywhere is never run
    #     again, and one that is running elsewhere is not run alongside.
    claimed = False
    if guard.active:
        claim: list = []
        if _guarded(
            driver,
            lambda: driver.claim_run(job_uid, job_data),
            "check the processed-job store",
            output,
            result=claim,
        ):
            if claim[0] == "done":
                return _drop_duplicate()
            if claim[0] != "claimed":
                output(f"  Postponed: {job_class} [{job_uid}] is being run by another worker.", err=True)
                _put_back(_BUSY_DELAY)
                return "deferred"
            claimed = True
        elif guard.check():
            # The store exists but cannot be asked: do not run a job that may
            # already have finished. It is put back and tried again later.
            output(f"  Postponed: {job_class} [{job_uid}]: the processed-job store did not answer.", err=True)
            _put_back(30)
            return "deferred"
        # else: the store is gone. guard.check() has said so, and the worker
        # carries on with the in-process check only.

    # 2. Run.
    cls: type | None = None
    instance: Any = None
    error: BaseException | None = None
    try:
        cls = import_job_class(body.get("class"))
        if attempts > resolve_tries(cls, body, tries):
            raise MaxAttemptsExceeded(
                f"{job_class} was attempted {attempts - 1} time(s) without finishing "
                "(the worker crashed or the job outlived retry_after)."
            )
        instance = rebuild_job(cls, body.get("data", {}))
        job_timeout = int(body.get("timeout", getattr(instance, "timeout", 60)) or 0)
        run_with_timeout(lambda: call_handler(instance), job_timeout)
    except (Exception, JobTimeout) as exc:
        error = exc

    # 3. Failure path: retry or move to the failed store.
    if error is not None:
        output(f"  Failed: {job_class} — {error}", err=True)
        job_tries = resolve_tries(cls, body, tries)
        if attempts >= job_tries:
            if instance is not None:
                hook_exc = error if isinstance(error, Exception) else TimeoutError(str(error))
                try:
                    result = instance.failed(hook_exc)
                    if inspect.isawaitable(result):
                        run_awaitable(result)
                except Exception as hook_error:
                    log_error(f"{job_class}.failed() raised: {hook_error}")
            if claimed:
                # Recorded as finished together with the failure, so a copy of
                # this payload (for example from jobs_failed) cannot be queued
                # and run again. `queue:retry` gives the retry a new id.
                _guarded(
                    driver,
                    lambda: driver.fail(
                        job_id, job_queue, payload_str, str(error), job_uid=job_uid, keep_from=keep_from
                    ),
                    "fail job",
                    output,
                )
            else:
                _guarded(driver, lambda: driver.fail(job_id, job_queue, payload_str, str(error)), "fail job", output)
            record_history(job_class, job_queue, _elapsed_ms(), "failed")
            output(f"  Moved to failed after {attempts} attempt(s): {job_class}", err=True)
            return "failed"
        delay = backoff_delay(body.get("backoff", 0), attempts) or 30
        if claimed:
            # Give the lease back first, so the retry is not held up by it.
            _guarded(driver, lambda: driver.release_run(job_uid), "release the job's run lease", output)
        _guarded(driver, lambda: driver.release(job_id, delay=delay), "release job", output)
        return "released"

    # 4. Success path. handle() has returned, so nothing below may re-run it:
    #    bookkeeping errors are retried, logged and otherwise ignored.
    duration_ms = _elapsed_ms()
    chain = body.get("chain") or []
    if chain:
        first = dict(chain[0])
        first["chain"] = chain[1:]
        # Derived from this job's id: if this job is delivered again and the
        # continuation is pushed twice, both copies carry the same id and the
        # chained job still runs once.
        first["id"] = derive_job_id("chain", job_uid)
        first.pop("created_at", None)
        first.pop("available_at", None)
        # Push the continuation before deleting, so a crash here re-delivers
        # this job instead of silently dropping the rest of the chain.
        if not _guarded(
            driver, lambda: driver.push_payload(first, first.get("queue", queue)), "push chained job", output
        ):
            log_error(f"Chain after {job_class} was lost: could not enqueue {first.get('class', 'unknown')}.")
    if completed is not None:
        completed.add(job_uid)
    if claimed:
        # Removing the job and recording its id as finished is one step for
        # the driver, and the driver says whether this worker was the one to
        # record it.
        won: list = []
        _guarded(
            driver,
            lambda: driver.complete(job_id, job_uid, keep_from),
            "record and delete finished job",
            output,
            result=won,
        )
        if won and not won[0]:
            message = (
                f"{job_class} [{job_uid}] was also completed by another worker (it ran longer than retry_after); "
                "not recording this run."
            )
            output(f"  Duplicate: {message}", err=True)
            log_warning(f"Queue worker: {message}")
            return "duplicate"
    else:
        _guarded(driver, lambda: driver.delete(job_id), "delete finished job", output)
    record_history(job_class, job_queue, duration_ms, "completed")
    output(f"  Processed: {job_class}")
    return "processed"


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def run_worker(
    driver: Any,
    *,
    queue: str = "default",
    sleep: float = 3,
    tries: int = 3,
    once: bool = False,
    max_jobs: int = 0,
    max_time: float = 0,
    should_stop: Callable[[], bool] = lambda: False,
    output: Callable[..., None] = _default_output,
) -> int:
    """Pop and process jobs until told to stop. Returns the number of jobs handled.

    Stops when *should_stop()* is true, after one job (or an empty queue) with
    *once*, after *max_jobs* jobs, or once *max_time* seconds have passed.
    """
    started = time.monotonic()
    handled = 0
    pop_errors = 0
    completed = _RecentIds()
    guard = ReplayGuard(driver, output)
    guard.maintain(force=True)

    while not should_stop():
        if max_time and time.monotonic() - started >= max_time:
            break
        guard.maintain()
        try:
            job_data = driver.pop(queue)
            pop_errors = 0
        except Exception as exc:
            pop_errors += 1
            output(f"  WARNING: could not fetch the next job ({exc}); retrying.", err=True)
            if once and pop_errors >= 3:
                raise WorkerError(f"Could not fetch a job after {pop_errors} attempts: {exc}") from exc
            _reconnect(driver)
            _idle(min(max(float(sleep), 1.0) * 2 ** (pop_errors - 1), 60.0), should_stop)
            continue

        if job_data is None:
            if once:
                break
            _idle(sleep, should_stop)
            continue

        try:
            process_job(driver, job_data, queue=queue, tries=tries, output=output, completed=completed, guard=guard)
        except Exception as exc:  # defensive: process_job settles its own errors
            output(f"  ERROR: unexpected worker error: {exc}", err=True)
            log_error(f"Queue worker: unexpected error while processing a job: {exc}")
        handled += 1

        if once or (max_jobs and handled >= max_jobs):
            break

    return handled
