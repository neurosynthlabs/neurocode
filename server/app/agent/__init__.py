"""The agent's hands: git, the worktree, the project's own test command.

Everything in this package is blocking and knows nothing about the database, the web, or async. That
separation is the point — the rules that make a run safe to leave alone are here, in plain functions
that can be read and tested on their own:

  * nothing touches your working tree — every change happens in a worktree on a new branch;
  * nothing a model says is ever executed — it may only propose file contents, and a path that tries
    to leave the worktree is refused outright, never quietly rewritten;
  * the only commands that run are the project's own — its test command and its lint and typecheck
    checks — found in the repository itself, each only after a person allowed it.

The service above it decides *when* to call these; this package decides *how*, and refuses when it
cannot do it safely.
"""
from .git import (
    CHECK_NAMES,
    DIRTY,
    TEST_RECIPES,
    Refused,
    apply_files,
    branch_diff,
    cleanup,
    commit,
    compare_url,
    detect_checks,
    detect_tests,
    diff,
    dirty,
    fingerprint,
    free_branch,
    git,
    head,
    label_patch,
    merge_branch,
    merge_into_checkout,
    open_worktree,
    push,
    remotes,
    repo_of,
    reset_worktree,
    run_tests,
    safe_path,
    stats,
    take_back_merge,
    undo_merge,
    unmerge_refusal,
)

__all__ = [
    "CHECK_NAMES", "DIRTY", "TEST_RECIPES", "Refused", "apply_files", "branch_diff", "cleanup", "commit", "compare_url",
    "detect_checks", "detect_tests", "diff", "dirty", "fingerprint", "free_branch", "git", "head", "label_patch",
    "merge_branch", "merge_into_checkout", "open_worktree", "push", "remotes", "repo_of", "reset_worktree", "run_tests",
    "safe_path", "stats", "take_back_merge", "undo_merge", "unmerge_refusal",
]
