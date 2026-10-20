"""pr-land tests.

These drive the REAL script via subprocess with a stub `gh` on PATH that plays back a
sequence of PR states from a JSON file: each `gh pr view` with the full field list
returns the next state, and the last one repeats. Every stub call appends its argv as
one JSON line to a log. Real `git` (for the state directory), `jq` and `ps` are
required.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
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
env = os.environ
with open(env["STUB_LOG"], "a") as log:
    log.write(json.dumps(["gh", *args]) + "\n")

def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)

if args[:2] == ["pr", "view"] and args[-1] == "number,headRefName":
    print(json.dumps({"number": 42, "headRefName": "issue/42-notional"}))
elif args[:2] == ["pr", "view"]:
    fails = int(env.get("STUB_VIEW_FAILS", "0"))
    counter = env["STUB_COUNTER"]
    try:
        i = int(open(counter).read())
    except (FileNotFoundError, ValueError):
        i = 0
    open(counter, "w").write(str(i + 1))
    if i < fails:
        fail("HTTP 502: Bad Gateway")
    states = json.load(open(env["STUB_STATES"]))
    print(json.dumps(states[min(i - fails, len(states) - 1)]))
elif args[:2] == ["pr", "update-branch"]:
    if env.get("STUB_UPDATE_FAILS"):
        fail("GraphQL: update failed\nsecond line")
    print(env.get("STUB_UPDATE_OUTPUT", "PR branch updated"))
elif args[:2] == ["pr", "merge"]:
    if env.get("STUB_MERGE_FAILS"):
        fail(
            "X Pull request #42 is not mergeable: the base branch policy prohibits the merge.\n"
            "To have the pull request merged after all the requirements have been met, add the `--auto` flag.\n"
            "To use administrator privileges to immediately merge the pull request, add the `--admin` flag."
        )
elif args[:2] == ["run", "list"]:
    print(json.dumps([
        {"databaseId": 7, "status": "in_progress"},
        {"databaseId": 8, "status": "completed"},
    ]))
elif args[:2] == ["run", "cancel"]:
    pass
elif args[:1] == ["api"]:
    path = args[1]
    if path.endswith("/protection/required_status_checks"):
        if not env.get("STUB_STRICT"):
            fail("HTTP 404: Branch not protected")
        print(json.dumps({"strict": True}))
    elif "/rules/branches/" in path:
        print("[]")
    elif "/compare/" in path:
        # A test can change replies under a running lander through the file.
        compares = json.loads(env.get("STUB_COMPARE", "{}"))
        if os.path.exists(env["STUB_COMPARE_FILE"]):
            compares.update(json.load(open(env["STUB_COMPARE_FILE"])))
        reply = compares.get(path.split("/compare/", 1)[1], {"status": "identical", "behind_by": 0})
        if reply is None:
            fail("HTTP 404: Not Found")
        print(json.dumps(reply))
    else:
        fail(f"stub gh: unexpected {args}")
else:
    fail(f"stub gh: unexpected {args}")
"""

OLD = "a" * 40
NEW = "b" * 40


def check(
    name: str,
    status: str = "COMPLETED",
    conclusion: str = "SUCCESS",
    started: str = "2026-10-20T10:00:00Z",
) -> dict:
    return {
        "__typename": "CheckRun",
        "name": name,
        "workflowName": "CI",
        "status": status,
        "conclusion": conclusion if status == "COMPLETED" else "",
        "startedAt": started,
    }


