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
        worktrees = json.loads(os.environ.get("STUB_WORKTREES", "[]"))
        print(json.dumps({"result": {"worktrees": worktrees}}))
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
        self, *args: str, issues: dict, origin_head: str = "", worktrees: list | None = None
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
            "STUB_WORKTREES": json.dumps(worktrees or []),
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


def use_template(harness, text: str) -> None:
    override = harness.repo / ".claude" / "prompts" / "autonomous-issue.md"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(text)


URL = "https://github.com/example/notional/issues/{}"

NESTED = """\
A
{{#plain}}
B
{{^multi}}
C
{{/multi}}
{{#multi}}
D
{{/multi}}
{{/plain}}
{{#ultracode}}
E
{{^multi}}
F
{{/multi}}
{{/ultracode}}
G {{ISSUES}}
"""


def test_sections_nest_and_invert(harness):
    use_template(harness, NESTED)
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert prompt_sent(calls) == f"A\nB\nC\nG {URL.format(101)}"


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("{{#bogus}}\nx\n{{/bogus}}\n", "line 1: unknown section {{#bogus}}"),
        ("{{#plain}}\nx\n{{/ultracode}}\n", "line 3: unmatched {{/ultracode}}"),
        ("{{/plain}}\n", "line 1: unmatched {{/plain}}"),
        ("{{^multi}}\nx\n", "line 2: {{^multi}} is never closed"),
    ],
)
def test_template_mistakes_launch_nothing(harness, text, error):
    use_template(harness, text)
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 1
    assert error in proc.stderr
    assert not any(c[:3] == ["herdr", "worktree", "create"] for c in calls)
    assert agent_starts(calls) == []


def test_values_are_substituted_literally(harness):
    # A ref name may hold & and |, which sed's s||| would misread.
    use_template(harness, "{{DEFAULT_BRANCH}} {{ISSUES}}\n")
    proc, calls = harness.run("101", issues={"101": OPEN}, origin_head="origin/re&l|ease")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert prompt_sent(calls) == f"re&l|ease {URL.format(101)}"


def test_plugin_prompt_opens_with_the_issue(harness):
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert prompt_sent(calls).startswith(f"Let's start working on {URL.format(101)}.\n")


def created_worktree(calls: list) -> list:
    return next(c for c in calls if c[:3] == ["herdr", "worktree", "create"])


def launched_nothing(calls: list) -> bool:
    return (
        not any(c[:3] == ["herdr", "worktree", "create"] for c in calls)
        and agent_starts(calls) == []
        and not any(c[:3] == ["gh", "issue", "edit"] for c in calls)
    )


def test_several_issues_share_one_session(harness):
    issues = {"101": OPEN, "102": {"state": "OPEN", "title": "Second"}}
    proc, calls = harness.run("101", "102", issues=issues)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-101_102"]
    create = created_worktree(calls)
    assert create[create.index("--branch") + 1] == "issue/101_102-notional-issue-title"
    assert create[create.index("--label") + 1] == "#101 #102 Notional issue title"
    edits = [c[3] for c in calls if c[:3] == ["gh", "issue", "edit"]]
    assert edits == ["101", "102"]
    assert proc.stdout.startswith("Launched issues #101 #102\n")
    assert f"Issue:     {URL.format(101)}\nIssue:     {URL.format(102)}\n" in proc.stdout
    assert prompt_sent(calls).startswith(
        f"Let's start working on {URL.format(101)} and {URL.format(102)}.\n"
    )


