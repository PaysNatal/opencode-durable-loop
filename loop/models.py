"""Data models for the durable loop workflow (serialized by Temporal's data converter)."""

from dataclasses import dataclass, field


@dataclass
class LoopParams:
    """Input to LoopWorkflow."""

    task: str
    project_dir: str  # where zeroshot runs + where .zeroshot/lessons.jsonl lives
    flags: list[str] = field(
        default_factory=list
    )  # extra zeroshot flags (e.g. --worktree, --provider opencode)
    max_iterations: int = (
        3  # max executor-verifier iterations (retry with learned lessons)
    )
    run_timeout_min: int = 30  # start_to_close timeout for ONE cluster run
    heartbeat_timeout_sec: int = (
        90  # no heartbeat within this => Temporal reschedules the activity
    )
    poll_interval_sec: int = 15  # how often run_cluster polls the cluster
    max_stall_polls: int = 8  # consecutive zero-progress polls => stall
    memory_vault: str = (
        "~/memory-vault"  # memorix vault for shared lessons (fallback: lessons.jsonl)
    )
    clean_sessions: bool = (
        True  # delete the cluster's opencode sessions after it finishes (anti-garbage)
    )


@dataclass
class RunClusterInput:
    """Input to the run_cluster activity."""

    full_task: str  # task with lessons already injected
    project_dir: str
    flags: list[str] = field(default_factory=list)
    poll_interval_sec: int = 15
    max_stall_polls: int = 8
    clean_sessions: bool = (
        True  # delete the cluster's opencode sessions when it finishes
    )


# Terminal cluster states (zeroshot).
TERMINAL_STATES = {"stopped", "killed", "failed", "corrupted", "zombie", "stalled"}
FAILURE_STATES = {"failed", "zombie", "corrupted", "error", "rejected", "stalled"}


@dataclass
class ClusterOutcome:
    """Result of the run_cluster activity."""

    cluster_id: str
    state: str  # stopped / failed / zombie / stalled / ...
    tokens: int = 0
    failure_info: str = ""  # failureInfo.error from zeroshot status


@dataclass
class AnalyzeInput:
    """Input to the analyze_and_capture activity."""

    task: str
    project_dir: str
    outcome: ClusterOutcome
    memory_vault: str = ""  # memorix vault for shared lessons (fallback: lessons.jsonl)


@dataclass
class Analysis:
    """Result of analyze_and_capture."""

    status: str  # success / task_failure / infra_error / stalled / unverified_success
    detail: str = ""
    lesson_id: str = ""
    verified: bool = False


@dataclass
class LoopResult:
    """Final result of LoopWorkflow."""

    success: bool
    attempts: int
    reason: str = ""  # success / task_failure / infra_error / stalled / max_iterations
    detail: str = ""
    last_cluster_id: str = ""
    verified: bool = False
