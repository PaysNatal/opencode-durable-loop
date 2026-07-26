"""Worker: polls the 'loop-queue' task queue and runs LoopWorkflow + its activities."""

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from loop_workflow import LoopWorkflow, analyze_and_capture, inject_lessons, run_cluster


async def main():
    client = await Client.connect("localhost:7233", namespace="loop")
    worker = Worker(
        client,
        task_queue="loop-queue",
        workflows=[LoopWorkflow],
        activities=[inject_lessons, run_cluster, analyze_and_capture],
    )
    print("Loop worker started, polling 'loop-queue'...", flush=True)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
