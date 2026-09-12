"""The agent's hands: git, the worktree, the project's own test command.

Everything in this package is blocking and knows nothing about the database, the web, or async. That
separation is the point — the rules that make a run safe to leave alone are here, in plain functions
that can be read and tested on their own:

  * nothing touches your working tree — every change happens in a worktree on a new branch;
  * nothing a model says is ever executed — it may only propose file contents, and a path that tries
    to leave the worktree is refused outright, never quietly rewritten;
  * the only command that runs is the project's own test command, found in the repository itself.

The service above it decides *when* to call these; this package decides *how*, and refuses when it
cannot do it safely.
"""
from .git import (
    TEST_RECIPES,
    Refused,
    apply_files,
    cleanup,
    commit,
    detect_tests,
    diff,
    free_branch,
    git,
    merge_branch,
    merge_into_checkout,
    open_worktree,
    repo_of,
    run_tests,
    safe_path,
    stats,
)

__all__ = [
    "TEST_RECIPES", "Refused", "apply_files", "cleanup", "commit", "detect_tests", "diff", "free_branch",
    "git", "merge_branch", "merge_into_checkout", "open_worktree", "repo_of", "run_tests", "safe_path",
    "stats",
]
