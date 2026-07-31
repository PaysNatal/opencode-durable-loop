"""Async subprocess helpers for driving the zeroshot CLI from Temporal activities."""

import asyncio
import json
import os
import re
from typing import Optional


async def _run(cmd: list[str], cwd: str) -> str:
    """Run a command in `cwd`, return combined stdout+stderr as text."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return out.decode(errors="replace")


def parse_cluster_id(output: str) -> Optional[str]:
    """Extract the cluster id from `zeroshot run --detach` output ('Started <id>')."""
    m = re.search(r"Started\s+([a-zA-Z0-9-]+)", output)
    return m.group(1) if m else None


async def launch_cluster(
    task: str, flags: list[str], cwd: str
) -> tuple[Optional[str], str]:
    """Launch a detached zeroshot cluster. Returns (cluster_id, raw_output)."""
    output = await _run(["zeroshot", "run", task, "--detach", *flags], cwd)
    return parse_cluster_id(output), output


async def get_cluster_state(cluster_id: str, cwd: str) -> tuple[str, int]:
    """Return (state, totalTokens) for a cluster via `zeroshot list --json`."""
    output = await _run(["zeroshot", "list", "--json"], cwd)
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return ("", 0)
    for c in data.get("clusters", []):
        if c.get("id") == cluster_id:
            return (
                c.get("state") or c.get("status") or "",
                int(c.get("totalTokens") or 0),
            )
    return ("", 0)  # not found


async def get_cluster_created_at(cluster_id: str, cwd: str) -> int:
    """Return the cluster's createdAt timestamp (ms) via `zeroshot list --json`, or 0."""
    output = await _run(["zeroshot", "list", "--json"], cwd)
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return 0
    for c in data.get("clusters", []):
        if c.get("id") == cluster_id:
            return int(c.get("createdAt") or 0)
    return 0


async def get_status_json(cluster_id: str, cwd: str) -> dict:
    """Return the parsed `zeroshot status <id> --json` object ({} on failure)."""
    output = await _run(["zeroshot", "status", cluster_id, "--json"], cwd)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {}


async def get_failure_info(cluster_id: str, cwd: str) -> str:
    status = await get_status_json(cluster_id, cwd)
    return (status.get("failureInfo") or {}).get("error") or ""


async def get_max_validator_iteration(cluster_id: str, cwd: str) -> int:
    """Max iteration count among agents whose role contains 'validator' (0 if none ran)."""
    status = await get_status_json(cluster_id, cwd)
    iters = [
        int(a.get("iteration") or 0)
        for a in status.get("agents", [])
        if "validator" in (a.get("role") or "")
    ]
    return max(iters) if iters else 0


async def kill_cluster(cluster_id: str, cwd: str) -> None:
    await _run(["zeroshot", "kill", cluster_id], cwd)


async def cleanup_cluster_sessions(
    cluster_id: str, project_dir: str = "", cluster_created_at: int = 0
) -> int:
    """Delete opencode sessions created by a loop cluster (loop garbage).

    Zeroshot runs opencode agents (conductor/planner/worker/validators) which
    create sessions in opencode.db.  In zeroshot >= 6.12 these sessions live in
    the *project directory* (not in ~/.zeroshot/worktrees/).  We identify them
    by project directory + creation-time window, plus reformat title patterns.

    Best-effort: never raises, returns count deleted.
    """
    if not re.fullmatch(r"[A-Za-z0-9-]+", cluster_id or ""):
        return 0  # sanity guard against SQL injection via cluster_id
    db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if not os.path.exists(db):
        return 0

    # Build the WHERE clause:
    #   1. Legacy: sessions in the old worktree path (zeroshot < 6.12)
    #   2. New: sessions in the project directory created after the cluster started
    #   3. Reformat sessions (title pattern) created after the cluster started
    conditions = [f"directory LIKE '%/.zeroshot/worktrees/{cluster_id}%'"]
    if project_dir and cluster_created_at:
        proj = project_dir.replace("'", "''")
        conditions.append(
            f"(directory = '{proj}' AND time_created >= {cluster_created_at})"
        )
        conditions.append(
            f"(title LIKE '%Text-to-JSON%' AND time_created >= {cluster_created_at})"
        )
        conditions.append(
            f"(title LIKE '%Text to JSON%' AND time_created >= {cluster_created_at})"
        )
    where = " OR ".join(conditions)

    try:
        proc = await asyncio.create_subprocess_exec(
            "sqlite3",
            db,
            f"SELECT id FROM session WHERE {where};",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        ids = [
            ln.strip() for ln in out.decode(errors="replace").splitlines() if ln.strip()
        ]
        deleted = 0
        for sid in ids:
            p = await asyncio.create_subprocess_exec(
                "opencode",
                "session",
                "delete",
                sid,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await p.communicate()
            deleted += 1
        return deleted
    except Exception:
        return 0
