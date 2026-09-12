"""Services: what the product *does*, with nothing about HTTP or SQL in it.

A route's job becomes small — check who is asking, hand the request to a service, turn the answer into
JSON. A service's job is the rule itself: what may move to what, what must be settled before a plan
can be dispatched, who is allowed to sign. Written this way, a rule can be tested by calling a
function, and read without holding a web framework in your head.

Services raise `Refused` when a rule says no. The reason is written for the person who will read it
on screen, not for a log file.
"""
from .errors import Refused
from .work import PlanQuestions, TaskService

__all__ = ["PlanQuestions", "Refused", "TaskService"]
