"""pr-land tests.

These drive the REAL script via subprocess with a stub `gh` on PATH that plays back a
sequence of PR states from a JSON file: each `gh pr view` with the full field list
returns the next state, and the last one repeats. Every stub call appends its argv as
one JSON line to a log. Real `git` (for the state directory) and `jq` are required.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "herdr-issue"
SCRIPT = PLUGIN_ROOT / "scripts" / "pr-land"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("git") is None,
    reason="pr-land needs jq and git",
)

STUB = r"""#!{python}
import json, os, sys

args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(["gh", *args]) + "\n")

if args[:2] == ["pr", "view"] and args[-1] == "number":
    print(json.dumps({"number": 42}))
elif args[:2] == ["pr", "view"]:
    fails = int(os.environ.get("STUB_VIEW_FAILS", "0"))
    counter = os.environ["STUB_COUNTER"]
    i = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(i + 1))
    if i < fails:
        sys.exit("HTTP 502: Bad Gateway")
    states = json.load(open(os.environ["STUB_STATES"]))
    print(json.dumps(states[min(i - fails, len(states) - 1)]))
elif args[:2] == ["pr", "update-branch"]:
    if os.environ.get("STUB_UPDATE_FAILS"):
        sys.exit("update failed")
    print("PR branch updated")
elif args[:2] == ["pr", "merge"]:
    if os.environ.get("STUB_MERGE_FAILS"):
        sys.exit("Pull request is not mergeable")
elif args[:2] == ["run", "list"]:
    print(json.dumps([
        {"databaseId": 7, "status": "in_progress"},
        {"databaseId": 8, "status": "completed"},
    ]))
elif args[:2] != ["run", "cancel"]:
    sys.exit(f"stub gh: unexpected {args}")
