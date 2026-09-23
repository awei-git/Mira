"""Process-scoped paid-step receipts shared by the writer's worker threads.

Only the finite content batch installs a guard. Legacy app/model calls are
unchanged. No prompts, keys, or personal conversation are saved here.
"""

from contextlib import contextmanager
from functools import wraps
import json
import threading
import uuid

from content_worker.files import atomic_write, audit, checksum, safe_file, write_json

_scope_lock = threading.Lock()
_active = None


class PaidCallBlocked(RuntimeError):
    pass


def guard_active():
    return _active is not None


class ModelGuard:
    def __init__(self, ledger, obligation, repo, route=None):
        self.ledger, self.job, self.repo = ledger, obligation, repo
        self.route = route
        self.lock = threading.Lock()
        self.failed = False

    def call(self, function, model_name, prompt, system, timeout):
        model_name = self.route or model_name
        with self.lock:
            if self.failed:
                raise PaidCallBlocked("prior_model_step_uncertain")
            step_id = uuid.uuid4().hex
            prefix = self.job["payload"]["draft_dir"] + "/steps/" + step_id
            receipt_path = prefix + "/receipt.json"
            reservation = {
                "obligation_id": self.job["id"],
                "revision": self.job["revision"],
                "step_id": step_id,
                "route": model_name,
                "status": "reserved",
                "input_sha256": checksum(json.dumps([model_name, prompt, system], ensure_ascii=False).encode()),
                "provider_request_id": None,
                "usage": None,
            }
            audit(self.repo, "Reserve paid model step before dispatch", receipt_path)
            write_json(safe_file(self.repo, receipt_path), reservation)
            try:
                self.ledger.begin_step(self.job["id"], "mira-aws", self.job["revision"], step_id, receipt_path)
            except BaseException:
                self.failed = True
                raise
        try:
            # Concurrent writer calls share this guard; previously dispatched
            # requests may finish, but a failed step prevents any new dispatch.
            result = function(model_name, prompt, system, timeout)
            if not isinstance(result, str) or not result.strip():
                raise PaidCallBlocked("empty_or_uncertain_provider_response")
            response_path = prefix + "/response.txt"
            atomic_write(safe_file(self.repo, response_path), result.encode())
            outcome = {
                "response_path": response_path,
                "response_sha256": checksum(result.encode()),
                "usage": None,
                "usage_note": "Provider usage remains in the existing usage log; unknown here.",
            }
            write_json(safe_file(self.repo, receipt_path), {**reservation, **outcome, "status": "completed"})
            self.ledger.finish_step(self.job["id"], "mira-aws", self.job["revision"], step_id, outcome)
            audit(self.repo, "Retain model step receipt; no implied publication", receipt_path)
            return result
        except BaseException:
            with self.lock:
                self.failed = True
            # Leave the pre-call reservation for reconciliation. Do not turn an
            # uncertain outcome into an automatic fallback or a fresh paid run.
            raise


@contextmanager
def model_guard(ledger, obligation, repo, *, route=None):
    global _active
    if route is not None and (not isinstance(route, str) or not route.strip()):
        raise PaidCallBlocked("configured_model_route_required")
    if not _scope_lock.acquire(blocking=False):
        raise PaidCallBlocked("another_content_batch_in_process")
    guard = ModelGuard(ledger, obligation, repo, route=route)
    try:
        _active = guard
        yield guard
        if guard.failed:
            raise PaidCallBlocked("model_step_failed_or_uncertain")
    finally:
        _active = None
        _scope_lock.release()


def guard_model_call(function):
    @wraps(function)
    def wrapped(model_name, prompt, system, timeout):
        guard = _active
        if guard is None:
            return function(model_name, prompt, system, timeout)
        return guard.call(function, model_name, prompt, system, timeout)

    return wrapped
