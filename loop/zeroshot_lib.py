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


async def cleanup_cluster_sessions(cluster_id: str) -> int:
    """Delete opencode sessions created in the cluster's worktree (loop garbage).

    Each zeroshot cluster runs opencode (conductor/planner/worker/validators)
    inside ~/.zeroshot/worktrees/<cluster-id>; those sessions linger in
    opencode.db after the cluster finishes. This removes them so the session
    store doesn't fill up. Best-effort: never raises, returns count deleted.
    """
    if not re.fullmatch(r"[A-Za-z0-9-]+", cluster_id or ""):
        return 0  # sanity guard against SQL injection via cluster_id
    db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if not os.path.exists(db):
        return 0
    pattern = f"%/.zeroshot/worktrees/{cluster_id}%"
    try:
        proc = await asyncio.create_subprocess_exec(
            "sqlite3",
            db,
            f"SELECT id FROM session WHERE directory LIKE '{pattern}';",
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
