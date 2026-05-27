"""Read-only diagnostics MCP tool for the lgrep server.

Exposes ``lgrep_diagnostics`` — a non-destructive snapshot of daemon state
including process info, loaded projects, and runtime job lifecycle.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Annotated

from mcp.server.fastmcp import Context  # noqa: TC002
from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.server import mcp, time_tool
from lgrep.server.responses import (  # noqa: TC001
    DiagnosticsResult,
    LoadedProjectEntry,
    TimeoutAbandonmentSummary,
)
from lgrep.server.runtime import JobStatus

if TYPE_CHECKING:
    from lgrep.server.lifecycle import LgrepContext


@mcp.tool(
    description=(
        "Return a read-only diagnostic snapshot of the lgrep daemon. "
        "Includes PID, uptime, loaded projects, active/recent jobs, and "
        "timeout/abandonment summary. Contains no secrets or env vars."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@time_tool
async def lgrep_diagnostics(
    ctx: Annotated[
        Context | None,
        Field(description="MCP request context (optional for direct calls)"),
    ] = None,
) -> DiagnosticsResult:
    """Return read-only daemon diagnostics.

    Uses existing RuntimeSupervisor snapshots; does not perform expensive
    disk work. Never includes secrets, environment variables, or raw
    tracebacks.
    """
    app_ctx: LgrepContext | None = None
    if ctx is not None:
        app_ctx = ctx.request_context.lifespan_context

    # Process info
    pid = os.getpid()
    uptime_seconds = 0.0
    transport = None
    worker_max_threads = 0
    active_jobs: list[dict] = []
    recent_jobs: list[dict] = []

    if app_ctx is not None:
        transport = app_ctx.transport
        supervisor = app_ctx.runtime
        uptime_seconds = round(time.time() - supervisor.started_at, 2)
        worker_max_threads = supervisor.max_workers
        active_jobs = supervisor.snapshot_active_jobs()
        recent_jobs = supervisor.snapshot_recent_jobs()

    # Loaded projects
    loaded_projects: list[LoadedProjectEntry] = []
    if app_ctx is not None:
        for path, state in app_ctx.projects.items():
            loaded_projects.append(
                LoadedProjectEntry(
                    path=path,
                    watching=state.watching,
                )
            )

    # Timeout/abandonment summary
    abandoned_count = 0
    finished_after_abandon_count = 0
    failed_after_abandon_count = 0
    for job in active_jobs + recent_jobs:
        status = job.get("status", "")
        if status == JobStatus.ABANDONED.value:
            abandoned_count += 1
        elif status == JobStatus.FINISHED_AFTER_ABANDON.value:
            finished_after_abandon_count += 1
        elif status == JobStatus.FAILED_AFTER_ABANDON.value:
            failed_after_abandon_count += 1

    return DiagnosticsResult(
        pid=pid,
        uptime_seconds=uptime_seconds,
        transport=transport,
        worker_max_threads=worker_max_threads,
        active_job_count=len(active_jobs),
        recent_job_count=len(recent_jobs),
        loaded_project_count=len(loaded_projects),
        loaded_projects=loaded_projects,
        active_jobs=active_jobs,
        recent_jobs=recent_jobs,
        timeout_abandonment_summary=TimeoutAbandonmentSummary(
            abandoned_count=abandoned_count,
            finished_after_abandon_count=finished_after_abandon_count,
            failed_after_abandon_count=failed_after_abandon_count,
        ),
    )
