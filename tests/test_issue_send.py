"""issue-send tests: messaging a running issue session in Herdr or Orca.

The real script runs with the same stub tools as the launcher tests (see
test_issue_herd.py). Git lists the issue worktrees; Herdr answers `agent list`
from STUB_AGENTS and Orca `terminal list` from STUB_ORCA_TERMINALS, so a test
chooses which host has the session.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from test_issue_herd import PLUGIN_ROOT, Harness, harness  # noqa: F401

SEND = PLUGIN_ROOT / "scripts" / "issue-send"

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("jq") is None, reason="issue-send needs jq"
)

ISSUE_101 = [{"path": "/wt/101", "branch": "refs/heads/issue/101-notional-title"}]
SHARED = [{"path": "/wt/shared", "branch": "refs/heads/issue/101_102-notional-title"}]
AGENT_101 = [{"name": "issue-101", "agent_status": "idle"}]
ORCA_TERMINALS = [
    {"handle": "term_shell", "agentIdentity": None, "connected": True, "writable": True},
    {"handle": "term_claude", "agentIdentity": "claude", "connected": True, "writable": True},
]


def send(harness: Harness, *args: str, git_worktrees=None, **kwargs):
    worktrees = [{"path": str(harness.repo), "branch": "refs/heads/main"}]
    worktrees += git_worktrees if git_worktrees is not None else ISSUE_101
    return harness.run(*args, issues={}, git_worktrees=worktrees, script=SEND, **kwargs)


def prompts(calls: list) -> list[list[str]]:
    return [c[3:] for c in calls if c[:3] == ["herdr", "agent", "prompt"]]


def orca_sends(calls: list) -> list[str]:
    """The terminal handle of each Orca send."""
    return [c[c.index("--terminal") + 1] for c in calls if c[:3] == ["orca", "terminal", "send"]]


def nothing_sent(calls: list) -> bool:
    return not prompts(calls) and not orca_sends(calls)


def test_the_send_skill_runs_the_messenger_script():
    skill = (PLUGIN_ROOT / "skills" / "send" / "SKILL.md").read_text()
    names = re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([^"\s]+)', skill)
    assert names == [SEND.name]
    assert os.access(SEND, os.X_OK)


def test_in_herdr_the_message_goes_to_the_issues_agent(harness):
    proc, calls = send(
        harness, "101", "Hello", "there", stub_env={"STUB_AGENTS": json.dumps(AGENT_101)}
    )
    assert proc.returncode == 0, proc.stderr
    assert prompts(calls) == [["issue-101", "Hello there"]]
    assert proc.stdout == "Sent to issue #101: Herdr agent issue-101 (idle).\n"


def test_the_skills_single_string_form_splits_the_number_off(harness):
    proc, calls = send(
        harness, "#101  Hello there ", stub_env={"STUB_AGENTS": json.dumps(AGENT_101)}
    )
    assert proc.returncode == 0, proc.stderr
    assert prompts(calls) == [["issue-101", "Hello there"]]


def test_any_issue_of_a_shared_session_reaches_it(harness):
    agents = [{"name": "issue-101_102", "agent_status": "working"}]
    proc, calls = send(
        harness, "102", "Hello", git_worktrees=SHARED,
        stub_env={"STUB_AGENTS": json.dumps(agents)},
    )
    assert proc.returncode == 0, proc.stderr
    assert prompts(calls) == [["issue-101_102", "Hello"]]


def test_with_no_text_the_message_is_read_from_stdin(harness):
    proc, calls = send(
        harness, "101", stdin="Line one.\nLine two.\n\n",
        stub_env={"STUB_AGENTS": json.dumps(AGENT_101)},
    )
    assert proc.returncode == 0, proc.stderr
    assert prompts(calls) == [["issue-101", "Line one.\nLine two."]]


def test_an_empty_message_sends_nothing(harness):
    proc, calls = send(harness, "101", stdin="", stub_env={"STUB_AGENTS": json.dumps(AGENT_101)})
    assert proc.returncode == 2
    assert "The message is empty." in proc.stderr
    assert nothing_sent(calls)


@pytest.mark.parametrize("args", [(), ("--backend", "nope", "101", "hi"), ("abc", "hi"), ("--backend",)])
def test_bad_arguments_send_nothing(harness, args):
    proc, calls = send(harness, *args, stub_env={"STUB_AGENTS": json.dumps(AGENT_101)})
    assert proc.returncode == 2
    assert "usage: issue-send" in proc.stderr
    assert nothing_sent(calls)


def test_in_orca_the_message_goes_to_the_claude_terminal_of_the_worktree(harness):
    proc, calls = send(
        harness, "101", "Hello there", herdr_up=False, orca_up=True,
        stub_env={"STUB_ORCA_TERMINALS": json.dumps(ORCA_TERMINALS)},
    )
    assert proc.returncode == 0, proc.stderr
    lists = [c for c in calls if c[:3] == ["orca", "terminal", "list"]]
    assert lists == [["orca", "terminal", "list", "--worktree", "path:/wt/101", "--json"]]
    assert [c for c in calls if c[:3] == ["orca", "terminal", "send"]] == [[
        "orca", "terminal", "send", "--terminal", "term_claude", "--text", "Hello there",
        "--enter", "--wait-submit", "10", "--json",
    ]]
    assert proc.stdout == (
        "Sent to issue #101: Orca terminal term_claude (the session took it up).\n"
    )


def test_a_busy_orca_session_is_reported_as_queued(harness):
    proc, calls = send(
        harness, "101", "Hello", herdr_up=False, orca_up=True,
        stub_env={
            "STUB_ORCA_TERMINALS": json.dumps(ORCA_TERMINALS),
            "STUB_ORCA_STAGES": json.dumps(["input_accepted"]),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert orca_sends(calls)
    assert "(queued; the session takes it up when its turn ends)" in proc.stdout


def test_with_both_hosts_up_the_one_with_the_session_gets_it(harness):
    # Herdr knows no agent for the issue; Orca has its terminal.
    proc, calls = send(
        harness, "101", "Hello", herdr_up=True, orca_up=True,
        stub_env={"STUB_ORCA_TERMINALS": json.dumps(ORCA_TERMINALS)},
    )
    assert proc.returncode == 0, proc.stderr
    assert not prompts(calls)
    assert orca_sends(calls) == ["term_claude"]


def test_backend_looks_in_one_host_only(harness):
    proc, calls = send(
        harness, "--backend", "herdr", "101", "Hello", herdr_up=True, orca_up=True,
        stub_env={"STUB_ORCA_TERMINALS": json.dumps(ORCA_TERMINALS)},
    )
    assert proc.returncode == 1
    assert "Herdr has no agent named issue-101." in proc.stderr
    assert nothing_sent(calls)
    assert not [c for c in calls if c[0] == "orca"]


def test_an_issue_without_a_worktree_sends_nothing(harness):
    proc, calls = send(harness, "7777", "Hello", stub_env={"STUB_AGENTS": json.dumps(AGENT_101)})
    assert proc.returncode == 1
    assert "Issue #7777 has no worktree. Launch it with: /issue-herd:issue 7777" in proc.stderr
    assert nothing_sent(calls)


def test_a_session_running_nowhere_is_reported_per_host(harness):
    proc, calls = send(harness, "101", "Hello", herdr_up=True, orca_up=True)
    assert proc.returncode == 1
    assert "Issue #101's session is not running, so nothing was sent." in proc.stderr
    assert "Herdr has no agent named issue-101." in proc.stderr
    assert "Orca has no Claude terminal in the workspace at /wt/101." in proc.stderr
    assert "Branch:   issue/101-notional-title" in proc.stderr
    assert nothing_sent(calls)


def test_no_host_running_sends_nothing(harness):
    proc, calls = send(harness, "101", "Hello", herdr_up=False, orca_up=False)
    assert proc.returncode == 1
    assert "Neither Herdr nor Orca is running" in proc.stderr
    assert nothing_sent(calls)


def test_a_blocked_herdr_agent_refuses_the_message(harness):
    proc, calls = send(
        harness, "101", "Hello",
        stub_env={"STUB_AGENTS": json.dumps([{"name": "issue-101", "agent_status": "blocked"}]),
                  "STUB_HERDR_BLOCKED": "1"},
    )
    assert proc.returncode == 1
    assert "agent_blocked" in proc.stderr
    assert "Herdr did not take the message for issue-101 (blocked)." in proc.stderr
