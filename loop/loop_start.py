"""Client: start a LoopWorkflow from the command line.

Usage:
  python3 loop_start.py "task description" [dloop options] [-- zeroshot flags...]

Examples:
  python3 loop_start.py "fix the bug" --detach -- --worktree --provider opencode
"""

import argparse
import asyncio
import os
import sys
import uuid

from temporalio.client import Client

from loop_workflow import LoopWorkflow
from models import LoopParams


async def main():
    # Tokens after '--' are passed verbatim to zeroshot (e.g. --worktree --provider opencode).
    argv = sys.argv[1:]
    zs_flags: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        zs_flags = argv[i + 1 :]
        argv = argv[:i]

    p = argparse.ArgumentParser(description="Start a durable loop workflow")
    p.add_argument("task", help="Task description")
    p.add_argument(
        "--project-dir",
        default=os.getcwd(),
        help="Project dir (zeroshot cwd + lessons)",
    )
    p.add_argument(
        "--flag", action="append", default=[], help="Extra zeroshot flag (repeatable)"
    )
    p.add_argument("--max-iterations", type=int, default=3)
    p.add_argument("--run-timeout-min", type=int, default=30)
    p.add_argument("--heartbeat-timeout-sec", type=int, default=90)
    p.add_argument("--poll-interval-sec", type=int, default=15)
    p.add_argument("--max-stall-polls", type=int, default=8)
    p.add_argument(
        "--memory-vault",
        default=os.path.expanduser("~/memory-vault"),
        help="Memorix vault for shared lessons (default ~/memory-vault; falls back to lessons.jsonl if not a git repo)",
    )
    p.add_argument(
        "--detach",
        action="store_true",
        help="Start and return immediately (workflow runs durably in background)",
    )
    args = p.parse_args(argv)

    params = LoopParams(
        task=args.task,
        project_dir=os.path.abspath(args.project_dir),
        flags=list(args.flag) + zs_flags,
        max_iterations=args.max_iterations,
        run_timeout_min=args.run_timeout_min,
        heartbeat_timeout_sec=args.heartbeat_timeout_sec,
        poll_interval_sec=args.poll_interval_sec,
        max_stall_polls=args.max_stall_polls,
        memory_vault=args.memory_vault,
    )

    client = await Client.connect("localhost:7233", namespace="loop")
    wf_id = f"loop-{uuid.uuid4().hex[:8]}"
    print(f"Starting LoopWorkflow id={wf_id} task_queue=loop-queue", flush=True)

    if args.detach:
        handle = await client.start_workflow(
            LoopWorkflow.run,
            params,
            id=wf_id,
            task_queue="loop-queue",
        )
        print(f"\n✓ Started durable loop workflow: {handle.id}")
        print(
            "  The workflow runs durably in Temporal — it survives even if you close this terminal."
        )
        print(
            f"  Status:  temporal workflow describe --workflow-id {handle.id} --namespace loop"
        )
        print(
            f"  Result:  temporal workflow result {handle.id} --namespace loop   (waits for completion)"
        )
        return

    result = await client.execute_workflow(
        LoopWorkflow.run,
        params,
        id=wf_id,
        task_queue="loop-queue",
    )
    print("\n=== LoopResult ===")
    print(f"  success:      {result.success}")
    print(f"  attempts:     {result.attempts}")
    print(f"  reason:       {result.reason}")
    print(f"  verified:     {result.verified}")
    print(f"  detail:       {result.detail}")
    print(f"  last_cluster: {result.last_cluster_id}")


if __name__ == "__main__":
    asyncio.run(main())