def pr(merge_state: str = "CLEAN", head: str = OLD, checks=None, **extra) -> dict:
    return {
        "state": "OPEN",
        "isDraft": False,
        "mergeStateStatus": merge_state,
        "headRefOid": head,
        "headRefName": "issue/42-notional",
        "baseRefName": "main",
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
        self.compare_file = tmp_path / "compare.json"
        self.state_dir = self.repo / ".git" / "herdr-issue"

    def env(self, **extra: str) -> dict:
        return {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "PR_LAND_INTERVAL": "0",
            "PR_LAND_SETTLE": "0",
            "STUB_LOG": str(self.log),
            "STUB_STATES": str(self.states),
            "STUB_COUNTER": str(self.counter),
            "STUB_COMPARE_FILE": str(self.compare_file),
            **extra,
        }

    def set_states(self, states: list) -> None:
        # Reset the counter first and swap the states in whole, so a running
        # lander never reads the new states at the old position.
        self.counter.unlink(missing_ok=True)
        tmp = self.states.with_suffix(".tmp")
        tmp.write_text(json.dumps(states))
        tmp.replace(self.states)

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

    def start(self, *args: str, **env: str) -> subprocess.Popen:
        return subprocess.Popen(
            [str(SCRIPT), *args],
            cwd=self.repo,
            env=self.env(**env),
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    def calls(self) -> list:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def lander_pid(self) -> int:
        return int((self.state_dir / "pr-land-42.pid").read_text())

    def kill_lander(self) -> None:
        try:
            os.kill(self.lander_pid(), signal.SIGKILL)
        except (FileNotFoundError, ValueError, ProcessLookupError):
            pass


@pytest.fixture
def harness(tmp_path: Path):
    h = Harness(tmp_path)
    yield h
    # A regression that leaves a lander running must not leave it spinning.
    h.kill_lander()


def merges(calls: list) -> list:
    return [c[1:] for c in calls if c[1:3] == ["pr", "merge"]]


def updates(calls: list) -> list:
    return [c for c in calls if c[1:3] == ["pr", "update-branch"]]


def cancels(calls: list) -> list:
    return [c for c in calls if c[1:3] == ["run", "cancel"]]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed lander stays a zombie until its parent, init, reaps it.
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return status.stdout.strip()[:1] not in ("", "Z")


def wait_for(predicate, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_green_clean_pr_merges_with_the_given_flags_on_its_head(harness):
    proc, calls = harness.run(
        "42", "--", "--squash", "--body-file", "body.md", states=[pr(), MERGED]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert merges(calls) == [
        ["pr", "merge", "42", "--squash", "--body-file", "body.md", "--match-head-commit", OLD]
    ]
    assert proc.stdout.splitlines()[-1].endswith("#42 merged aaaaaaa")


def test_behind_updates_at_once_and_cancels_the_old_heads_runs_once_it_moves(harness):
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
    # Only the PR branch's unfinished run on the old head is cancelled.
    assert [
        "gh", "run", "list", "--commit", OLD, "--branch", "issue/42-notional",
        "--json", "databaseId,status",
    ] in calls
    assert cancels(calls) == [["gh", "run", "cancel", "7"]]
    assert merges(calls)[0][-1] == NEW
    assert "behind the base branch: updated aaaaaaa" in proc.stdout


def test_behind_is_found_while_checks_run_when_the_base_requires_it(harness):
    # GitHub may report BLOCKED rather than BEHIND while a required check runs.
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[
            pr("BLOCKED", checks=PENDING),
            pr("BLOCKED", head=NEW, checks=PENDING),
            pr("CLEAN", head=NEW),
            MERGED,
        ],
        STUB_STRICT="1",
        STUB_COMPARE=json.dumps({f"main...{OLD}": {"status": "diverged", "behind_by": 2}}),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(updates(calls)) == 1
    assert merges(calls)[0][-1] == NEW


def test_a_behind_branch_is_left_alone_when_the_base_does_not_require_it(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr("UNSTABLE", checks=PENDING), pr(), MERGED],
        STUB_COMPARE=json.dumps({f"main...{OLD}": {"status": "diverged", "behind_by": 2}}),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert updates(calls) == []
    assert len(merges(calls)) == 1


def test_still_behind_on_the_same_head_is_not_updated_twice(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--merge",
        states=[pr("BEHIND"), pr("BEHIND"), pr("CLEAN", head=NEW), MERGED],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(updates(calls)) == 1


def test_an_update_that_finds_nothing_to_do_cancels_nothing(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr("BEHIND", checks=PENDING)],
        STUB_UPDATE_OUTPUT="✓ PR branch already up-to-date",
    )
    assert proc.returncode == 1
    assert cancels(calls) == [] and merges(calls) == []
    assert "but it is up to date" in proc.stdout.splitlines()[-1]


def test_an_update_that_keeps_failing_stops(harness):
    proc, calls = harness.run(
        "42", "--", "--squash", states=[pr("BEHIND")], STUB_UPDATE_FAILS="1"
    )
    assert proc.returncode == 1
    assert len(updates(calls)) == 3
    assert proc.stdout.splitlines()[-1].endswith(
        "stop: could not update aaaaaaa from the base branch: GraphQL: update failed second line"
    )


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


@pytest.mark.parametrize(
    "earlier",
    [
        check("tests", conclusion="FAILURE", started="2026-10-20T09:00:00Z"),
        check("tests", conclusion="CANCELLED", started="2026-10-20T09:00:00Z"),
    ],
)
def test_only_the_latest_attempt_of_a_check_counts(harness, earlier):
    proc, calls = harness.run(
        "42", "--", "--squash", states=[pr(checks=[earlier, check("tests")]), MERGED]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(merges(calls)) == 1


def test_a_rerun_that_shows_up_a_poll_late_is_waited_for(harness):
    failed = check("tests", conclusion="FAILURE")
    queued = check("tests", status="QUEUED", started="0001-01-01T00:00:00Z")
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr(checks=[failed]), pr(checks=[failed, queued]), pr(), MERGED],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(merges(calls)) == 1


def test_a_queued_rerun_of_a_failed_check_is_waited_for(harness):
    failed = check("tests", conclusion="FAILURE")
    queued = check("tests", status="QUEUED", started="0001-01-01T00:00:00Z")
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr(checks=[failed, queued]), pr(checks=[failed, check("tests", started="2026-10-20T11:00:00Z")]), MERGED],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "waiting for checks on aaaaaaa" in proc.stdout


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


def test_checks_pending_too_long_stop(harness):
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr("BLOCKED", checks=PENDING)],
        PR_LAND_INTERVAL="0.3",
        PR_LAND_PENDING_LIMIT="1",
    )
    assert proc.returncode == 1
    assert "stop: checks still pending on aaaaaaa after 0 minutes: CI Gate" in proc.stdout
    assert merges(calls) == []


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (pr("DIRTY"), "conflicts with the base branch"),
        (pr(isDraft=True), "the PR is a draft"),
        (pr(state="CLOSED"), "closed without merging"),
        (pr("BLOCKED"), "GitHub still blocks the merge"),
        (pr(checks=[]), "no checks were reported on aaaaaaa"),
    ],
)
def test_states_the_session_must_handle_stop_with_the_reason(harness, state, reason):
    proc, calls = harness.run("42", "--", "--squash", states=[state])
    assert proc.returncode == 1
    assert reason in proc.stdout.splitlines()[-1]
    assert merges(calls) == []


