"""nc — NeuroCode in the terminal.

A client of the same API the web app and the desktop app use, signed in with a personal access token.
It holds no logic of its own that the server does not also enforce: every permission, gate and rule is
the server's, so a terminal can never do more than the person could do in a browser.
"""
from __future__ import annotations

__version__ = "0.1.0"
