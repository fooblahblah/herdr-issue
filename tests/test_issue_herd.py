"""issue-herd launcher tests.

These drive the REAL script via subprocess with stub `herdr`, `orca`, `gh` and `git`
on PATH, so no Herdr server, Orca runtime, GitHub call or git fetch happens. Every
stub call appends its argv as one JSON line to a log, so a test can assert exactly
which calls the script made. Real `jq` is required: the script parses every host
and gh reply with it.

By default the stub Herdr server is up and the stub Orca is not, and neither
host's environment variable is set, so a launch is detected as Herdr's.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "issue-herd"
SCRIPT = PLUGIN_ROOT / "scripts" / "issue-herd"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None, reason="issue-herd needs jq"
)

# One stub serves git, gh and herdr; it dispatches on the name it was invoked as.
STUB = r"""#!{python}
import json, os, re, sys

tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\n")

if tool == "git":
    if args == ["rev-parse", "--show-toplevel"]:
        print(os.environ["STUB_REPO"])
    elif args[-1] == "--git-common-dir":
        print(os.path.join(os.environ["STUB_REPO"], ".git"))
    elif args[-3:] == ["worktree", "list", "--porcelain"]:
        # By default only the main checkout, on main.
        worktrees = json.loads(os.environ.get("STUB_GIT_WORKTREES") or "null") or [
            {"path": os.environ["STUB_REPO"], "branch": "refs/heads/main"}
        ]
        for wt in worktrees:
            print(f"worktree {wt['path']}")
            print("HEAD 0123456789abcdef0123456789abcdef01234567")
            print(f"branch {wt['branch']}" if "branch" in wt else "detached")
            print()
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
    if args[:2] == ["status", "server"]:
        sys.exit(0 if os.environ.get("STUB_HERDR_UP") == "1" else 1)
    elif args[:2] == ["worktree", "list"]:
        worktrees = json.loads(os.environ.get("STUB_WORKTREES", "[]"))
        print(json.dumps({"result": {"worktrees": worktrees}}))
    elif args[:2] == ["worktree", "create"]:
        print(json.dumps({"result": {
            "workspace": {"workspace_id": "w1"},
            "tab": {"tab_id": "t1"},
            "root_pane": {"pane_id": "p1"},
        }}))
    elif args[:2] == ["agent", "start"] and not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", args[2]):
        sys.exit(f"invalid_agent_name: {args[2]}")
    elif args[:2] == ["agent", "list"]:
        agents = json.loads(os.environ.get("STUB_AGENTS") or "[]")
        print(json.dumps({"result": {"agents": agents}}))
    elif args[:2] == ["agent", "prompt"] and os.environ.get("STUB_HERDR_BLOCKED") == "1":
        sys.exit("agent_blocked: the agent is waiting on a permission prompt")
    # tab rename/create, pane run/wait-output and agent prompt all succeed
    # silently.
elif tool == "orca":
    if args[:1] == ["status"]:
        if os.environ.get("STUB_ORCA_UP") != "1":
            sys.exit("Orca is not running")
        print(json.dumps({"ok": True, "result": {"runtime": {"reachable": True}}}))
    elif args[:2] == ["worktree", "list"]:
        if os.environ.get("STUB_ORCA_WARN") == "1":
            print("warning: something on the side", file=sys.stderr)
        if os.environ.get("STUB_ORCA_REPO_UNKNOWN") == "1":
            print(json.dumps({"ok": False, "error": {"code": "repo_not_found"}}))
            sys.exit(1)
        worktrees = json.loads(os.environ.get("STUB_WORKTREES", "[]"))
        print(json.dumps({"ok": True, "result": {"worktrees": worktrees}}))
    elif args[:2] == ["worktree", "create"]:
        if args[args.index("--model") + 1] == "haiku" and "--effort" in args:
            print(json.dumps({"ok": False, "error": {"message": "does not support effort"}}))
            sys.exit(1)
        name = args[args.index("--name") + 1]
        branch = args[args.index("--branch") + 1]
        if os.environ.get("STUB_ORCA_BRANCH_TAKEN") == "1":
            branch += "-2"
        if os.environ.get("STUB_ORCA_WARN") == "1":
            print("warning: something on the side", file=sys.stderr)
        print(json.dumps({"ok": True, "result": {"worktree": {
            "id": "r1::/wt/new", "path": "/wt/new", "displayName": name,
            "branch": f"refs/heads/{branch}",
        }}}))
    elif args[:2] == ["terminal", "create"]:
        print(json.dumps({"ok": True, "result": {"handle": "term_1"}}))
    elif args[:2] == ["terminal", "list"]:
        # Orca refuses a worktree it does not manage.
        terminals = os.environ.get("STUB_ORCA_TERMINALS")
        if terminals is None:
            print(json.dumps({"ok": False, "error": {"code": "selector_not_found"}}))
            sys.exit(1)
        print(json.dumps({"ok": True, "result": {"terminals": json.loads(terminals)}}))
    elif args[:2] == ["terminal", "send"]:
        stages = json.loads(os.environ.get("STUB_ORCA_STAGES") or '["input_accepted", "turn_started"]')
        print(json.dumps({"ok": True, "result": {"send": {
            "handle": args[args.index("--terminal") + 1], "accepted": True,
            "prompt": {"stages": stages},
        }}}))
    else:
        sys.exit(f"stub orca: unexpected {args}")
