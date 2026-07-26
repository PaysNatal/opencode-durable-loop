"""Quick round-trip test for memorix_lib (store → search/inject → resolve)."""

import asyncio

import memorix_lib


async def main():
    vault = "~/memory-vault"
    print("vault usable:", memorix_lib.vault_usable(vault))

    lesson = {
        "task": "refactor the payment webhook handler",
        "failure_class": "stall",
        "root_cause": "agent stalled on large codegen",
        "lesson": "Break large codegen tasks into smaller steps; add a watchdog timeout",
        "cluster_id": "test-cluster-99",
        "files": ["payment.py"],
    }
    obs_id = await memorix_lib.store_lesson(vault, lesson)
    print("stored lesson, obs_id =", obs_id)

    ctx = await memorix_lib.build_lesson_context(vault, "refactor payment webhook")
    print("=== injection context for a related task ===")
    print(ctx)

    n = await memorix_lib.resolve_matching(vault, "refactor payment webhook")
    print("resolved", n, "lesson(s) on success")

    entries = await memorix_lib.search_lessons(vault, "refactor payment webhook")
    print(
        "active loop lessons after resolve:",
        len(entries),
        "(should be 0 — resolved is hidden)",
    )


if __name__ == "__main__":
    asyncio.run(main())
