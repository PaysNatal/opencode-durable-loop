"""Durable executor-verifier loop on Temporal.

Wraps zeroshot (the multi-agent executor) in a Temporal workflow to get:
  - durable state (crash recovery — resume after worker/host death)
  - activity heartbeats (native stall/crash detection)
  - retry policies with error classification (fail-fast on infra errors)
  - lesson accumulation across retries (Reflexion-style)
"""

import asyncio
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

import classify
import lessons_lib
import memorix_lib
import zeroshot_lib
from models import (
    FAILURE_STATES,
    TERMINAL_STATES,
    Analysis,
    AnalyzeInput,
    ClusterOutcome,
    LoopParams,
    LoopResult,
    RunClusterInput,
)

# Retry policy for launching/polling the cluster: retry transient failures with
# backoff, but FAIL FAST on infrastructure errors (auth/permission/missing provider).
RUN_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=3,
    non_retryable_error_types=[
        "infra_auth",
        "infra_permission",
        "infra_provider_missing",
    ],
)


# ── Lesson backend helpers (memorix primary, lessons.jsonl fallback) ─────


async def _capture(vault: str, project_dir: str, lesson: dict) -> str:
    """Store a lesson to memorix (if the vault is usable) else lessons.jsonl. Returns an id."""
    if memorix_lib.vault_usable(vault):
        obs_id = await memorix_lib.store_lesson(vault, lesson)
        if obs_id:
            return f"memorix#{obs_id}"
    lid = lessons_lib.gen_id()
    record = {
        "id": lid,
        "ts": lessons_lib.now_iso(),
        "task": lesson.get("task", "?"),
        "failure_class": lesson.get("failure_class", "logic"),
        "verifier_finding": lesson.get("verifier_finding", ""),
        "root_cause": lesson.get("root_cause", ""),
        "lesson": lesson.get("lesson", ""),
        "cluster_id": lesson.get("cluster_id", ""),
        "files": lesson.get("files", []),
        "resolved": False,
    }
    lessons_lib.append_lesson(project_dir, record)
    return lid


async def _resolve(vault: str, project_dir: str, task: str) -> None:
    """Auto-resolve lessons matching a succeeded task (memorix or jsonl)."""
    if memorix_lib.vault_usable(vault):
        await memorix_lib.resolve_matching(vault, task)
    else:
        lessons_lib.auto_resolve_matching(project_dir, task)


# ── Activities ──────────────────────────────────────────────────────────


@activity.defn
async def inject_lessons(task: str, project_dir: str, memory_vault: str) -> str:
    """Prepend prior-failure lessons to the task prompt.

    Prefers memorix (shared across projects, relevance-ranked) when the vault is
    usable; falls back to the local lessons.jsonl otherwise.
    """
    if memorix_lib.vault_usable(memory_vault):
        ctx = await memorix_lib.build_lesson_context(memory_vault, task)
        if ctx:
            activity.logger.info("Injecting memorix lessons (vault=%s)", memory_vault)
            return f"{ctx}\n---\n\n{task}"
        activity.logger.info("No relevant memorix lessons — running clean")
        return task
    ctx = lessons_lib.build_lesson_context(project_dir)
    if ctx:
        n = lessons_lib.count_unresolved(project_dir)
        activity.logger.info("Injecting %d jsonl lesson(s) (memorix not usable)", n)
        return f"{ctx}\n---\n\n{task}"
    activity.logger.info("No prior failures — running clean")
    return task


