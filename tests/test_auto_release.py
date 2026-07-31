"""Deterministic static tests for the Auto Release workflow.

These tests treat the workflow YAML as the artifact under test. They verify that
a successful CI run on ``main`` always selects a release job, that the job
checks out the exact triggering SHA, tags before building, and only updates
CHANGELOG.md afterwards from a fresh default-branch checkout.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml


def _workflow_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".github" / "workflows" / "auto-release.yml"


@pytest.fixture
def workflow() -> dict[str, Any]:
    with _workflow_path().open() as f:
        data = yaml.safe_load(f)
    # YAML 1.1 parses the literal key ``on`` as the boolean ``True``.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _job_steps(workflow: dict[str, Any], job_name: str) -> list[dict[str, Any]]:
    return workflow["jobs"][job_name]["steps"]


def _step_named(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in {steps!r}")


def _resolve(path: str, context: dict[str, Any]) -> Any:
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _eval_github_expression(expr: str, context: dict[str, Any]) -> bool:
    """Evaluate a narrow GitHub Actions expression used for the release job ``if``."""
    expr = expr.replace("\n", " ").replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"\s+(and|or)\s*$", "", expr)
    expr = re.sub(r"\b(github(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b", r"_resolve('\1', ctx)", expr)
    return bool(
        eval(expr, {"__builtins__": {}}, {"_resolve": _resolve, "ctx": context})  # noqa: S307
    )


def test_trigger_requires_successful_ci_on_main(workflow: dict[str, Any]) -> None:
    trigger = workflow["on"]["workflow_run"]
    assert trigger["workflows"] == ["CI"], trigger["workflows"]
    assert trigger["types"] == ["completed"], trigger["types"]
    assert "main" in trigger["branches"], trigger["branches"]


def test_release_job_selects_only_successful_main_push(workflow: dict[str, Any]) -> None:
    expr = workflow["jobs"]["release"]["if"]
    assert "github.event.workflow_run.conclusion == 'success'" in expr
    assert "github.event.workflow_run.head_branch == 'main'" in expr
    assert "github.event.workflow_run.event == 'push'" in expr


@pytest.mark.parametrize(
    ("conclusion", "head_branch", "event", "expected"),
    [
        ("success", "main", "push", True),
        ("failure", "main", "push", False),
        ("success", "main", "pull_request", False),
        ("success", "other", "push", False),
    ],
)
def test_release_job_selectability(
    workflow: dict[str, Any],
    conclusion: str,
    head_branch: str,
    event: str,
    expected: bool,
) -> None:
    expr = workflow["jobs"]["release"]["if"]
    context = {
        "github": {
            "event": {
                "workflow_run": {
                    "conclusion": conclusion,
                    "head_branch": head_branch,
                    "event": event,
                }
            }
        }
    }
    assert _eval_github_expression(expr, context) is expected


def test_release_job_checks_out_exact_triggering_sha(workflow: dict[str, Any]) -> None:
    steps = _job_steps(workflow, "release")
    checkout = _step_named(steps, "Checkout")
    assert checkout["uses"].startswith("actions/checkout")
    assert checkout["with"]["ref"] == "${{ github.event.workflow_run.head_sha }}"


def test_release_tag_created_before_build_and_changelog_pushback_is_isolated(
    workflow: dict[str, Any],
) -> None:
    steps = _job_steps(workflow, "release")
    names = [s.get("name") for s in steps]
    tag_idx = names.index("Create release tag")
    build_idx = names.index("Build package")
    release_idx = names.index("Create Release")
    changelog_idx = names.index("Update CHANGELOG")
    assert tag_idx < build_idx, f"tag must precede build: {names}"
    assert release_idx < changelog_idx == len(steps) - 1, (
        f"CHANGELOG update must be the final post-release step: {names}"
    )
    tag_step = steps[tag_idx]
    assert "git tag" in tag_step["run"]

    changelog_step = steps[changelog_idx]
    assert changelog_step["continue-on-error"] is True
    assert "git switch --create changelog-update --track origin/main" in changelog_step["run"]
    assert "git commit -m" in changelog_step["run"]
    assert "git push origin HEAD:main" in changelog_step["run"]

    # No detached-HEAD pushback race: only the final, isolated changelog step
    # may commit or push HEAD back to the default branch.
    for step in steps:
        if step is changelog_step:
            continue
        run = step.get("run", "")
        assert "git push origin HEAD" not in run, (
            f"step {step.get('name')!r} pushes HEAD, creating a CHANGELOG pushback race"
        )
        assert "git commit" not in run, (
            f"step {step.get('name')!r} commits to the checkout, altering the release SHA"
        )


def test_release_publishes_exact_tag_assets(workflow: dict[str, Any]) -> None:
    steps = _job_steps(workflow, "release")
    release = _step_named(steps, "Create Release")
    assert release["uses"].startswith("softprops/action-gh-release")
    assert release["with"]["tag_name"] == "${{ steps.bump.outputs.new_tag }}"
    files = release["with"]["files"]
    # `files` is a newline-delimited STRING input, not a YAML sequence.
    assert isinstance(files, str), f"files must be a newline-delimited string, got {type(files)}"
    globs = [line.strip() for line in files.splitlines() if line.strip()]
    assert any(g.endswith("*.whl") for g in globs), globs
    assert any(g.endswith("*.tar.gz") for g in globs), globs


def test_all_step_with_values_are_scalars(workflow: dict[str, Any]) -> None:
    """GitHub Actions rejects non-scalar ``with:`` values, invalidating the workflow.

    The workflow schema types ``jobs.<id>.steps[*].with`` values as
    string | number | boolean. A YAML sequence or mapping makes GitHub reject the
    entire file, which surfaces as a release run with zero executable jobs (AC1).
    """
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            for key, value in (step.get("with") or {}).items():
                assert isinstance(value, str | int | float | bool), (
                    f"{job_name}/{step.get('name')}: with.{key} must be a scalar, "
                    f"got {type(value).__name__} ({value!r})"
                )
