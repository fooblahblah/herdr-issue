"""show-pr tests.

These drive the REAL script with stub `herdr`, `git` and `gh` on PATH. herdr and
gh log each call; git names the branch in STUB_BRANCH, and `gh pr view` answers
with STUB_PR, or fails as gh does when the branch has no PR. Real `jq` is
required for the hook mode.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "herdr-issue" / "scripts" / "show-pr"
HOOKS = Path(__file__).resolve().parents[1] / "plugins" / "herdr-issue" / "hooks" / "hooks.json"

STUB = r"""#!{python}
import json, os, sys

tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if tool == "git":
    if args[-2:] != ["branch", "--show-current"]:
        sys.exit(f"stub git: unexpected {args}")
    print(os.environ["STUB_BRANCH"])
    sys.exit(0)
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\n")
if tool == "gh":
    if args[:2] != ["pr", "view"]:
        sys.exit(f"stub gh: unexpected {args}")
    if not os.environ["STUB_PR"]:
        sys.exit('no pull requests found for branch "x"')
    print(os.environ["STUB_PR"])
"""


def run(
    tmp_path: Path,
    *args: str,
    pane: str | None = "w1:p1",
    branch: str = "issue/3284-tests-per-issue",
    pr: str = "",
    stdin: str = "",
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "stub"
    stub.write_text(STUB.replace("{python}", sys.executable))
    stub.chmod(0o755)
    for tool in ("git", "gh", "herdr"):
        if not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(stub)
    log = tmp_path / "calls.jsonl"
    log.unlink(missing_ok=True)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "STUB_BRANCH": branch,
        "STUB_PR": pr,
    }
    env.pop("HERDR_PANE_ID", None)
    if pane is not None:
        env["HERDR_PANE_ID"] = pane

    proc = subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return proc, calls


def reports(calls: list) -> list:
    return [c[1:] for c in calls if c[:3] == ["herdr", "pane", "report-metadata"]]


def labels(calls: list) -> list[str]:
    return [r[-1] for r in reports(calls)]


# Manual use: show-pr <pr>


@pytest.mark.parametrize("pr", ["3301", "#3301"])
def test_shows_the_issue_and_the_pr(tmp_path, pr):
    proc, calls = run(tmp_path, pr)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert reports(calls) == [[
        "pane", "report-metadata", "w1:p1",
        "--source", "herdr-issue",
        "--display-agent", "Issue: #3284 · PR #3301",
    ]]


def test_several_issues_are_all_shown(tmp_path):
    # The branch keeps every number, even those the agent name drops to fit Herdr.
    proc, calls = run(tmp_path, "3301", branch="issue/3284_3285_3290-tests-per-issue")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert labels(calls) == ["Issues: #3284, #3285, #3290 · PR #3301"]


@pytest.mark.parametrize("branch", ["main", "issue/next-steps", ""])
def test_a_branch_without_issue_numbers_shows_only_the_pr(tmp_path, branch):
    proc, calls = run(tmp_path, "12", branch=branch)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert labels(calls) == ["PR #12"]


def test_without_a_number_it_finds_the_branch_pr(tmp_path):
    proc, calls = run(tmp_path, pr="3301")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert labels(calls) == ["Issue: #3284 · PR #3301"]


def test_without_a_number_or_a_pr_it_fails(tmp_path):
    proc, calls = run(tmp_path)
    assert proc.returncode != 0
    assert reports(calls) == []


@pytest.mark.parametrize("args", [("abc",), ("12", "13"), ("",)])
def test_anything_but_one_pr_number_is_refused(tmp_path, args):
    proc, calls = run(tmp_path, *args)
    assert proc.returncode == 2
    assert reports(calls) == []


def test_outside_a_herdr_pane_it_fails_and_reports_nothing(tmp_path):
    proc, calls = run(tmp_path, "12", pane=None)
    assert proc.returncode != 0
    assert "HERDR_PANE_ID" in proc.stderr
    assert reports(calls) == []


# Hook use: show-pr --hook, with PostToolUse input on stdin


def hook_input(command: str, cwd: str) -> str:
    return json.dumps({
        "hook_event_name": "PostToolUse",
        "cwd": cwd,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": "", "stderr": ""},
    })


needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="the hook needs jq")


@needs_jq
@pytest.mark.parametrize("command", [
    "gh pr create --draft --assignee @me --title x --body-file /tmp/b",
    "cd /work/tree && gh pr ready 3301",
    "gh  pr view --json state",
    "GH_PAGER= gh pr checks",
])
def test_a_gh_pr_command_shows_the_branch_pr(tmp_path, command):
    proc, calls = run(tmp_path, "--hook", pr="3301", stdin=hook_input(command, str(tmp_path)))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert labels(calls) == ["Issue: #3284 · PR #3301"]


@needs_jq
@pytest.mark.parametrize("command", [
    "git push -u origin HEAD",
    "gh issue view 3284",
    "ghx pr create",
    "my-gh pr create",
])
def test_other_commands_do_nothing(tmp_path, command):
    proc, calls = run(tmp_path, "--hook", pr="3301", stdin=hook_input(command, str(tmp_path)))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert calls == []


@needs_jq
@pytest.mark.parametrize("branch", ["main", "feature/x", ""])
def test_the_hook_leaves_sessions_off_issue_branches_alone(tmp_path, branch):
    proc, calls = run(
        tmp_path, "--hook", branch=branch, pr="3301", stdin=hook_input("gh pr create", str(tmp_path))
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert calls == []


@needs_jq
def test_the_hook_does_nothing_outside_herdr(tmp_path):
    proc, calls = run(
        tmp_path, "--hook", pane=None, pr="3301", stdin=hook_input("gh pr create", str(tmp_path))
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert calls == []


@needs_jq
def test_the_hook_is_silent_when_the_branch_has_no_pr(tmp_path):
    proc, calls = run(tmp_path, "--hook", stdin=hook_input("gh pr create --draft", str(tmp_path)))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert reports(calls) == []


@needs_jq
@pytest.mark.parametrize("stdin", ["", "not json", "{}"])
def test_the_hook_never_fails_on_odd_input(tmp_path, stdin):
    proc, calls = run(tmp_path, "--hook", pr="3301", stdin=stdin)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert calls == []


def test_the_plugin_runs_the_hook_after_bash_in_the_background():
    hooks = json.loads(HOOKS.read_text())["hooks"]["PostToolUse"]
    assert hooks == [{
        "matcher": "Bash",
        "hooks": [{
            "type": "command",
            "command": '"${CLAUDE_PLUGIN_ROOT}/scripts/show-pr" --hook',
            "async": True,
        }],
    }]
