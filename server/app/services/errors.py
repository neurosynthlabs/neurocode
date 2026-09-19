"""What a service says when a rule says no.

One exception, carrying words a person can act on. Routes turn it into a status code; nothing else has
to know that HTTP exists. `Refused` is not an error in the "something broke" sense — it is the
product answering, so its message is written to be read on screen.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from ..ai.gateway import NoModel, ProviderError

T = TypeVar("T")

#: What a feature that cannot work without a model says when no lane can answer.
NO_MODEL = ("No model is configured. Add a free key in Admin → AI providers — Groq, Cerebras or Gemini "
            "take a minute.")


class Refused(RuntimeError):
    """A rule said no. `status` is what HTTP should make of it; the message is for the person."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class Denied(Refused):
    """The person is allowed to ask, but not to do this."""

    def __init__(self, permission: str, what: str = "") -> None:
        super().__init__(f"This needs the {permission} permission{f' to {what}' if what else ''}.", status=403)
        self.permission = permission


def needs_a_model(call: Callable[[], T]) -> T:
    """Run a call that has no honest answer without a model, turning the gateway's two failures into
    the refusals a screen can show: nothing configured is a 409 with the words that fix it, and a
    provider that failed is a 502 carrying the provider's own reason.

    Blocking, like the gateway it calls — the caller hands it to a worker thread.
    """
    try:
        return call()
    except NoModel as none:
        raise Refused(NO_MODEL, status=409) from none
    except ProviderError as failed:
        raise Refused(f"The model could not answer: {failed.body}", status=502) from failed
