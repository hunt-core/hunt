from __future__ import annotations

import importlib
import inspect
import re
from typing import Any

from hunt.queue.job import Job

# Registry of permitted listener/event dotted paths.
# EventServiceProvider populates this at boot time.
_ALLOWED_LISTENER_CLASSES: set[str] = set()
_ALLOWED_EVENT_CLASSES: set[str] = set()

_DOTTED_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")


def allow_listener(dotted_path: str) -> None:
    _ALLOWED_LISTENER_CLASSES.add(dotted_path)


def allow_event(dotted_path: str) -> None:
    _ALLOWED_EVENT_CLASSES.add(dotted_path)


class QueuedEventListener(Job):
    """Internal wrapper job that runs a queued event listener.

    The EventServiceProvider creates one of these whenever it sees a
    listener class that is a ``Job`` subclass or carries
    ``implements_queued_listener = True``.  The job stores enough
    information to reconstruct both the listener and the event when the
    queue worker picks it up later.
    """

    def __init__(
        self,
        listener_class: str,
        event_class: str,
        event_data: dict[str, Any],
    ) -> None:
        self.listener_class = listener_class
        self.event_class = event_class
        self.event_data = event_data

    def handle(self) -> None:
        # Validate against allowlists before importing anything
        if self.listener_class not in _ALLOWED_LISTENER_CLASSES:
            raise ValueError(
                f"Refusing to execute unregistered listener: {self.listener_class!r}. "
                "Register it via EventServiceProvider.listen."
            )
        if self.event_class not in _ALLOWED_EVENT_CLASSES:
            raise ValueError(f"Refusing to deserialize unregistered event: {self.event_class!r}.")

        # Reconstruct the listener
        listener_cls = _import_dotted(self.listener_class)
        listener = listener_cls()

        # Reconstruct the event from its stored public attributes
        event_cls = _import_dotted(self.event_class)
        event = object.__new__(event_cls)
        for key, value in self.event_data.items():
            setattr(event, key, value)

        # Call handle() — pass the event if the signature accepts arguments
        sig = inspect.signature(listener.handle)
        positional = [
            p
            for p in sig.parameters.values()
            if p.name != "self"
            and p.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            )
        ]
        if positional:
            listener.handle(event)
        else:
            listener.handle()


def _import_dotted(dotted_path: str) -> type:
    if not _DOTTED_PATH_RE.match(dotted_path):
        raise ValueError(f"Invalid dotted path: {dotted_path!r}")
    module_path, class_name = dotted_path.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)
