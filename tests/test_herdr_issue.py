"""herdr-issue launcher tests.

These drive the REAL script via subprocess with stub `herdr`, `gh` and `git` on PATH,
so no Herdr server, GitHub call or git fetch happens. Every stub call appends its argv
as one JSON line to a log, so a test can assert exactly which calls the script made.
Real `jq` is required: the script parses every Herdr and gh reply with it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "herdr-issue"
SCRIPT = PLUGIN_ROOT / "scripts" / "herdr-issue"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="herdr-issue needs jq")

# One stub serves git, gh and herdr; it dispatches on the name it was invoked as.
STUB = r"""#!{python}
import json, os, sys

tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\n")

if tool == "git":
    if args == ["rev-parse", "--show-toplevel"]:
        print(os.environ["STUB_REPO"])
    elif args[-1] == "--git-common-dir":
        print(os.path.join(os.environ["STUB_REPO"], ".git"))
    elif args[-1] == "refs/remotes/origin/HEAD":
        head = os.environ.get("STUB_ORIGIN_HEAD")
        if not head:
            sys.exit(1)
        print(head)
    elif args[-3:-1] != ["fetch", "origin"]:
        sys.exit(f"stub git: unexpected {args}")
elif tool == "gh":
    issues = json.loads(os.environ["STUB_ISSUES"])
    if args[:2] == ["issue", "view"]:
        n = args[2]
        if n not in issues:
            sys.exit(f"GraphQL: Could not resolve to an issue with the number of {n}.")
        print(json.dumps({
            "number": int(n),
            "title": issues[n]["title"],
            "state": issues[n]["state"],
            "url": f"https://github.com/example/notional/issues/{n}",
        }))
    elif args[:2] != ["issue", "edit"]:
        sys.exit(f"stub gh: unexpected {args}")
elif tool == "herdr":
    if args[:2] == ["worktree", "list"]:
        print(json.dumps({"result": {"worktrees": []}}))
    elif args[:2] == ["worktree", "create"]:
        print(json.dumps({"result": {
            "workspace": {"workspace_id": "w1"},
            "tab": {"tab_id": "t1"},
            "root_pane": {"pane_id": "p1"},
        }}))
    # status server, tab rename/create, pane run/wait-output and
    # agent start/prompt all succeed silently.
