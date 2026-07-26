"""Memorix integration for loop lessons (drives the memorix CLI against a memory vault).

The loop stores its failure lessons in a centralized git-backed memory vault
(default ~/memory-vault) so that:
  - lessons are shared across ALL projects (a fix learned in project A helps project B),
  - it works regardless of whether the task's own project is a git repo,
  - new projects automatically benefit (the loop always targets the vault).

If the vault is not usable (not a git repo), callers fall back to lessons.jsonl.
"""

import asyncio
import json
import os
from typing import Optional

DEFAULT_VAULT = os.path.expanduser("~/memory-vault")
MAX_INJECT = 10
RESOLVE_CAP = 3  # max lessons to auto-resolve on a success (stay conservative)

# Map loop failure_class → memorix observation type.
_TYPE_MAP = {
    "stall": "gotcha",
    "lost": "gotcha",
    "logic": "problem-solution",
    "dependency": "problem-solution",
    "config": "problem-solution",
    "test": "problem-solution",
    "regression": "problem-solution",
    "approach": "problem-solution",
}


def expand_vault(vault: str) -> str:
    return os.path.expanduser(vault or DEFAULT_VAULT)


def vault_usable(vault: str) -> bool:
    """Usable iff the vault is a directory containing a .git folder."""
    return os.path.isdir(os.path.join(expand_vault(vault), ".git"))


async def _memorix(vault: str, args: list[str]) -> Optional[dict]:
    """Run a memorix CLI command with cwd=vault; return parsed JSON or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "memorix",
            *args,
            cwd=expand_vault(vault),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except (OSError, FileNotFoundError):
        return None
    text = out.decode(errors="replace")
    idx = text.find("{")
    if idx == -1:
        return None
    try:
        return json.loads(text[idx:])
    except json.JSONDecodeError:
        return None


def _obs_type(failure_class: str) -> str:
    if failure_class.startswith("infra-"):
        return "gotcha"
    return _TYPE_MAP.get(failure_class, "problem-solution")


async def store_lesson(vault: str, lesson: dict) -> Optional[int]:
    """Store a loop lesson as a memorix observation. Returns the observation id."""
    fc = lesson.get("failure_class", "logic")
    lesson_text = lesson.get("lesson") or lesson.get("root_cause") or "lesson"
    title = f"[{fc}] {lesson_text[:80]}"
    narrative = (
        f"Task: {lesson.get('task', '?')}\n"
        f"Failure class: {fc}\n"
        f"Cause: {lesson.get('root_cause', '?')}\n"
        f"Lesson: {lesson_text}\n"
        f"Cluster: {lesson.get('cluster_id', '?')}"
    )
    facts = f"failure_class={fc},cluster_id={lesson.get('cluster_id', '')},source=zeroshot-loop"
    concepts = f"zeroshot,loop,failure,{fc}"
    args = [
        "memory",
        "store",
        "--text",
        narrative,
        "--title",
        title,
        "--type",
        _obs_type(fc),
        "--entity",
        "loop",
        "--facts",
        facts,
        "--concepts",
        concepts,
        "--json",
    ]
    files = lesson.get("files") or []
    if files:
        args += ["--files", ",".join(files)]
    data = await _memorix(vault, args)
    if data and "observation" in data:
        return data["observation"].get("id")
    return None


async def search_lessons(vault: str, query: str, limit: int = MAX_INJECT) -> list[dict]:
    """Search active loop lessons relevant to `query` (entity == 'loop')."""
    data = await _memorix(
        vault, ["memory", "search", "--query", query, "--limit", str(limit), "--json"]
    )
    if not data:
        return []
    return [e for e in data.get("entries", []) if e.get("entityName") == "loop"]


async def build_lesson_context(
    vault: str, task: str, max_inject: int = MAX_INJECT
) -> str:
    """Build the injection block from memorix lessons relevant to the task.
    Uses each lesson's title (which encodes '[failure_class] lesson') — no extra detail lookups."""
    entries = await search_lessons(vault, task, limit=max_inject)
    if not entries:
        return ""
    lines = ["## Prior Failures — do NOT repeat these mistakes", ""]
    for e in entries[:max_inject]:
        title = e.get("title", "?")
        lines.append(f"- {title}")
    return "\n".join(lines) + "\n"


async def resolve_matching(vault: str, task: str) -> int:
    """Resolve a few active loop lessons relevant to a succeeded task. Returns count resolved."""
    entries = await search_lessons(vault, task, limit=RESOLVE_CAP * 2)
    ids = [str(e["id"]) for e in entries[:RESOLVE_CAP] if e.get("id")]
    if not ids:
        return 0
    data = await _memorix(
        vault,
        ["memory", "resolve", "--ids", ",".join(ids), "--status", "resolved", "--json"],
    )
    return len(ids) if data else 0