def test_blocked_or_unknown_that_clears_still_merges(harness):
    proc, calls = harness.run(
        "42", "--", "--squash", states=[pr("UNKNOWN"), pr("BLOCKED"), pr(), MERGED]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(merges(calls)) == 1


def test_a_merge_github_is_slow_to_show_still_counts(harness):
    proc, calls = harness.run("42", "--", "--squash", states=[pr(), pr(), MERGED])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(merges(calls)) == 1


def test_merge_accepted_but_never_merged_stops(harness):
    proc, calls = harness.run("42", "--", "--squash", states=[pr()])
    assert proc.returncode == 1
    assert len(merges(calls)) == 1
    assert "gh accepted the merge but the PR is still open" in proc.stdout


def test_a_refused_merge_is_retried_then_stops_on_one_line(harness):
    proc, calls = harness.run("42", "--", "--squash", states=[pr()], STUB_MERGE_FAILS="1")
    assert proc.returncode == 1
    assert len(merges(calls)) == 3
    last = proc.stdout.splitlines()[-1]
    assert "stop: could not merge aaaaaaa: X Pull request #42 is not mergeable" in last
    assert "--admin" not in proc.stdout


def test_transient_gh_failures_are_retried(harness):
    proc, _ = harness.run("42", "--", "--squash", states=[pr(), MERGED], STUB_VIEW_FAILS="2")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_gh_failing_every_time_stops(harness):
    proc, _ = harness.run("42", "--", "--squash", states=[pr()], STUB_VIEW_FAILS="99")
    assert proc.returncode == 1
    assert proc.stdout.splitlines()[-1].endswith(
        "stop: gh pr view failed 5 times in a row: HTTP 502: Bad Gateway"
    )


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
        ("--stop", "42", "--", "--squash"),
    ],
)
def test_bad_arguments_are_refused(harness, args):
    proc, calls = harness.run(*args, states=[pr()])
    assert proc.returncode == 2
    assert calls == []