"""

OLD = "a" * 40
NEW = "b" * 40


def check(name: str, status: str = "COMPLETED", conclusion: str = "SUCCESS") -> dict:
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion if status == "COMPLETED" else "",
    }


def pr(merge_state: str = "CLEAN", head: str = OLD, checks=None, **extra) -> dict:
    return {
        "state": "OPEN",
        "isDraft": False,
        "mergeStateStatus": merge_state,
        "headRefOid": head,
        "statusCheckRollup": [check("CI Gate")] if checks is None else checks,
        **extra,
    }


MERGED = pr(state="MERGED")
PENDING = [check("CI Gate", status="IN_PROGRESS")]


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        stub = self.bin / "gh"
        stub.write_text(STUB.replace("{python}", sys.executable))
        stub.chmod(0o755)

        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

        self.log = tmp_path / "calls.jsonl"
        self.states = tmp_path / "states.json"
        self.counter = tmp_path / "counter"
        self.state_dir = self.repo / ".git" / "herdr-issue"

    def env(self, **extra: str) -> dict:
        return {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "PR_LAND_INTERVAL": "0",
            "STUB_LOG": str(self.log),
            "STUB_STATES": str(self.states),
            "STUB_COUNTER": str(self.counter),
            **extra,
        }

    def set_states(self, states: list) -> None:
        self.states.write_text(json.dumps(states))
        self.counter.unlink(missing_ok=True)

    def run(self, *args: str, states: list, **env: str):
        self.set_states(states)
        proc = subprocess.run(
            [str(SCRIPT), *args],
            cwd=self.repo,
            env=self.env(**env),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc, self.calls()

    def calls(self) -> list:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def merges(calls: list) -> list:
    return [c[1:] for c in calls if c[1:3] == ["pr", "merge"]]


def updates(calls: list) -> list:
    return [c for c in calls if c[1:3] == ["pr", "update-branch"]]


def test_green_clean_pr_merges_with_the_given_flags_on_its_head(harness):
    proc, calls = harness.run(
        "42", "--", "--squash", "--body-file", "body.md", states=[pr(), MERGED]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert merges(calls) == [
        ["pr", "merge", "42", "--squash", "--body-file", "body.md", "--match-head-commit", OLD]
    ]
    assert proc.stdout.splitlines()[-1].endswith("#42 merged aaaaaaa")


def test_behind_updates_at_once_even_with_checks_running(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[
            pr("BEHIND", checks=PENDING),
            pr("BLOCKED", head=NEW, checks=PENDING),
            pr("CLEAN", head=NEW),
            MERGED,
        ],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(updates(calls)) == 1
    # The stale run on the old head is cancelled; the finished one is left alone.
    assert ["gh", "run", "list", "--commit", OLD, "--json", "databaseId,status"] in calls
    assert [c for c in calls if c[1:3] == ["run", "cancel"]] == [["gh", "run", "cancel", "7"]]
    assert merges(calls)[0][-1] == NEW
    assert "behind the base branch: updated aaaaaaa" in proc.stdout


def test_still_behind_on_the_same_head_is_not_updated_twice(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--merge",
        states=[pr("BEHIND"), pr("BEHIND"), pr("CLEAN", head=NEW), MERGED],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(updates(calls)) == 1


def test_failing_checks_stop_without_updating_or_merging(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr("BEHIND", checks=[check("lint"), check("tests", conclusion="FAILURE")])],
    )
    assert proc.returncode == 1
    assert "stop: checks failed on aaaaaaa: tests" in proc.stdout
    assert updates(calls) == [] and merges(calls) == []


def test_a_failing_commit_status_counts_as_failing(harness):
    status = {"__typename": "StatusContext", "context": "ci/legacy", "state": "ERROR"}
    proc, _ = harness.run("42", "--", "--squash", states=[pr(checks=[status])])
    assert proc.returncode == 1
    assert "checks failed on aaaaaaa: ci/legacy" in proc.stdout


def test_pending_checks_wait_and_say_so_once(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr("UNSTABLE", checks=PENDING)] * 3 + [pr(), MERGED],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("waiting for checks on aaaaaaa") == 1
    assert len(merges(calls)) == 1


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (pr("DIRTY"), "conflicts with the base branch"),
        (pr(isDraft=True), "the PR is a draft"),
        (pr(state="CLOSED"), "closed without merging"),
        (pr("BLOCKED"), "GitHub still blocks the merge"),
        (pr(checks=[]), "no checks reported on aaaaaaa"),
    ],
)
def test_states_the_session_must_handle_stop_with_the_reason(harness, state, reason):
    proc, calls = harness.run("42", "--", "--squash", states=[state])
    assert proc.returncode == 1
    assert reason in proc.stdout.splitlines()[-1]
    assert merges(calls) == []


def test_merge_accepted_but_not_merged_stops(harness):
    proc, _ = harness.run("42", "--", "--squash", states=[pr(), pr()])
    assert proc.returncode == 1
    assert "gh accepted the merge but the PR is still open" in proc.stdout


def test_a_refused_merge_is_retried_then_stops(harness):
    proc, calls = harness.run("42", "--", "--squash", states=[pr()], STUB_MERGE_FAILS="1")
    assert proc.returncode == 1
    assert len(merges(calls)) == 3
    assert "stop: could not merge aaaaaaa: Pull request is not mergeable" in proc.stdout


def test_transient_gh_failures_are_retried(harness):
    proc, _ = harness.run("42", "--", "--squash", states=[pr(), MERGED], STUB_VIEW_FAILS="2")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_current_branch_pr_is_used_without_a_number(harness):
    proc, calls = harness.run("--", "--squash", states=[pr(), MERGED])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert merges(calls)[0][2] == "42"


@pytest.mark.parametrize(
    "args",
    [
        ("42",),
        ("42", "--"),
        ("42", "--", "--body", "x"),
        ("42", "--", "--squash", "--admin"),
        ("42", "--", "--squash", "--auto"),
        ("42", "--", "--squash", "--delete-branch"),
    ],
)
def test_bad_merge_flags_are_refused(harness, args):
    proc, calls = harness.run(*args, states=[pr()])
    assert proc.returncode == 2
    assert calls == []


def test_the_lander_outlives_its_follower_and_can_be_reattached(harness):
    harness.set_states([pr(checks=PENDING)])
    env = harness.env(PR_LAND_INTERVAL="0.2")
    first = subprocess.Popen(
        [str(SCRIPT), "42", "--", "--squash"],
        cwd=harness.repo,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert "waiting for checks" in "".join(first.stdout.readline() for _ in range(2))
    # Kill the follower's whole process group, as an expiring Monitor would.
    os.killpg(first.pid, signal.SIGKILL)
    first.wait()

    pid = int((harness.state_dir / "pr-land-42.pid").read_text())
    try:
        os.kill(pid, 0)
        second = subprocess.Popen(
            [str(SCRIPT), "42", "--", "--merge"],
            cwd=harness.repo,
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert "following the lander already running for #42" in second.stdout.readline()
        assert "waiting for checks on aaaaaaa" in second.stdout.readline()

        harness.set_states([pr(), MERGED])
        out, _ = second.communicate(timeout=30)
        assert second.returncode == 0, out
        assert out.splitlines()[-1].endswith("merged aaaaaaa")
        # The running lander kept its own flags.
        assert merges(harness.calls())[0][3] == "--squash"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