"""

OPEN = {"state": "OPEN", "title": "Notional issue title"}
CLOSED = {"state": "CLOSED", "title": "Notional closed issue"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        stub = self.bin / "stub"
        stub.write_text(STUB.replace("{python}", sys.executable))
        stub.chmod(0o755)
        for tool in ("git", "gh", "herdr"):
            (self.bin / tool).symlink_to(stub)

        self.repo = tmp_path / "repo"
        self.repo.mkdir()

        self.config_dir = tmp_path / "claude-config"
        self.log = tmp_path / "calls.jsonl"

    def run(
        self, *args: str, issues: dict, origin_head: str = ""
    ) -> tuple[subprocess.CompletedProcess, list]:
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            # Keep the real user settings out of Superpowers detection.
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "STUB_LOG": str(self.log),
            "STUB_REPO": str(self.repo),
            "STUB_ISSUES": json.dumps(issues),
            "STUB_ORIGIN_HEAD": origin_head,
        }
        proc = subprocess.run(
            [str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        calls = []
        if self.log.exists():
            calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return proc, calls

    def enable_plugins(self, plugins: dict) -> None:
        self.config_dir.mkdir(exist_ok=True)
        (self.config_dir / "settings.json").write_text(json.dumps({"enabledPlugins": plugins}))


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def agent_starts(calls: list) -> list:
    return [c for c in calls if c[:3] == ["herdr", "agent", "start"]]


def agents_started(calls: list) -> list[str]:
    return [c[3] for c in agent_starts(calls)]


def test_single_issue_keeps_the_existing_output(harness):
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-101"]
    assert proc.stdout.startswith("Launched issue #101\n")
    assert "Launched:" not in proc.stdout


@pytest.mark.parametrize(
    "args", [("101", "1o2"), ("--ultracode",), ("",), ("101", "--plain", "--ultracode")]
)
def test_bad_or_missing_issue_numbers_launch_nothing(harness, args):
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert calls == []


def test_two_open_issues_both_launch(harness):
    proc, calls = harness.run("101", "102", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-101", "issue-102"]
    assert "Launched: #101 #102" in proc.stdout
    assert "Skipped:" not in proc.stdout


def test_closed_issue_is_skipped_and_the_rest_launch(harness):
    issues = {"101": OPEN, "102": CLOSED, "103": OPEN}
    proc, calls = harness.run("101", "102", "103", issues=issues)
    assert proc.returncode == 1
    assert agents_started(calls) == ["issue-101", "issue-103"]
    assert "Launched: #101 #103" in proc.stdout
    assert "Skipped:  #102  Issue #102 is not open (CLOSED)." in proc.stdout


def test_single_string_form_splits_and_applies_ultracode_to_each(harness):
    proc, calls = harness.run("101, 102 --ultracode", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    starts = agent_starts(calls)
    assert [c[3] for c in starts] == ["issue-101", "issue-102"]
    settings_dir = harness.repo / ".git" / "herdr-issue"
    for start, n in zip(starts, ("101", "102"), strict=True):
        path = settings_dir / f"issue-{n}.settings.json"
        assert start[start.index("--settings") + 1] == str(path)
        assert json.loads(path.read_text())["ultracode"] is True


@pytest.mark.parametrize(
    ("args", "effort"), [(("101",), "high"), (("101", "--ultracode"), "xhigh")]
)
def test_session_effort_is_high_and_ultracode_keeps_xhigh(harness, args, effort):
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    start = agent_starts(calls)[0]
    claude_args = start[start.index("--") + 1 :]
    assert claude_args[claude_args.index("--effort") + 1] == effort


def test_effort_is_never_exported_into_the_pane(harness):
    # CLAUDE_CODE_EFFORT_LEVEL outranks a subagent's own `effort:`, so an
    # export would pin final-reviewer to the session's high instead of xhigh.
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    typed = [c[4] for c in calls if c[:3] == ["herdr", "pane", "run"]]
    assert typed
    assert not any("CLAUDE_CODE_EFFORT_LEVEL" in cmd for cmd in typed)


@pytest.mark.parametrize(("args", "routed"), [(("101",), True), (("101", "--ultracode"), False)])
def test_only_the_high_effort_session_routes_its_final_review_to_final_reviewer(
    harness, args, routed
):
    # A plain or superpowers session runs at high, so it hands the final review to the
    # agent that raises effort back to xhigh; an ultracode session is already
    # at xhigh and keeps its own final-review line.
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = next(c for c in calls if c[:3] == ["herdr", "agent", "prompt"])[4]
    assert ("`herdr-issue:final-reviewer` agent" in prompt) is routed


def test_final_reviewer_agent_runs_at_xhigh():
    agent = (PLUGIN_ROOT / "agents" / "final-reviewer.md").read_text()
    frontmatter = agent.split("---")[1].splitlines()
    assert "name: final-reviewer" in frontmatter
    assert "effort: xhigh" in frontmatter


def test_repeated_number_launches_once(harness):
    proc, calls = harness.run("101", "102", "101", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-101", "issue-102"]


def test_branch_slug_never_ends_in_a_dash(harness):
    # The 48-character cut lands just after the x's, on the dash before "tail".
    title = "x" * 47 + " tail"
    proc, calls = harness.run("101", issues={"101": {"state": "OPEN", "title": title}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    create = next(c for c in calls if c[:3] == ["herdr", "worktree", "create"])
    assert create[create.index("--branch") + 1] == "issue/101-" + "x" * 47


def prompt_sent(calls: list) -> str:
    return next(c for c in calls if c[:3] == ["herdr", "agent", "prompt"])[4]


def test_a_repository_template_replaces_the_plugin_one(harness):
    override = harness.repo / ".claude" / "prompts" / "autonomous-issue.md"
    override.parent.mkdir(parents=True)
    override.write_text("Repository template for {{ISSUE_URL}}.\n")
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert prompt_sent(calls) == (
        "Repository template for https://github.com/example/notional/issues/101."
    )


@pytest.mark.parametrize(("origin_head", "base"), [("", "main"), ("origin/trunk", "trunk")])
def test_issue_branches_from_the_remote_default_branch(harness, origin_head, base):
    proc, calls = harness.run("101", issues={"101": OPEN}, origin_head=origin_head)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert next(c for c in calls if "fetch" in c)[-3:] == ["fetch", "origin", base]
    create = next(c for c in calls if c[:3] == ["herdr", "worktree", "create"])
    assert create[create.index("--base") + 1] == f"origin/{base}"
    prompt = prompt_sent(calls)
    assert "{{" not in prompt
    assert f"`git fetch origin {base}`" in prompt


SUPERPOWERS = "superpowers@superpowers-marketplace"


def test_without_superpowers_the_default_mode_is_plain(harness):
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Mode:      plain (Superpowers plugin not enabled)" in proc.stdout
    assert "Superpowers" not in prompt_sent(calls)
    assert "--settings" not in agent_starts(calls)[0]


def test_a_disabled_superpowers_plugin_does_not_count_as_detected(harness):
    harness.enable_plugins({SUPERPOWERS: False})
    proc, _ = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Mode:      plain (Superpowers plugin not enabled)" in proc.stdout


def test_an_enabled_superpowers_plugin_makes_superpowers_the_default(harness):
    harness.enable_plugins({SUPERPOWERS: True})
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Mode:      superpowers (Superpowers plugin detected;" in proc.stdout
    assert "normal Superpowers\nworkflow" in prompt_sent(calls)
    assert "--settings" not in agent_starts(calls)[0]


def test_plain_flag_switches_an_enabled_superpowers_plugin_off(harness):
    harness.enable_plugins({SUPERPOWERS: True, "other@somewhere": True})
    proc, calls = harness.run("101", "--plain", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Mode:      plain\n" in proc.stdout
    assert "Superpowers" not in prompt_sent(calls)
    start = agent_starts(calls)[0]
    settings = json.loads(Path(start[start.index("--settings") + 1]).read_text())
    assert settings == {"enabledPlugins": {SUPERPOWERS: False}}


def test_several_issues_pass_the_plain_flag_to_each(harness):
    harness.enable_plugins({SUPERPOWERS: True})
    proc, calls = harness.run("101 102 --plain", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("Mode:      plain\n") == 2
    assert all("--settings" in start for start in agent_starts(calls))