def commit(harness, branch: str = "issue/42-notional") -> str:
    git = ["git", "-C", str(harness.repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "checkout", "-q", "-B", branch], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    return subprocess.run(
        [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_the_lander_waits_for_github_to_see_the_local_head(harness):
    local = commit(harness)
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr(head=OLD), pr(head=local), MERGED],
        STUB_COMPARE=json.dumps({f"{local}...{OLD}": None}),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert merges(calls)[0][-1] == local
    assert f"waiting for GitHub to see {local[:7]}" in proc.stdout


def test_a_head_that_descends_from_the_local_head_counts_as_seen(harness):
    local = commit(harness)
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr(head=NEW), MERGED],
        STUB_COMPARE=json.dumps({f"{local}...{NEW}": {"status": "ahead", "behind_by": 0}}),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert merges(calls)[0][-1] == NEW


def test_a_checkout_on_another_branch_is_not_waited_for(harness):
    commit(harness, branch="main")
    proc, calls = harness.run("42", "--", "--squash", states=[pr(head=OLD), MERGED])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert merges(calls)[0][-1] == OLD


def test_an_unpushed_local_head_stops(harness):
    local = commit(harness)
    proc, calls = harness.run(
        "42",
        "--",
        "--squash",
        states=[pr(head=OLD)],
        STUB_COMPARE=json.dumps({f"{local}...{OLD}": None}),
    )
    assert proc.returncode == 1
    assert f"does not contain your HEAD {local[:7]}; push" in proc.stdout
    assert merges(calls) == []


def test_the_lander_outlives_its_follower_and_can_be_reattached(harness):
    harness.set_states([pr(checks=PENDING)])
    # An exported COLUMNS must not hide the running lander from ps.
    first = harness.start("42", "--", "--squash", PR_LAND_INTERVAL="0.2", COLUMNS="80")
    assert "waiting for checks" in "".join(first.stdout.readline() for _ in range(2))
    # Kill the follower's whole process group, as an expiring Monitor would.
    os.killpg(first.pid, signal.SIGKILL)
    first.wait()
    first.stdout.close()
    assert alive(harness.lander_pid())

    second = harness.start("42", "--", "--merge", PR_LAND_INTERVAL="0.2", COLUMNS="80")
    assert "following the lander already running for #42" in second.stdout.readline()
    assert "waiting for checks on aaaaaaa" in second.stdout.readline()

    harness.set_states([pr(), MERGED])
    out, _ = second.communicate(timeout=30)
    assert second.returncode == 0, out
    assert out.splitlines()[-1].endswith("merged aaaaaaa")
    # The running lander kept its own flags.
    assert merges(harness.calls())[0][3] == "--squash"


def test_reattaching_hands_the_lander_a_new_local_head(harness):
    first_head = commit(harness)
    harness.set_states([pr(head=first_head, checks=PENDING)])
    first = harness.start("42", "--", "--squash", PR_LAND_INTERVAL="0.2")
    assert "waiting for checks" in "".join(first.stdout.readline() for _ in range(2))
    os.killpg(first.pid, signal.SIGKILL)
    first.wait()
    first.stdout.close()

    second_head = commit(harness)
    harness.compare_file.write_text(json.dumps({f"{second_head}...{first_head}": None}))
    second = harness.start("42", "--", "--squash", PR_LAND_INTERVAL="0.2")
    assert "following the lander" in second.stdout.readline()
    # GitHub still shows the first head, green, for a few polls.
    harness.set_states([pr(head=first_head)] * 3 + [pr(head=second_head), MERGED])
    out, _ = second.communicate(timeout=30)
    assert second.returncode == 0, out
    assert f"waiting for GitHub to see {second_head[:7]}" in out
    assert [m[-1] for m in merges(harness.calls())] == [second_head]


def test_stop_ends_the_lander_and_its_follower(harness):
    harness.set_states([pr(checks=PENDING)])
    follower = harness.start("42", "--", "--squash", PR_LAND_INTERVAL="0.2")
    assert "waiting for checks" in "".join(follower.stdout.readline() for _ in range(2))
    pid = harness.lander_pid()

    proc, _ = harness.run("--stop", "42", states=[pr(checks=PENDING)])
    assert proc.returncode == 0, proc.stderr
    assert "stopped the lander for #42" in proc.stdout

    out, _ = follower.communicate(timeout=30)
    assert follower.returncode == 1
    assert out.splitlines()[-1].endswith("stop: stopped by pr-land --stop")
    wait_for(lambda: not alive(pid))
    assert merges(harness.calls()) == []


def test_stop_without_a_lander_says_so(harness):
    proc, _ = harness.run("--stop", "42", states=[pr()])
    assert proc.returncode == 0
    assert "no lander is running for #42" in proc.stdout


def test_a_pid_file_naming_another_process_is_ignored(harness):
    other = subprocess.Popen(["sleep", "30"])
    try:
        harness.state_dir.mkdir(parents=True)
        (harness.state_dir / "pr-land-42.pid").write_text(str(other.pid))
        proc, calls = harness.run("42", "--", "--squash", states=[pr(), MERGED])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "following the lander" not in proc.stdout
        assert len(merges(calls)) == 1
    finally:
        other.kill()
        other.wait()


def test_followers_starting_together_share_one_lander(harness):
    harness.set_states([pr(checks=PENDING)])
    followers = [
        harness.start("42", "--", "--squash", PR_LAND_INTERVAL="0.2") for _ in range(3)
    ]
    log = harness.state_dir / "pr-land-42.log"
    wait_for(lambda: log.exists() and "waiting for checks" in log.read_text())
    for f in followers:
        f.stdout.readline()

    harness.set_states([pr(), MERGED])
    for f in followers:
        out, _ = f.communicate(timeout=30)
        assert f.returncode == 0, out
    assert sum(1 for c in harness.calls() if c[1:3] == ["pr", "merge"]) == 1
    assert log.read_text().count("landing with") == 1


def test_a_lander_that_dies_is_reported(harness):
    harness.set_states([pr(checks=PENDING)])
    follower = harness.start("42", "--", "--squash", PR_LAND_INTERVAL="0.2")
    assert "waiting for checks" in "".join(follower.stdout.readline() for _ in range(2))
    os.kill(harness.lander_pid(), signal.SIGKILL)
    out, _ = follower.communicate(timeout=30)
    assert follower.returncode == 1
    assert "stopped without a result; run pr-land again" in out


def test_a_lander_nobody_follows_stops_itself(harness):
    harness.set_states([pr(checks=PENDING)])
    follower = harness.start(
        "42", "--", "--squash", PR_LAND_INTERVAL="0.2", PR_LAND_ORPHAN_LIMIT="2"
    )
    assert "waiting for checks" in "".join(follower.stdout.readline() for _ in range(2))
    pid = harness.lander_pid()
    os.killpg(follower.pid, signal.SIGKILL)
    follower.wait()
    follower.stdout.close()

    wait_for(lambda: not alive(pid))
    log = (harness.state_dir / "pr-land-42.log").read_text()
    assert "stop: nothing has followed this lander" in log.splitlines()[-1]
    assert (harness.state_dir / "pr-land-42.status").read_text().strip() == "1"
