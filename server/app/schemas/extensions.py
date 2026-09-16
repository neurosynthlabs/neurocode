"""The extension screens' JSON: a file found on disk, and what sessions really did with it.

Usage is passed in beside the file because the two come from different places — the file from disk,
the counts from `chat_messages` — and a skill no session has touched has no row at all, which is a
real zero, not a missing value.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .work import when

if TYPE_CHECKING:
    from ..services.extensions import CommandFile, HookEntry, SkillFile


def skill_json(skill: SkillFile, used: dict[str, Any] | None) -> dict[str, Any]:
    """A skill as the Skills screen reads it. `enabled` is the default; the screen merges the pref over it."""
    return {
        "id": skill.key, "name": skill.name, "slug": skill.slug, "description": skill.description,
        "scope": skill.scope, "project": skill.project, "source": skill.source, "enabled": True,
        "triggers": list(skill.triggers), "tools": list(skill.tools),
        # Who started the sessions that really loaded it — never an agent's declared list.
        "usedBy": used["by"] if used else [],
        "invocations": used["ever"] if used else 0, "loads24h": used["day"] if used else 0,
        "lastUsed": (when(used["last"]) or "") if used else "",
        "version": skill.version, "tokens": skill.tokens, "rules": [], "author": skill.author,
    }


def command_json(command: CommandFile, used: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": command.key, "name": command.name, "description": command.description, "scope": command.scope,
        "args": command.args, "agent": command.agent, "model": command.model, "tools": list(command.tools),
        "runs": used["ever"] if used else 0, "lastRun": (when(used["last"]) or "") if used else "",
        "body": command.body, "truncated": command.truncated, "source": command.source, "enabled": True,
    }


def hook_json(hook: HookEntry) -> dict[str, Any]:
    """A hook as found. There are no firing counts: Claude Code runs hooks and keeps no readable log of
    it, and NeuroCode never runs one — so none is invented."""
    return {
        "id": hook.id, "event": hook.event, "matcher": hook.matcher, "type": hook.type,
        "command": hook.command, "scope": hook.scope, "source": hook.source, "blocking": hook.blocking,
        "description": hook.description, "timeoutS": hook.timeout_s,
    }
