"""The web layer: dependencies, error translation, and routes that stay thin.

A route's whole job is to say who is asking, hand the request to a service, and turn the answer into
JSON. Nothing here holds a rule. That is why the rules can be tested without a web request, and why a
route can be read in one sitting.
"""
from .deps import COOKIE, current_person, database, identity_service, require, require_any, session, token_from
from .errors import install_error_handlers

__all__ = [
    "COOKIE", "current_person", "database", "identity_service", "install_error_handlers", "require",
    "require_any", "session", "token_from",
]
