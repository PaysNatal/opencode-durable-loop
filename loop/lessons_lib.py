"""Lesson storage — compatible with zs-learn's .zeroshot/lessons.jsonl format."""

import json
import os
import re
import secrets
from datetime import datetime, timezone

MAX_INJECT = 10
MAX_LINE_LEN = 160


def _lessons_file(project_dir: str) -> str:
    return os.path.join(project_dir, ".zeroshot", "lessons.jsonl")


def _ensure(project_dir: str) -> str:
    d = os.path.join(project_dir, ".zeroshot")
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, "lessons.jsonl")
    if not os.path.exists(f):
        open(f, "a").close()
    return f


def gen_id() -> str:
    return secrets.token_hex(2)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_all(project_dir: str) -> list[dict]:
    f = _lessons_file(project_dir)
    if not os.path.exists(f):
        return []
    out = []
    with open(f) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _write_all(project_dir: str, lessons: list[dict]) -> None:
    f = _ensure(project_dir)
    with open(f, "w") as fh:
        for lesson in lessons:
            fh.write(json.dumps(lesson, ensure_ascii=False) + "\n")


def count_unresolved(project_dir: str) -> int:
    return sum(1 for l in _read_all(project_dir) if not l.get("resolved"))


def build_lesson_context(project_dir: str, max_inject: int = MAX_INJECT) -> str:
    """Build the markdown block of unresolved lessons to inject into the task prompt."""
    unresolved = [l for l in _read_all(project_dir) if not l.get("resolved")]
    if not unresolved:
        return ""
    unresolved = list(reversed(unresolved))[:max_inject]  # most recent first
    lines = ["## Prior Failures — do NOT repeat these mistakes", ""]
    for l in unresolved:
        cls = l.get("failure_class", "unknown")
        task = l.get("task", "?")
        lesson = l.get("lesson") or l.get("verifier_finding") or "?"
        if len(lesson) > MAX_LINE_LEN:
            lesson = lesson[: MAX_LINE_LEN - 3] + "..."
        lines.append(f"- [{cls}] {task}: {lesson}")
    return "\n".join(lines) + "\n"


def append_lesson(project_dir: str, lesson: dict) -> None:
    f = _ensure(project_dir)
    with open(f, "a") as fh:
        fh.write(json.dumps(lesson, ensure_ascii=False) + "\n")


def _tokenize(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", text.lower()) if w]


def auto_resolve_matching(project_dir: str, task: str) -> int:
    """Resolve unresolved lessons whose task overlaps >50% with `task`. Returns count resolved.
    (Heuristic, same limitation as zs-learn — not ideal for CJK text.)"""
    task_words = set(_tokenize(task))
    if not task_words:
        return 0
    lessons = _read_all(project_dir)
    resolved = 0
    for l in lessons:
        if l.get("resolved"):
            continue
        lwords = set(_tokenize(l.get("task", "")))
        if len(task_words & lwords) > len(task_words) / 2:
            l["resolved"] = True
            resolved += 1
    if resolved:
        _write_all(project_dir, lessons)
    return resolved
