"""What a service says when a rule says no.

One exception, carrying words a person can act on. Routes turn it into a status code; nothing else has
to know that HTTP exists. `Refused` is not an error in the "something broke" sense — it is the
product answering, so its message is written to be read on screen.
"""
from __future__ import annotations


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
