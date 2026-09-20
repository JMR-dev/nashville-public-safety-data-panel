"""The checks workflow runs every gate, the way a developer runs it locally.

A gate that only exists on a laptop stops being a gate, so these tests read the workflow and
fail when it drifts from the commands this project is actually checked with.
"""

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "checks.yml"

GATES = {
    "Python lint": "uv run ruff check backend",
    "Python formatting": "uv run ruff format --check backend",
    "Python types": "uv run pyright",
    "Python tests, including their coverage gate": "uv run pytest",
    "TypeScript and configuration lint": "pnpm exec eslint .",
    "Frontend types": "pnpm --filter frontend typecheck",
    "Playwright configuration and flow types": "pnpm exec tsc --noEmit -p tsconfig.json",
    "Frontend tests, including their coverage gate": "pnpm --filter frontend test",
    "HTTP collections": "pnpm run test:api",
    "Browser flows": "pnpm run test:e2e",
    "Container images and Quadlet units": "uv run pytest -m containers --no-cov",
}


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    return cast(dict[Any, Any], yaml.safe_load(WORKFLOW.read_text()))


def jobs(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], job) for job in workflow["jobs"].values()]


def steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], step) for job in jobs(workflow) for step in job["steps"]]


def uses(step: dict[str, Any]) -> str:
    return str(step.get("uses", ""))


def commands(workflow: dict[Any, Any]) -> list[str]:
    return [str(step["run"]) for step in steps(workflow) if "run" in step]


@pytest.mark.parametrize("command", GATES.values(), ids=list(GATES))
def test_every_gate_runs_in_the_workflow(workflow: dict[Any, Any], command: str) -> None:
    assert command in commands(workflow)


def test_the_checks_run_on_every_push_and_on_pull_requests(workflow: dict[Any, Any]) -> None:
    # YAML reads an unquoted "on" key as the boolean it also spells, which is how GitHub's
    # trigger block arrives here.
    assert set(workflow[True]) == {"push", "pull_request"}
    assert workflow[True]["push"]["branches"] == ["**"]


def test_dependencies_come_from_the_committed_lockfiles(workflow: dict[Any, Any]) -> None:
    """An unfrozen install would test versions nobody committed."""
    for command in commands(workflow):
        if command.startswith("uv sync"):
            assert "--frozen" in command
        if command.startswith("pnpm install"):
            assert "--frozen-lockfile" in command
    assert "uv sync --frozen" in commands(workflow)
    assert "pnpm install --frozen-lockfile" in commands(workflow)


def test_node_comes_from_the_pinned_version(workflow: dict[Any, Any]) -> None:
    setups = [step for step in steps(workflow) if uses(step).startswith("actions/setup-node")]
    assert setups
    for setup in setups:
        assert setup["with"]["node-version-file"] == ".node-version"
    assert (ROOT / ".node-version").read_text().strip().startswith("26.")


def test_python_comes_from_the_pinned_version() -> None:
    """uv installs the interpreter named in .python-version when it syncs."""
    assert (ROOT / ".python-version").read_text().strip().startswith("3.14")


def test_every_action_is_pinned_to_a_commit_with_its_version_beside_it(
    workflow: dict[Any, Any],
) -> None:
    """A tag can be moved to point at different code; a commit cannot."""
    used = [uses(step) for step in steps(workflow) if uses(step)]
    assert used
    for reference in used:
        action, _, pin = reference.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", pin), f"{action} is pinned to {pin!r}, not a commit"
    source = WORKFLOW.read_text()
    for reference in used:
        assert re.search(rf"uses: {re.escape(reference)} # v\d+\.\d+", source), (
            f"{reference} has no version comment"
        )


def test_a_failed_browser_run_keeps_its_report(workflow: dict[Any, Any]) -> None:
    uploads = [step for step in steps(workflow) if "upload-artifact" in uses(step)]
    assert [step["if"] for step in uploads] == ["failure()"]
    assert [step["with"]["path"] for step in uploads] == ["playwright-report/"]