@activity.defn
async def run_cluster(inp: RunClusterInput) -> ClusterOutcome:
    """Launch a zeroshot cluster (detached) and poll it to a terminal state.

    Heartbeats every poll with (cluster_id, state, tokens). On retry after a
    heartbeat timeout, reattaches to the already-running cluster (via
    heartbeat_details) instead of launching a duplicate.
    """
    cwd = inp.project_dir

    # ── Reattach if this is a retry after heartbeat timeout ──
    cluster_id = None
    details = activity.info().heartbeat_details
    if details:
        cluster_id = details[0]
        activity.logger.info(
            "Reattaching to existing cluster %s (after retry)", cluster_id
        )

    if not cluster_id:
        cluster_id, output = await zeroshot_lib.launch_cluster(
            inp.full_task, inp.flags, cwd
        )
        if not cluster_id:
            # Classify the launch failure: infra errors fail fast (non-retryable).
            err = classify.classify_infra_error(output)
            if err != "none":
                raise ApplicationError(
                    f"Cluster launch failed — infra error ({err}): {output[:300]}",
                    type=f"infra_{err.replace('-', '_')}",
                    non_retryable=True,
                )
            raise ApplicationError(
                f"Cluster launch failed: {output[:300]}", type="launch_error"
            )
        activity.logger.info("Launched cluster %s", cluster_id)

    # ── Poll with heartbeat + stall detection ──
    last_tokens = -1
    stall = 0
    while True:
        state, tokens = await zeroshot_lib.get_cluster_state(cluster_id, cwd)
        # Heartbeat reports the cluster id (for reattach) + progress. Synchronous in 1.30.0.
        activity.heartbeat(cluster_id, state, tokens)

        if state in TERMINAL_STATES or state in FAILURE_STATES:
            failure_info = ""
            if state in FAILURE_STATES:
                failure_info = await zeroshot_lib.get_failure_info(cluster_id, cwd)
            return ClusterOutcome(
                cluster_id=cluster_id,
                state=state,
                tokens=tokens,
                failure_info=failure_info,
            )

        if state == "":
            # Cluster vanished from the list (killed/cleaned externally).
            return ClusterOutcome(
                cluster_id=cluster_id,
                state="lost",
                tokens=tokens,
                failure_info="cluster disappeared from list",
            )

        # Stall detection: zero token progress after work began.
        if tokens > 0 and tokens == last_tokens:
            stall += 1
            if stall >= inp.max_stall_polls:
                activity.logger.warning(
                    "STALL: %s zero progress for %d polls — killing", cluster_id, stall
                )
                await zeroshot_lib.kill_cluster(cluster_id, cwd)
                return ClusterOutcome(
                    cluster_id=cluster_id,
                    state="stalled",
                    tokens=tokens,
                    failure_info="zero token progress (watchdog)",
                )
        else:
            stall = 0
            last_tokens = tokens

        await asyncio.sleep(inp.poll_interval_sec)


@activity.defn
async def analyze_and_capture(inp: AnalyzeInput) -> Analysis:
    """Post-run analysis: verification gate + error classification + lesson capture.

    Lessons are stored via memorix (shared vault) when usable, else lessons.jsonl.
    """
    outcome = inp.outcome
    state = outcome.state
    cid = outcome.cluster_id
    project_dir = inp.project_dir
    vault = inp.memory_vault

    # ── stalled ──
    if state == "stalled":
        lid = await _capture(
            vault,
            project_dir,
            {
                "task": inp.task,
                "failure_class": "stall",
                "verifier_finding": f"Cluster {cid} made zero token progress and was killed by the watchdog",
                "root_cause": "agent stalled / hung (zero token progress)",
                "lesson": "This task shape can hang the agent — break it into smaller steps and/or add a verification timeout",
                "cluster_id": cid,
                "files": [],
            },
        )
        return Analysis(status="stalled", detail="zero token progress", lesson_id=lid)

    # ── lost ──
    if state == "lost":
        return Analysis(
            status="infra_error",
            detail="cluster disappeared (killed/cleaned) before completion",
        )

    # ── success (stopped) → verification gate ──
    if state == "stopped":
        max_iter = await zeroshot_lib.get_max_validator_iteration(cid, project_dir)
        if max_iter > 0:
            await _resolve(vault, project_dir, inp.task)
            return Analysis(
                status="success",
                detail=f"validator ran (max iter {max_iter})",
                verified=True,
            )
        return Analysis(
            status="unverified_success",
            detail="no validator ran — result not independently verified",
            verified=False,
        )

    # ── failure states ──
    if state in FAILURE_STATES:
        err = classify.classify_infra_error(outcome.failure_info)
        if err != "none":
            lid = await _capture(
                vault,
                project_dir,
                {
                    "task": inp.task,
                    "failure_class": f"infra-{err}",
                    "verifier_finding": outcome.failure_info or "<no detail>",
                    "root_cause": f"infrastructure error: {err}",
                    "lesson": f"Fix the provider ({err}) before retrying — not a task-logic problem",
                    "cluster_id": cid,
                    "files": [],
                },
            )
            return Analysis(
                status="infra_error",
                detail=f"{err}: {outcome.failure_info}",
                lesson_id=lid,
            )
        # Genuine task failure → capture a lesson.
        fc = classify.classify_failure(outcome.failure_info)
        lid = await _capture(
            vault,
            project_dir,
            {
                "task": inp.task,
                "failure_class": fc,
                "verifier_finding": (outcome.failure_info or "task failed")[:200],
                "root_cause": (outcome.failure_info or "task failed")[:200],
                "lesson": f"Task failed ({fc}). See zeroshot export {cid} for details.",
                "cluster_id": cid,
                "files": [],
            },
        )
        return Analysis(
            status="task_failure",
            detail=outcome.failure_info or "task failed",
            lesson_id=lid,
        )

    return Analysis(status="task_failure", detail=f"unknown terminal state: {state}")


