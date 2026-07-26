"""Error classification heuristics (ported from zs-learn)."""

import re


def classify_failure(text: str) -> str:
    """Classify a task-logic failure from verifier/output text into a lesson category."""
    t = (text or "").lower()
    if re.search(
        r"import|require|module not found|package|dependency|version conflict|npm err|cannot find module",
        t,
    ):
        return "dependency"
    if re.search(r"config|path|permission|denied|enoent|env|variable not set|\.env", t):
        return "config"
    if re.search(
        r"test|assert|expect|spec|jest|vitest|pytest|mocha|fail.*test|test.*fail", t
    ):
        return "test"
    if re.search(
        r"regression|broke|was working|used to|previously|side.effect|broke.*other", t
    ):
        return "regression"
    if re.search(
        r"wrong approach|should use|instead of|better to|architecture|design|pattern", t
    ):
        return "approach"
    return "logic"


def classify_infra_error(text: str) -> str:
    """Classify an INFRASTRUCTURE error (non-retryable). Returns auth|permission|provider-missing|none."""
    t = (text or "").lower()
    if re.search(
        r"not logged in|please run /login|please login|authentication|invalid api key|unauthorized|\b401\b",
        t,
    ):
        return "auth"
    if re.search(r"permission denied|access denied|forbidden|eacces|\b403\b", t):
        return "permission"
    if re.search(
        r"not found|no such file|command not found|provider.*(missing|unavailable)", t
    ):
        return "provider-missing"
    return "none"