"""

HOST_VARS = ("HERDR_PANE_ID", "ORCA_TERMINAL_HANDLE")

OPEN = {"state": "OPEN", "title": "Notional issue title"}
CLOSED = {"state": "CLOSED", "title": "Notional closed issue"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        stub = self.bin / "stub"
        stub.write_text(STUB.replace("{python}", sys.executable))
        stub.chmod(0o755)
        for tool in ("git", "gh", "herdr", "orca"):
            (self.bin / tool).symlink_to(stub)

        self.repo = tmp_path / "repo"
        self.repo.mkdir()

        self.config_dir = tmp_path / "claude-config"
        self.xdg_config = tmp_path / "xdg-config"
        self.log = tmp_path / "calls.jsonl"

    def run(
        self,
        *args: str,
        issues: dict,
        origin_head: str = "",
        worktrees: list | None = None,
        git_worktrees: list | None = None,
        herdr_up: bool = True,
        orca_up: bool = False,
        orca_repo_known: bool = True,
        host_env: dict | None = None,
        orca_on_path: bool = True,
        stub_env: dict | None = None,
        script: Path = SCRIPT,
        stdin: str | None = None,
    ) -> tuple[subprocess.CompletedProcess, list]:
        # The tests may themselves run inside a Herdr pane or an Orca
        # terminal; neither host's variables reach the script unless a test
        # sets them.
        env = {k: v for k, v in os.environ.items() if k not in HOST_VARS}
        path = [str(self.bin), *os.environ["PATH"].split(os.pathsep)]
        if not orca_on_path:
            # Only in the shim directory Orca keeps under its config dir.
            shim = self.xdg_config / "orca" / "linux-orca-cli-shim" / "orca"
            shim.parent.mkdir(parents=True, exist_ok=True)
            if (self.bin / "orca").is_symlink():
                (self.bin / "orca").rename(shim)
            real = [d for d in path[1:] if (Path(d) / "orca").exists()]
            if any((Path(d) / "jq").exists() for d in real):
                pytest.skip("a real orca shares a PATH directory with jq")
            path = [d for d in path if d not in real]
        env.update({
            "PATH": os.pathsep.join(path),
            # Keep the real user settings out of Superpowers detection.
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            # Keep the real user's issue-herd config out of model selection.
            "XDG_CONFIG_HOME": str(self.xdg_config),
            "STUB_LOG": str(self.log),
            "STUB_REPO": str(self.repo),
            "STUB_ISSUES": json.dumps(issues),
            "STUB_ORIGIN_HEAD": origin_head,
            "STUB_WORKTREES": json.dumps(worktrees or []),
            "STUB_GIT_WORKTREES": json.dumps(git_worktrees),
            "STUB_HERDR_UP": "1" if herdr_up else "0",
            "STUB_ORCA_UP": "1" if orca_up else "0",
            "STUB_ORCA_REPO_UNKNOWN": "0" if orca_repo_known else "1",
            **(stub_env or {}),
            **(host_env or {}),
        })
        proc = subprocess.run(
            [str(script), *args],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            # With no input, stdin is closed rather than inherited, so a
            # script that reads it gets nothing instead of waiting.
            **({} if stdin is not None else {"stdin": subprocess.DEVNULL}),
        )
        calls = []
        if self.log.exists():
            calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return proc, calls

    def enable_plugins(self, plugins: dict) -> None:
        self.config_dir.mkdir(exist_ok=True)
        (self.config_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": plugins})
        )

    @property
    def user_config(self) -> Path:
        return self.xdg_config / "issue-herd" / "config.json"

    @property
    def repo_config(self) -> Path:
        return self.repo / ".claude" / "issue-herd.json"

    def write_config(self, path: Path, config: dict | str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config if isinstance(config, str) else json.dumps(config))


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


def sidebar_labels(calls: list) -> list[list[str]]:
    return [c[3:] for c in calls if c[:3] == ["herdr", "pane", "report-metadata"]]


@pytest.mark.parametrize(
    ("args", "label"),
    [
        (("101",), "Issue: #101"),
        (("101", "102", "103"), "Issues: #101, #102, #103"),
    ],
)
def test_the_sidebar_names_the_issues_from_launch(harness, args, label):
    proc, calls = harness.run(*args, issues={n: OPEN for n in args})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sidebar_labels(calls) == [
        ["p1", "--source", "issue-herd", "--display-agent", label]
    ]
    # Set once the agent is running, so its detection cannot replace it.
    names = [" ".join(c[1:3]) for c in calls]
    assert names.index("agent start") < names.index("pane report-metadata")


@pytest.mark.parametrize(
    "args",
    [
        ("101", "1o2"),
        ("--ultracode",),
        ("",),
        ("101", "--plain", "--ultracode"),
        ("#",),
        ("##101",),
        ("101#",),
    ],
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


def test_the_pane_ready_marker_matches_only_once_the_shell_runs_it(harness):
    # The stub accepts any wait-output, but real Herdr would wait out its
    # timeout if the printed marker and --match ever drifted apart.
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    names = [c[:3] for c in calls]
    wait = names.index(["herdr", "pane", "wait-output"])
    match = calls[wait][calls[wait].index("--match") + 1]
    typed = next(
        c[4] for c in reversed(calls[:wait]) if c[:3] == ["herdr", "pane", "run"]
    )
    shell = subprocess.run(
        ["bash", "-c", typed],
        cwd=harness.repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert shell.returncode == 0, shell.stdout + shell.stderr
    assert match in shell.stdout
    assert match not in typed


def test_the_issue_skill_runs_the_launcher_script():
    skill = (PLUGIN_ROOT / "skills" / "issue" / "SKILL.md").read_text()
    names = re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([^"\s]+)', skill)
    assert names == [SCRIPT.name]
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.parametrize(
    ("args", "routed"), [(("101",), True), (("101", "--ultracode"), False)]
)
def test_only_the_high_effort_session_routes_its_final_review_to_final_reviewer(
    harness, args, routed
):
    # A plain or superpowers session runs at high, so it hands the final review to the
    # agent that raises effort back to xhigh; an ultracode session is already
    # at xhigh and keeps its own final-review line.
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = next(c for c in calls if c[:3] == ["herdr", "agent", "prompt"])[4]
    assert ("`issue-herd:final-reviewer` agent" in prompt) is routed


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


@pytest.mark.parametrize(
    ("origin_head", "base"), [("", "main"), ("origin/trunk", "trunk")]
)
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


def test_plugin_prompt_names_the_pr_land_script(harness):
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pr_land = PLUGIN_ROOT / "scripts" / "pr-land"
    assert f'"{pr_land}" <PR number> -- <gh pr merge flags>' in prompt_sent(calls)


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
    proc, calls = harness.run(
        "101", issues={"101": OPEN}, origin_head="origin/re&l|ease"
    )
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
        not any(c[1:3] == ["worktree", "create"] for c in calls)
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
    assert (
        f"Issue:     {URL.format(101)}\nIssue:     {URL.format(102)}\n" in proc.stdout
    )
    assert prompt_sent(calls).startswith(
        f"Let's start working on {URL.format(101)} and {URL.format(102)}.\n"
    )


def test_single_string_form_splits_and_applies_ultracode(harness):
    proc, calls = harness.run("101, 102 --ultracode", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (start,) = agent_starts(calls)
    assert start[3] == "issue-101_102"
    path = harness.repo / ".git" / "issue-herd" / "issue-101_102.settings.json"
    assert start[start.index("--settings") + 1] == str(path)
    assert json.loads(path.read_text())["ultracode"] is True


def test_hash_prefixed_list_names_the_issues(harness):
    issues = {"111": OPEN, "123": OPEN, "125": OPEN}
    proc, calls = harness.run("#123, #125, #111", issues=issues)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-123_125_111"]
    assert proc.stdout.startswith("Launched issues #123 #125 #111\n")


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
    worktrees = [
        {"branch": "issue/101_102-notional-issue-title", "path": "/wt/existing"}
    ]
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
        (
            ("101", "102", "103"),
            f"{URL.format(101)}, {URL.format(102)} and {URL.format(103)}",
        ),
    ],
)
def test_multi_sections_and_issue_list(harness, numbers, listed):
    use_template(
        harness, "{{#multi}}\nM\n{{/multi}}\n{{^multi}}\nS\n{{/multi}}\n{{ISSUES}}\n"
    )
    proc, calls = harness.run(*numbers, issues={n: OPEN for n in numbers})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert prompt_sent(calls) == f"M\n{listed}"


def test_several_issues_are_told_they_share_one_pr(harness):
    proc, calls = harness.run("101", "102", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = prompt_sent(calls)
    assert "{{" not in prompt
    assert (
        "These issues are fixed together, on one branch and in one draft PR." in prompt
    )
    assert "one `Closes #N` line per issue." in prompt
    assert "Ensure the PR closes every referenced issue." in prompt


def test_one_issue_is_not_told_about_several(harness):
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = prompt_sent(calls)
    assert "These issues are fixed together" not in prompt
    assert prompt.startswith(f"Let's start working on {URL.format(101)}.\n\n")
    assert "\n\n\n" not in prompt


def test_agent_name_drops_trailing_numbers_to_fit_herdr(harness):
    numbers = ("2696", "2697", "2718", "2719", "2720", "2721")
    proc, calls = harness.run(*numbers, issues={n: OPEN for n in numbers})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-2696_2697_2718_2719_2720"]
    create = created_worktree(calls)
    branch = create[create.index("--branch") + 1]
    assert branch.startswith("issue/2696_2697_2718_2719_2720_2721-")


def test_an_existing_long_worktree_reports_the_shortened_agent(harness):
    branch = "issue/2696_2697_2718_2719_2720_2721-notional-issue-title"
    worktrees = [{"branch": branch, "path": "/wt/existing"}]
    proc, calls = harness.run("2718", issues={"2718": OPEN}, worktrees=worktrees)
    assert proc.returncode == 1
    assert "Agent:     issue-2696_2697_2718_2719_2720 (not running)" in proc.stderr
    assert launched_nothing(calls)


def test_issue_url_inside_a_hidden_section_does_not_refuse_several_issues(harness):
    use_template(harness, "{{^multi}}\nOne {{ISSUE_URL}}\n{{/multi}}\n{{ISSUES}}\n")
    proc, calls = harness.run("101", "102", issues={"101": OPEN, "102": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert prompt_sent(calls) == f"{URL.format(101)} and {URL.format(102)}"


def test_leading_zeros_name_the_same_issue(harness):
    proc, calls = harness.run("101", "0101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-101"]
    assert [c[3] for c in calls if c[:3] == ["gh", "issue", "edit"]] == ["101"]


def test_a_single_issue_failure_does_not_claim_a_group_launch(harness):
    proc, calls = harness.run("101", issues={"101": CLOSED})
    assert proc.returncode == 1
    assert "Issue #101 is not open (CLOSED)." in proc.stderr
    assert "Nothing was launched" not in proc.stderr
    assert launched_nothing(calls)


# --- Model and effort selection -------------------------------------------------


def claude_args(calls: list) -> list[str]:
    start = agent_starts(calls)[0]
    return start[start.index("--") + 1 :]


def session_model(calls: list) -> tuple[str, str]:
    args = claude_args(calls)
    return args[args.index("--model") + 1], args[args.index("--effort") + 1]


@pytest.mark.parametrize(
    ("args", "effort"), [(("101",), "high"), (("101", "--ultracode"), "xhigh")]
)
def test_default_session_runs_opus(harness, args, effort):
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("opus", effort)
    assert f"Model:     opus ({effort} effort)\n" in proc.stdout


@pytest.mark.parametrize("args", [("101",), ("101", "--ultracode")])
def test_default_prompt_routes_as_before(harness, args):
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = prompt_sent(calls)
    flat = " ".join(prompt.split())
    assert "{{" not in prompt
    assert "Use Opus 5.5 as the parent coordinator." in prompt
    assert "inherit Opus 5.5 simply because it is the parent" in flat
    assert "Use Haiku 5.5 for:" in prompt
    assert "do not use Haiku 5.5 for reviews" in flat
    assert "Use Sonnet 5.5 by default for:" in prompt
    assert "Use Opus 5.5 for:" in prompt
    assert "Escalate to Opus 5.5 for subtle" in prompt


def test_default_final_review_is_opus_at_xhigh(harness):
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = " ".join(prompt_sent(calls).split())
    assert (
        "`issue-herd:final-reviewer` agent, dispatched with model `opus` and"
        " effort `xhigh` (this session runs at high)"
    ) in prompt


def test_user_config_switches_the_coordinator(harness):
    harness.write_config(harness.user_config, {"coordinator": {"model": "fable"}})
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("fable", "high")
    prompt = prompt_sent(calls)
    assert "Use Fable 5.2 as the parent coordinator." in prompt
    assert "inherit Fable 5.2 simply because it is the parent" in " ".join(
        prompt.split()
    )
    # The routing tiers are configured separately and keep their defaults.
    assert "Use Opus 5.5 for:" in prompt


def test_repo_config_merges_over_user_config(harness):
    harness.write_config(harness.user_config, {"coordinator": {"model": "fable"}})
    harness.write_config(harness.repo_config, {"coordinator": {"effort": "medium"}})
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("fable", "medium")
    assert "this session runs at medium" in " ".join(prompt_sent(calls).split())


def test_ultracode_follows_the_coordinator_model_and_keeps_its_own_effort(harness):
    harness.write_config(harness.user_config, {"coordinator": {"model": "fable"}})
    proc, calls = harness.run("101", "--ultracode", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("fable", "xhigh")


def test_ultracode_coordinator_can_be_configured_apart(harness):
    harness.write_config(
        harness.repo_config,
        {"ultracode_coordinator": {"model": "sonnet", "effort": "max"}},
    )
    proc, calls = harness.run("101", "--ultracode", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("sonnet", "max")
    # A non-ultracode launch is unaffected.
    harness.log.unlink()
    proc, calls = harness.run("102", issues={"102": OPEN})
    assert session_model(calls) == ("opus", "high")


def test_standard_tier_can_be_opus_at_lower_effort(harness):
    harness.write_config(
        harness.repo_config,
        {"tiers": {"standard": {"model": "opus", "effort": "medium"}}},
    )
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = prompt_sent(calls)
    assert "Use Opus 5.5 at medium effort by default for:" in prompt
    assert "Sonnet" not in prompt
    assert "Use Haiku 5.5 for:" in prompt
    # The session itself is unchanged.
    assert session_model(calls) == ("opus", "high")


def test_every_tier_can_be_switched(harness):
    harness.write_config(
        harness.repo_config,
        {
            "tiers": {
                "light": {"model": "sonnet", "effort": "low"},
                "standard": {"model": "opus"},
                "heavy": {"model": "fable", "effort": "high"},
            }
        },
    )
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = prompt_sent(calls)
    assert "Use Sonnet 5.5 at low effort for:" in prompt
    assert "Use Opus 5.5 by default for:" in prompt
    assert "Use Fable 5.2 at high effort for:" in prompt
    assert "Escalate to Fable 5.2 at high effort for subtle" in prompt
    assert "Haiku" not in prompt


def test_final_review_can_be_switched(harness):
    harness.write_config(
        harness.user_config, {"final_review": {"model": "fable", "effort": "high"}}
    )
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prompt = " ".join(prompt_sent(calls).split())
    assert "dispatched with model `fable` and effort `high`" in prompt


def test_ultracode_final_review_uses_the_configured_model(harness):
    harness.write_config(harness.user_config, {"final_review": {"model": "fable"}})
    proc, calls = harness.run("101", "--ultracode", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "final whole-branch review using Fable 5.2" in prompt_sent(calls)


@pytest.mark.parametrize(
    "args",
    [
        ("101", "--model", "fable", "--effort", "xhigh"),
        ("101", "--model=fable", "--effort=xhigh"),
        ("101 --model fable --effort xhigh",),
    ],
)
def test_flags_override_the_config(harness, args):
    harness.write_config(
        harness.repo_config, {"coordinator": {"model": "sonnet", "effort": "low"}}
    )
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("fable", "xhigh")
    assert "Use Fable 5.2 as the parent coordinator." in prompt_sent(calls)


def test_flags_apply_to_the_ultracode_coordinator(harness):
    proc, calls = harness.run(
        "101", "--ultracode", "--effort", "max", issues={"101": OPEN}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("opus", "max")


def test_a_full_model_id_reaches_the_session_verbatim(harness):
    proc, calls = harness.run(
        "101", "--model", "claude-fable-5-2", issues={"101": OPEN}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert session_model(calls) == ("claude-fable-5-2", "high")
    assert "Use claude-fable-5-2 as the parent coordinator." in prompt_sent(calls)


@pytest.mark.parametrize(
    "args",
    [
        ("101", "--model"),
        ("101", "--effort"),
        ("101", "--model", "--effort", "high"),
        ("101", "--model="),
    ],
)
def test_a_flag_without_its_value_launches_nothing(harness, args):
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert calls == []


@pytest.mark.parametrize(
    ("where", "config", "error"),
    [
        ("repo", "{not json", "issue-herd.json"),
        (
            "repo",
            {"coordinator": {"effort": "extreme"}},
            "coordinator.effort must be one of low, medium, high, xhigh, max",
        ),
        (
            "user",
            {"tiers": {"standard": {"model": "claude-opus-5-5"}}},
            "tiers.standard.model must be one of haiku, sonnet, opus, fable",
        ),
        (
            "user",
            {"final_review": {"model": "gpt"}},
            "final_review.model must be one of haiku, sonnet, opus, fable",
        ),
        ("repo", {"coordinater": {"model": "fable"}}, "unknown key coordinater"),
        ("repo", {"tiers": {"medium": {"model": "opus"}}}, "unknown key tiers.medium"),
        (
            "repo",
            {"coordinator": {"model": "fable; rm -rf /"}},
            "coordinator.model is not a model name",
        ),
        (
            "repo",
            {"coordinator": {"effort": None}},
            "coordinator.effort must be one of",
        ),
        ("repo", ["opus"], "must hold a JSON object"),
    ],
)
def test_a_bad_config_launches_nothing(harness, where, config, error):
    path = harness.repo_config if where == "repo" else harness.user_config
    harness.write_config(path, config)
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 1
    assert error in proc.stderr
    assert str(path) in proc.stderr
    assert launched_nothing(calls)


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (("101", "--effort", "extreme"), "--effort must be one of low, medium"),
        (("101", "--model", "opus;rm"), "--model is not a model name"),
    ],
)
def test_a_bad_flag_value_launches_nothing(harness, args, error):
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 1
    assert error in proc.stderr
    assert launched_nothing(calls)


def test_a_tier_effort_of_null_falls_back_to_inheriting(harness):
    harness.write_config(
        harness.user_config, {"tiers": {"standard": {"model": "opus", "effort": "low"}}}
    )
    harness.write_config(harness.repo_config, {"tiers": {"standard": {"effort": None}}})
    proc, calls = harness.run("101", issues={"101": OPEN})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Use Opus 5.5 by default for:" in prompt_sent(calls)


def test_the_shipped_defaults_file_matches_the_built_in_defaults():
    defaults = json.loads((PLUGIN_ROOT / "config" / "defaults.json").read_text())
    assert defaults == {
        "coordinator": {"model": "opus", "effort": "high"},
        "ultracode_coordinator": {"effort": "xhigh"},
        "tiers": {
            "light": {"model": "haiku"},
            "standard": {"model": "sonnet"},
            "heavy": {"model": "opus"},
        },
        "final_review": {"model": "opus", "effort": "xhigh"},
    }


# --- Orca --------------------------------------------------------------------


IN_ORCA = {"ORCA_TERMINAL_HANDLE": "term_0"}
IN_HERDR = {"HERDR_PANE_ID": "w0:p0"}


def orca_calls(calls: list) -> list:
    return [c for c in calls if c[0] == "orca"]


def herdr_calls(calls: list) -> list:
    return [c for c in calls if c[0] == "herdr"]


def orca_create(calls: list) -> list:
    return next(c for c in calls if c[:3] == ["orca", "worktree", "create"])


def flag(call: list, name: str) -> str:
    return call[call.index(name) + 1]


def flags(call: list, name: str) -> list[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == name]


def test_a_herdr_pane_launches_in_herdr_even_when_orca_runs(harness):
    proc, calls = harness.run(
        "101", issues={"101": OPEN}, orca_up=True, host_env=IN_HERDR
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-101"]
    assert orca_calls(calls) == []
    assert "running independently in Herdr." in proc.stdout


def test_an_orca_terminal_launches_in_orca_even_when_herdr_runs(harness):
    proc, calls = harness.run(
        "101", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert herdr_calls(calls) == []
    assert [c[1:3] for c in orca_calls(calls)] == [
        ["status", "--json"],
        ["worktree", "list"],
        ["worktree", "create"],
        ["terminal", "create"],
    ]
    assert "running independently in Orca." in proc.stdout


def test_outside_both_hosts_the_running_one_is_used(harness):
    proc, calls = harness.run("101", issues={"101": OPEN}, herdr_up=False, orca_up=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert herdr_calls(calls) == [["herdr", "status", "server"]]
    assert orca_create(calls)
    harness.log.unlink()
    proc, calls = harness.run("102", issues={"102": OPEN}, herdr_up=True, orca_up=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agents_started(calls) == ["issue-102"]
    assert [c[1:3] for c in orca_calls(calls)] == [["status", "--json"]]


def test_both_hosts_running_outside_either_needs_the_backend_flag(harness):
    proc, calls = harness.run("101", issues={"101": OPEN}, orca_up=True)
    assert proc.returncode == 1
    assert "Both Herdr and Orca are running; pass --backend herdr or --backend orca." in proc.stderr
    assert launched_nothing(calls)


def test_neither_host_running_launches_nothing(harness):
    proc, calls = harness.run("101", issues={"101": OPEN}, herdr_up=False)
    assert proc.returncode == 1
    assert "Neither Herdr nor Orca is running." in proc.stderr
    assert launched_nothing(calls)


@pytest.mark.parametrize("args", [("101", "--backend", "orca"), ("101 --backend=orca",)])
def test_the_backend_flag_overrides_detection(harness, args):
    proc, calls = harness.run(*args, issues={"101": OPEN}, orca_up=True, host_env=IN_HERDR)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert herdr_calls(calls) == []
    assert orca_create(calls)


def test_a_backend_flag_naming_a_stopped_host_launches_nothing(harness):
    proc, calls = harness.run("101", "--backend", "orca", issues={"101": OPEN})
    assert proc.returncode == 1
    assert "Orca is not running. Start Orca first." in proc.stderr
    assert launched_nothing(calls)


@pytest.mark.parametrize("args", [("101", "--backend", "zed"), ("101", "--backend")])
def test_a_bad_backend_is_a_usage_error(harness, args):
    proc, calls = harness.run(*args, issues={"101": OPEN})
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert calls == []


def test_orca_creates_the_worktree_with_the_agent_and_prompt_in_one_call(harness):
    proc, calls = harness.run("101", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    create = orca_create(calls)
    assert flag(create, "--repo") == f"path:{harness.repo}"
    assert flag(create, "--name") == "#101 Notional issue title"
    assert flag(create, "--branch") == "issue/101-notional-issue-title"
    assert flag(create, "--base-branch") == "origin/main"
    assert flags(create, "--reference") == [URL.format(101)]
    assert flag(create, "--agent") == "claude"
    assert flag(create, "--surface") == "terminal"
    assert flag(create, "--model") == "opus"
    assert flag(create, "--effort") == "high"
    assert flag(create, "--prompt").startswith(f"Let's start working on {URL.format(101)}.\n")
    assert "--no-parent" in create and "--json" in create
    assert "--settings" not in create
    shell = next(c for c in calls if c[:3] == ["orca", "terminal", "create"])
    assert flag(shell, "--worktree") == "id:r1::/wt/new"
    assert flag(shell, "--title") == "shell"
    assert [c[3] for c in calls if c[:3] == ["gh", "issue", "edit"]] == ["101"]
    assert proc.stdout.startswith("Launched issue #101\n")
    assert "Workspace: #101 Notional issue title\n" in proc.stdout
    assert "Agent:" not in proc.stdout
    assert "Model:     opus (high effort)\n" in proc.stdout


def test_orca_links_every_issue_of_a_shared_session(harness):
    proc, calls = harness.run(
        "101", "102", issues={"101": OPEN, "102": OPEN}, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    create = orca_create(calls)
    assert flags(create, "--reference") == [URL.format(101), URL.format(102)]
    assert flag(create, "--branch") == "issue/101_102-notional-issue-title"
    assert flag(create, "--name") == "#101 #102 Notional issue title"
    assert proc.stdout.startswith("Launched issues #101 #102\n")


def test_orca_follows_the_configured_model_and_flags(harness):
    harness.write_config(harness.repo_config, {"coordinator": {"model": "fable"}})
    proc, calls = harness.run(
        "101", "--effort", "xhigh", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    create = orca_create(calls)
    assert (flag(create, "--model"), flag(create, "--effort")) == ("fable", "xhigh")
    assert "Use Fable 5.2 as the parent coordinator." in flag(create, "--prompt")


@pytest.mark.parametrize(
    "branch",
    ["refs/heads/issue/102-an-old-title", "refs/heads/issue/101_102-x", "issue/102-bare"],
)
def test_orca_refuses_an_issue_that_already_has_a_worktree(harness, branch):
    worktrees = [{"branch": branch, "path": "/wt/existing", "displayName": "#102 Old"}]
    # Git lists the same worktree; Orca's entry, with its workspace, is the one shown.
    git_worktrees = [
        {"path": str(harness.repo), "branch": "refs/heads/main"},
        {"path": "/wt/existing", "branch": "refs/heads/" + branch.removeprefix("refs/heads/")},
    ]
    proc, calls = harness.run(
        "102",
        issues={"102": OPEN},
        worktrees=worktrees,
        git_worktrees=git_worktrees,
        orca_up=True,
        host_env=IN_ORCA,
    )
    assert proc.returncode == 1
    assert "Issue #102 already has a worktree." in proc.stderr
    assert "Workspace: #102 Old" in proc.stderr
    assert "Agent:" not in proc.stderr
    assert "Switch to workspace #102 Old in Orca to see it." in proc.stderr
    assert launched_nothing(calls)


@pytest.mark.parametrize(
    ("backend", "host_env"), [("Herdr", IN_HERDR), ("Orca", IN_ORCA)]
)
def test_a_worktree_only_git_lists_still_holds_its_issue(harness, backend, host_env):
    # Orca lists only the worktrees it made, so one made in Herdr (or by hand)
    # is known only to git.
    git_worktrees = [
        {"path": str(harness.repo), "branch": "refs/heads/main"},
        {"path": "/wt/detached"},
        {"path": "/wt/elsewhere", "branch": "refs/heads/issue/102-made-elsewhere"},
    ]
    proc, calls = harness.run(
        "102",
        issues={"102": OPEN},
        git_worktrees=git_worktrees,
        orca_up=True,
        host_env=host_env,
    )
    assert proc.returncode == 1
    assert "Issue #102 already has a worktree." in proc.stderr
    assert f"Workspace: (not made in {backend})" in proc.stderr
    assert "Worktree:  /wt/elsewhere" in proc.stderr
    assert f"It was made outside {backend}; see it where it was made." in proc.stderr
    assert "Agent:" not in proc.stderr
    assert "herdr worktree open" not in proc.stderr
    assert launched_nothing(calls)


def test_orca_names_the_repository_by_its_main_checkout(harness):
    # Launched from a linked worktree, whose path Orca does not know.
    git_worktrees = [
        {"path": "/src/main-checkout", "branch": "refs/heads/main"},
        {"path": str(harness.repo), "branch": "refs/heads/some-feature"},
    ]
    proc, calls = harness.run(
        "101", issues={"101": OPEN}, git_worktrees=git_worktrees, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    listed = next(c for c in calls if c[:3] == ["orca", "worktree", "list"])
    assert flag(listed, "--repo") == "path:/src/main-checkout"
    assert flag(orca_create(calls), "--repo") == "path:/src/main-checkout"


def test_orca_other_branches_do_not_hold_the_issue(harness):
    worktrees = [
        {"branch": "refs/heads/main", "path": "/repo", "displayName": "main"},
        {"branch": "refs/heads/issue/1020-x", "path": "/wt/other", "displayName": "x"},
        {"path": "/wt/detached"},
    ]
    proc, calls = harness.run(
        "102", issues={"102": OPEN}, worktrees=worktrees, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert orca_create(calls)


def test_orca_cli_is_found_in_its_config_dir_shim(harness):
    # Orca's Linux CLI is a shim under its config dir, often not on PATH.
    proc, calls = harness.run(
        "101", "--backend", "orca", issues={"101": OPEN}, orca_up=True, orca_on_path=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert orca_create(calls)


def test_a_missing_orca_cli_is_named_as_such(harness):
    (harness.bin / "orca").unlink()
    proc, calls = harness.run(
        "101", "--backend", "orca", issues={"101": OPEN}, orca_up=True, orca_on_path=False
    )
    assert proc.returncode == 1
    assert "The orca command was not found" in proc.stderr
    assert launched_nothing(calls)


def test_orca_warnings_do_not_stop_a_launch_and_its_branch_is_reported(harness):
    proc, calls = harness.run(
        "101",
        issues={"101": OPEN},
        orca_up=True,
        host_env=IN_ORCA,
        stub_env={"STUB_ORCA_WARN": "1", "STUB_ORCA_BRANCH_TAKEN": "1"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "warning: something on the side" in proc.stderr
    assert "Branch:    issue/101-notional-issue-title-2\n" in proc.stdout
    assert [c[3] for c in calls if c[:3] == ["gh", "issue", "edit"]] == ["101"]


def test_orca_launches_haiku_without_an_effort(harness):
    harness.write_config(harness.repo_config, {"coordinator": {"model": "haiku"}})
    proc, calls = harness.run("101", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--effort" not in orca_create(calls)
    assert "Model:     haiku\n" in proc.stdout


def test_orca_must_know_the_repository(harness):
    proc, calls = harness.run(
        "101", issues={"101": OPEN}, orca_up=True, orca_repo_known=False, host_env=IN_ORCA
    )
    assert proc.returncode == 1
    assert f"Orca does not know the repository at {harness.repo}." in proc.stderr
    assert launched_nothing(calls)


def test_orca_refuses_ultracode(harness):
    proc, calls = harness.run(
        "101", "--ultracode", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 1
    assert "--ultracode is not available in Orca" in proc.stderr
    assert "Nothing was launched." in proc.stderr
    assert launched_nothing(calls)
    assert not (harness.repo / ".git" / "issue-herd").exists()


def test_orca_refuses_plain_only_while_superpowers_is_enabled(harness):
    harness.enable_plugins({SUPERPOWERS: True})
    proc, calls = harness.run(
        "101", "--plain", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 1
    assert "--plain is not available in Orca while a Superpowers plugin is enabled" in proc.stderr
    assert launched_nothing(calls)
    # Without the plugin, or with it listed as disabled, plain needs no
    # settings and launches.
    for plugins in ({}, {SUPERPOWERS: False}):
        harness.enable_plugins(plugins)
        harness.log.unlink()
        proc, calls = harness.run(
            "102", "--plain", issues={"102": OPEN}, orca_up=True, host_env=IN_ORCA
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Mode:      plain\n" in proc.stdout
        assert orca_create(calls)
        assert not (harness.repo / ".git" / "issue-herd").exists()
    # Enabled for the user and disabled only in the gitignored local file,
    # which the new worktree does not have: the session would get the plugin.
    harness.enable_plugins({SUPERPOWERS: True})
    harness.write_config(
        harness.repo / ".claude" / "settings.local.json",
        {"enabledPlugins": {SUPERPOWERS: False}},
    )
    harness.log.unlink()
    proc, calls = harness.run(
        "102", "--plain", issues={"102": OPEN}, orca_up=True, host_env=IN_ORCA
    )
    assert proc.returncode == 1
    assert launched_nothing(calls)


def test_orca_runs_superpowers_when_the_plugin_is_enabled(harness):
    harness.enable_plugins({SUPERPOWERS: True})
    proc, calls = harness.run("101", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Mode:      superpowers (Superpowers plugin detected;" in proc.stdout
    assert "normal Superpowers\nworkflow" in flag(orca_create(calls), "--prompt")


def test_orca_template_mistakes_create_nothing(harness):
    use_template(harness, "{{#bogus}}\nx\n{{/bogus}}\n")
    proc, calls = harness.run("101", issues={"101": OPEN}, orca_up=True, host_env=IN_ORCA)
    assert proc.returncode == 1
    assert launched_nothing(calls)