# ── Workflow ────────────────────────────────────────────────────────────


@workflow.defn
class LoopWorkflow:
    """Durable executor-verifier loop with lesson accumulation.

    Each iteration: inject lessons → run zeroshot cluster → analyze.
    On task-logic failure, the captured lesson is injected into the next attempt.
    Infra errors stop the loop immediately (environment problems, not task logic).
    """

    @workflow.run
    async def run(self, params: LoopParams) -> LoopResult:
        task = params.task
        last_cid = ""
        for attempt in range(params.max_iterations):
            # 1. Inject prior-failure lessons (accumulated from earlier attempts).
            full_task = await workflow.execute_activity(
                inject_lessons,
                args=[task, params.project_dir, params.memory_vault],
                start_to_close_timeout=timedelta(seconds=30),
            )
            # 2. Run the cluster (durable: heartbeat + retry policy).
            outcome = await workflow.execute_activity(
                run_cluster,
                RunClusterInput(
                    full_task=full_task,
                    project_dir=params.project_dir,
                    flags=params.flags,
                    poll_interval_sec=params.poll_interval_sec,
                    max_stall_polls=params.max_stall_polls,
                ),
                start_to_close_timeout=timedelta(minutes=params.run_timeout_min),
                heartbeat_timeout=timedelta(seconds=params.heartbeat_timeout_sec),
                retry_policy=RUN_RETRY_POLICY,
            )
            last_cid = outcome.cluster_id
            # 3. Analyze + capture lesson.
            analysis = await workflow.execute_activity(
                analyze_and_capture,
                AnalyzeInput(
                    task=task,
                    project_dir=params.project_dir,
                    outcome=outcome,
                    memory_vault=params.memory_vault,
                ),
                start_to_close_timeout=timedelta(seconds=60),
            )

            if analysis.status in ("success", "unverified_success"):
                return LoopResult(
                    success=True,
                    attempts=attempt + 1,
                    reason="success",
                    detail=analysis.detail,
                    last_cluster_id=last_cid,
                    verified=analysis.verified,
                )
            if analysis.status == "infra_error":
                return LoopResult(
                    success=False,
                    attempts=attempt + 1,
                    reason="infra_error",
                    detail=analysis.detail,
                    last_cluster_id=last_cid,
                )
            # stalled or task_failure → retry if attempts remain (lesson now recorded)
            if attempt >= params.max_iterations - 1:
                return LoopResult(
                    success=False,
                    attempts=attempt + 1,
                    reason=analysis.status,
                    detail=analysis.detail,
                    last_cluster_id=last_cid,
                )

        return LoopResult(
            success=False,
            attempts=params.max_iterations,
            reason="max_iterations",
            last_cluster_id=last_cid,
        )