def test_single_string_form_splits_and_applies_ultracode(harness):
    proc, calls = harness.run("101, 102 --ultracode", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (start,) = agent_starts(calls)
    assert start[3] == "issue-101_102"
    path = harness.repo / ".git" / "herdr-issue" / "issue-101_102.settings.json"
    assert start[start.index("--settings") + 1] == str(path)
    assert json.loads(path.read_text())["ultracode"] is True


def test_several_issues_take_the_plain_flag(harness):
    harness.enable_plugins({SUPERPOWERS: True})
    proc, calls = harness.run("101 102 --plain", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("Mode:      plain\n") == 1
    assert "--settings" in agent_starts(calls)[0]


def test_repeated_number_launches_once(harness):
    proc, calls = harness.run("101", "102", "101", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-101_102"]


def test_closed_issues_launch_nothing_and_are_all_reported(harness):
    issues = {"101": OPEN, "102": CLOSED, "103": CLOSED}
    proc, calls = harness.run("101", "102", "103", issues=issues)
    assert proc.returncode == 1
    assert "Issue #102 is not open (CLOSED)." in proc.stderr
    assert "Issue #103 is not open (CLOSED)." in proc.stderr
    assert "Nothing was launched" in proc.stderr
    assert launched_nothing(calls)


@pytest.mark.parametrize(
    ("branch", "agent"),
    [
        ("issue/102-an-old-title", "issue-102"),
        ("issue/101_102-notional-issue-title", "issue-101_102"),
        ("issue/7_102_9-x", "issue-7_102_9"),
    ],
)
def test_an_issue_already_in_a_worktree_launches_nothing(harness, branch, agent):
    worktrees = [{"branch": branch, "path": "/wt/existing"}]
    proc, calls = harness.run(
        "101", "102", issues={"101": OPEN, "102": OPEN}, worktrees=worktrees
    )
    assert proc.returncode == 1
    assert "Issue #102 already has a worktree." in proc.stderr
    assert f"Agent:     {agent} (not running)" in proc.stderr
    assert launched_nothing(calls)


def test_a_single_issue_inside_a_combined_worktree_is_refused(harness):
    worktrees = [{"branch": "issue/101_102-notional-issue-title", "path": "/wt/existing"}]
    proc, calls = harness.run("102", issues={"102": OPEN}, worktrees=worktrees)
    assert proc.returncode == 1
    assert "Issue #102 already has a worktree." in proc.stderr
    assert launched_nothing(calls)


@pytest.mark.parametrize(
    "branch", ["issue/1020-x", "issue/12-102-errors", "feature/102-x", "main"]
)
def test_other_branches_do_not_hold_the_issue(harness, branch):
    worktrees = [{"branch": branch, "path": "/wt/other"}, {"path": "/wt/detached"}]
    proc, calls = harness.run("102", issues={"102": OPEN}, worktrees=worktrees)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-102"]


def test_issue_url_template_refuses_several_issues(harness):
    use_template(harness, "Work on {{ISSUE_URL}}.\n")
    proc, calls = harness.run("101", "102", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 1
    assert "use {{ISSUES}}" in proc.stderr
    assert launched_nothing(calls)


@pytest.mark.parametrize(
    ("numbers", "listed"),
    [
        (("101", "102"), f"{URL.format(101)} and {URL.format(102)}"),
        (("101", "102", "103"), f"{URL.format(101)}, {URL.format(102)} and {URL.format(103)}"),
    ],
)
def test_multi_sections_and_issue_list(harness, numbers, listed):
    use_template(harness, "{{#multi}}\nM\n{{/multi}}\n{{^multi}}\nS\n{{/multi}}\n{{ISSUES}}\n")
    proc, calls = harness.run(*numbers, issues={n: OPEN for n in numbers})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert prompt_sent(calls) == f"M\n{listed}"


def test_several_issues_are_told_they_share_one_pr(harness):
    proc, calls = harness.run("101", "102", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = prompt_sent(calls)
    assert "{{" not in prompt
    assert "These issues are fixed together, on one branch and in one draft PR." in prompt
    assert "one `Closes #N` line per issue." in prompt
    assert "Ensure the PR closes every referenced issue." in prompt


def test_one_issue_is_not_told_about_several(harness):
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = prompt_sent(calls)
    assert "These issues are fixed together" not in prompt
    assert prompt.startswith(f"Let's start working on {URL.format(101)}.\n\n")
    assert "\n\n\n" not in prompt
