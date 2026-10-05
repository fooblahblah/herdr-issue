# Several Issues, One PR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/herdr-issue:issue 12 15 17` launches one worktree, one Claude Code session and one PR that fixes all three issues; one issue launches exactly as today.

**Architecture:** The launcher (`plugins/herdr-issue/scripts/herdr-issue`, bash) loses its per-issue fan-out and handles an array of issues in one pass: validate all, then create one worktree whose names carry every number joined by `_`. The prompt template stays rendered by the launcher's own awk program, extended with nesting, inverted `{{^name}}` sections, a `multi` condition and an `{{ISSUES}}` variable, with literal substitution from `ENVIRON`.

**Tech Stack:** bash, POSIX awk, jq, pytest (tests drive the real script with stub `git`/`gh`/`herdr` on `PATH`).

**Spec:** `docs/superpowers/specs/2026-10-05-multi-issue-single-pr-design.md`

## Global Constraints

- No new runtime dependency: the launcher uses only bash, awk, jq, git, gh, herdr.
- The awk program must stay POSIX (it must run under `awk --posix`): no gawk extensions.
- One issue: branch `issue/<N>-<slug>`, agent `issue-<N>`, settings `issue-<N>.settings.json`, label `#<N> <title>`, summary first line `Launched issue #<N>` — all unchanged.
- Several issues: branch `issue/12_15_17-<slug of first title>`, agent `issue-12_15_17`, settings `issue-12_15_17.settings.json`, label `#12 #15 #17 <first title>`. Order is the order given, duplicates dropped.
- Section markers occupy a whole line; known names are exactly `superpowers`, `plain`, `ultracode`, `multi`.
- Run the tests with: `uvx pytest tests -q` (pytest is not installed globally). Baseline: 24 passed.
- POSIX awk check: `mkdir -p /tmp/posix-awk && printf '#!/bin/sh\nexec gawk --posix "$@"\n' > /tmp/posix-awk/awk && chmod +x /tmp/posix-awk/awk`, then `PATH=/tmp/posix-awk:$PATH uvx pytest tests -q` must also pass.
- Line numbers in this plan refer to the files as they are before Task 1. Locate each block by the content quoted with it; earlier edits shift the numbers.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BMNghpGy94vrG1un9cy7pe
  ```

---

### Task 1: Template engine — nesting, inverted sections, `{{ISSUES}}`, literal substitution

The launcher still fans out per issue during this task, so every render here is single-issue; `multi` is wired in but only becomes true in Task 2.

**Files:**
- Modify: `plugins/herdr-issue/scripts/herdr-issue:154` (add `urls` array) and `:161-202` (template comment, renderer)
- Modify: `plugins/herdr-issue/prompts/autonomous-issue.md:1`
- Test: `tests/test_herdr_issue.py`

**Interfaces:**
- Consumes: `$mode`, `$issue_url`, `$base`, `$template`, `issues` array (existing script variables).
- Produces: `urls` (bash array of issue URLs, Task 2 fills it per issue), `multi` (`""` or `1`), `issues_text` (English list of `urls`), and an awk renderer reading env vars `MODE`, `MULTI`, `ISSUES`, `ISSUE_URL`, `DEFAULT_BRANCH`. Task 2 relies on `multi` being set before the prompt is built and on the renderer failing (exit 1, message containing `use {{ISSUES}}`) when `MULTI` is set and an emitted line contains `{{ISSUE_URL}}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_herdr_issue.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest tests -q -k "nest or mistakes or literally or opens_with"`
Expected: `test_sections_nest_and_invert` FAILS (current awk rejects a section opened inside another, and `^` sections), `test_template_mistakes_launch_nothing` FAILS for the `{{^multi}}` case, `test_values_are_substituted_literally` FAILS (sed error), `test_plugin_prompt_opens_with_the_issue` PASSES already (it guards the prompt edit below).

- [ ] **Step 3: Add the `urls` array**

In `plugins/herdr-issue/scripts/herdr-issue`, directly after the line `issue_url="$(jq -r '.url' <<<"$issue_json")"` (line 154), add:

```bash
urls=("$issue_url")
```

- [ ] **Step 4: Replace the renderer**

Replace the comment above `template=` (originally lines 161-165, starting `# Build the prompt before creating anything`) with:

```bash
# Build the prompt before creating anything, so a template mistake leaves no
# worktree behind. Section lines nest: {{#name}} keeps the lines up to its
# {{/name}} when name is active, and {{^name}} keeps them when it is not. The
# active names are the mode and, when several issues are launched, multi.
# A repository can carry its own template at
# .claude/prompts/autonomous-issue.md; otherwise the plugin's is used.
```

Replace the old renderer (originally lines 177-202: from `prompt="$(` through the `)"` that closes it, i.e. the awk program and the `sed` line) with:

```bash
multi=""
if [[ ${#issues[@]} -gt 1 ]]; then
    multi=1
fi

# The issue URLs as one phrase for {{ISSUES}}: "A", "A and B", "A, B and C".
issues_text="${urls[0]}"
for ((i = 1; i < ${#urls[@]}; i++)); do
    if ((i == ${#urls[@]} - 1)); then
        issues_text+=" and ${urls[i]}"
    else
        issues_text+=", ${urls[i]}"
    fi
done

# Values reach awk through the environment and are replaced literally, so no
# value is ever read as a pattern or an escape.
prompt="$(
    MODE="$mode" MULTI="$multi" ISSUES="$issues_text" ISSUE_URL="${urls[0]}" \
        DEFAULT_BRANCH="$base" awk '
        function fail(msg) {
            printf "%s line %d: %s\n", FILENAME, FNR, msg > "/dev/stderr"
            bad = 1
            exit 1
        }
        function replace(s, from, to,    out, i) {
            out = ""
            while ((i = index(s, from)) > 0) {
                out = out substr(s, 1, i - 1) to
                s = substr(s, i + length(from))
            }
            return out s
        }
        BEGIN {
            split("superpowers plain ultracode multi", names, " ")
            for (i in names) known[names[i]] = 1
            active[ENVIRON["MODE"]] = 1
            if (ENVIRON["MULTI"] != "") active["multi"] = 1
        }
        $0 ~ "^[{][{][#^/][a-z]+[}][}]$" {
            sigil = substr($0, 3, 1)
            name = substr($0, 4, length($0) - 5)
            if (!(name in known)) fail("unknown section " $0)
            if (sigil == "/") {
                if (depth == 0 || sec_name[depth] != name) fail("unmatched " $0)
                if (!sec_keep[depth]) hidden--
                depth--
            } else {
                depth++
                sec_name[depth] = name
                sec_sigil[depth] = sigil
                sec_keep[depth] = ((name in active) == (sigil == "#"))
                if (!sec_keep[depth]) hidden++
            }
            next
        }
        hidden == 0 {
            if (ENVIRON["MULTI"] != "" && index($0, "{{ISSUE_URL}}"))
                fail("{{ISSUE_URL}} names a single issue; use {{ISSUES}} to launch several")
            line = replace($0, "{{ISSUES}}", ENVIRON["ISSUES"])
            line = replace(line, "{{ISSUE_URL}}", ENVIRON["ISSUE_URL"])
            print replace(line, "{{DEFAULT_BRANCH}}", ENVIRON["DEFAULT_BRANCH"])
        }
        END {
            if (bad) exit 1
            if (depth > 0) fail("{{" sec_sigil[depth] sec_name[depth] "}} is never closed")
        }
    ' "$template"
)"
```

- [ ] **Step 5: Switch the plugin prompt to `{{ISSUES}}`**

In `plugins/herdr-issue/prompts/autonomous-issue.md`, change line 1 from
`Let's start working on {{ISSUE_URL}}.` to:

```
Let's start working on {{ISSUES}}.
```

- [ ] **Step 6: Run the whole suite**

Run: `uvx pytest tests -q`
Expected: all pass (24 existing + 8 new = 32 passed).

Then run the POSIX awk check from Global Constraints.
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add plugins/herdr-issue/scripts/herdr-issue plugins/herdr-issue/prompts/autonomous-issue.md tests/test_herdr_issue.py
git commit -m "Template sections nest and invert, and values substitute literally

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BMNghpGy94vrG1un9cy7pe"
```

---

### Task 2: Launcher — several issues share one worktree, session and PR

**Files:**
- Modify: `plugins/herdr-issue/scripts/herdr-issue` (fan-out block `:40-61`, existing-worktree check `:77-115`, issue metadata `:146-159`, settings path `:240`, naming `:250-261`, fetch comment `:263`, agent name `:296`, assignment `:323-328`, summary `:330-340`)
- Test: `tests/test_herdr_issue.py`

**Interfaces:**
- Consumes from Task 1: `multi`, `urls`, `issues_text`, the renderer's `{{ISSUE_URL}}`-with-`multi` failure.
- Produces: `titles` and `urls` arrays (one entry per issue, in order), `ids` (`12_15_17`), `numbers` (`#12 #15 #17`). Task 3 relies on a multi-issue launch rendering the plugin prompt with `MULTI=1`.

- [ ] **Step 1: Let the stub report worktrees**

In `tests/test_herdr_issue.py`, in `STUB`, replace

```python
    if args[:2] == ["worktree", "list"]:
        print(json.dumps({"result": {"worktrees": []}}))
```

with

```python
    if args[:2] == ["worktree", "list"]:
        worktrees = json.loads(os.environ.get("STUB_WORKTREES", "[]"))
        print(json.dumps({"result": {"worktrees": worktrees}}))
```

and change `Harness.run` to take and pass worktrees:

```python
    def run(
        self, *args: str, issues: dict, origin_head: str = "", worktrees: list | None = None
    ) -> tuple[subprocess.CompletedProcess, list]:
```

adding to its `env` dict:

```python
            "STUB_WORKTREES": json.dumps(worktrees or []),
```

- [ ] **Step 2: Replace the fan-out tests with failing multi-issue tests**

Delete `test_two_open_issues_both_launch`, `test_closed_issue_is_skipped_and_the_rest_launch`, `test_single_string_form_splits_and_applies_ultracode_to_each`, `test_repeated_number_launches_once` and `test_several_issues_pass_the_plain_flag_to_each`. Append:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uvx pytest tests -q`
Expected: every new test FAILS except `test_other_branches_do_not_hold_the_issue`, which already passes (the old `issue/<N>-` prefix match also ignores those branches) and guards the new matcher. The fan-out launches each issue separately with its own `issue-<N>` agent, prints child output to stdout rather than stderr, and misses `issue/101_102-…` entirely. All other existing tests pass.

- [ ] **Step 4: Remove the fan-out**

In `plugins/herdr-issue/scripts/herdr-issue`, delete lines 40-61 (the comment `# Several issues: launch each through the single-issue path...` through the `fi` closing that block) and line 63 (`issue="${issues[0]}"`).

- [ ] **Step 5: Validate every issue before creating anything**

Replace the whole existing-worktree block (from `# Refuse to start a second session for an issue that already has a worktree.` through its closing `fi`, old lines 77-115) with:

```bash
# Check every issue before creating anything, and report every problem: the
# issues share one session, so one that cannot launch holds back the rest.
worktrees="$(herdr worktree list --cwd "$repo")"
titles=()
urls=()
failed=""
for n in "${issues[@]}"; do
    # Refuse an issue that already has a worktree, alone or with others. A
    # branch names its issues as issue/<N>[_<N>...]-<slug>; match on the
    # numbers rather than the full name, so a retitled issue is still caught.
    existing="$(
        jq -c --arg n "$n" '
            def issue_numbers:
                (.branch // "")
                | (capture("^issue/(?<n>[0-9]+(_[0-9]+)*)-") | .n | split("_")) // [];
            first(
                .result.worktrees[]
                | select(any(issue_numbers[]; . == $n))
                | . + {agent: ("issue-" + (issue_numbers | join("_")))}
            ) // empty
        ' <<<"$worktrees"
    )"

    if [[ -n "$existing" ]]; then
        wt_branch="$(jq -r '.branch' <<<"$existing")"
        wt_path="$(jq -r '.path' <<<"$existing")"
        wt_workspace="$(jq -r '.open_workspace_id // empty' <<<"$existing")"
        wt_agent="$(jq -r '.agent' <<<"$existing")"
        agent_status="$(
            herdr agent list |
                jq -r --arg name "$wt_agent" \
                    'first(.result.agents[] | select(.name == $name) | .agent_status) // empty'
        )"

        {
            if [[ -n "$agent_status" ]]; then
                echo "Issue #${n} is already running. No new session was started."
            else
                echo "Issue #${n} already has a worktree. No new session was started."
            fi
            echo
            echo "Workspace: ${wt_workspace:-(not open in Herdr)}"
            echo "Agent:     ${wt_agent} (${agent_status:-not running})"
            echo "Branch:    ${wt_branch}"
            echo "Worktree:  ${wt_path}"
            echo
            if [[ -n "$wt_workspace" ]]; then
                echo "Switch to workspace ${wt_workspace} in Herdr to see it."
            else
                echo "Reopen it with: herdr worktree open --cwd \"${repo}\" --path \"${wt_path}\""
            fi
            echo
        } >&2
        failed=1
        continue
    fi

    # Get authoritative issue metadata; gh explains its own failures.
    if ! issue_json="$(gh issue view "$n" --json number,title,state,url)"; then
        failed=1
        continue
    fi

    state="$(jq -r '.state' <<<"$issue_json")"
    if [[ "$state" != "OPEN" ]]; then
        echo "Issue #$n is not open ($state)." >&2
        failed=1
        continue
    fi

    titles+=("$(jq -r '.title' <<<"$issue_json")")
    urls+=("$(jq -r '.url' <<<"$issue_json")")
done

if [[ -n "$failed" ]]; then
    if [[ ${#issues[@]} -gt 1 ]]; then
        echo "Nothing was launched: these issues launch together or not at all." >&2
    fi
    exit 1
fi

# One issue keeps issue/12-<slug> and issue-12; several are joined by "_"
# (issue/12_15_17-<slug>), which never appears in a slug.
ids="${issues[*]}"
ids="${ids// /_}"
numbers="$(printf '#%s ' "${issues[@]}")"
numbers="${numbers% }"
```

- [ ] **Step 6: Remove the old single-issue metadata block**

Delete the block that starts `# Get authoritative issue metadata.` (old lines 146-159: the `gh issue view "$issue"` call, the `title`/`state`/`issue_url` assignments and the `OPEN` check) together with the `urls=("$issue_url")` line Task 1 added after it. `titles` and `urls` now come from the loop in Step 5.

- [ ] **Step 7: Rename the per-launch names**

Make these replacements in the script:

- `session_settings="${settings_dir}/issue-${issue}.settings.json"` → `session_settings="${settings_dir}/issue-${ids}.settings.json"`
- In the slug pipeline, `printf '%s' "$title" |` → `printf '%s' "${titles[0]}" |`
- `branch="issue/${issue}-${slug}"` → `branch="issue/${ids}-${slug}"`
- `label="#${issue} ${title}"` → `label="${numbers} ${titles[0]}"`
- `# Every issue begins from the current remote default branch, independently.` → `# The worktree begins from the current remote default branch.`
- `agent_name="issue-${issue}"` → `agent_name="issue-${ids}"`

- [ ] **Step 8: Assign every issue and list every URL in the summary**

Replace the assignment block (from `# Mark the issue as being worked on,` to the end of the file) with:

```bash
# Mark the issues as being worked on, only once a session is actually running.
# "@me" resolves to whoever gh is logged in as, so each launcher assigns
# themselves. The session is already up, so a failure here only warns.
for n in "${issues[@]}"; do
    if ! gh issue edit "$n" --add-assignee @me >/dev/null; then
        echo "Warning: could not assign issue #${n} to you; assign it by hand." >&2
    fi
done

cat <<EOF
Launched issue${multi:+s} ${numbers}

Workspace: ${workspace_id}
Agent:     ${agent_name}
Branch:    ${branch}
Mode:      ${mode}${mode_note}
$(printf 'Issue:     %s\n' "${urls[@]}")

The Claude session is running independently in Herdr.
EOF
```

- [ ] **Step 9: Check no stale names remain**

Run: `grep -nE '\$\{?(issue|title|issue_url)\b' plugins/herdr-issue/scripts/herdr-issue`
Expected: no output (every use of the old single-issue variables is gone).

- [ ] **Step 10: Run the whole suite**

Run: `uvx pytest tests -q`, then the POSIX awk check from Global Constraints.
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add plugins/herdr-issue/scripts/herdr-issue tests/test_herdr_issue.py
git commit -m "Launch several issues together in one worktree and session

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BMNghpGy94vrG1un9cy7pe"
```

---

### Task 3: Prompt, skill and README for one shared PR

**Files:**
- Modify: `plugins/herdr-issue/prompts/autonomous-issue.md`
- Modify: `plugins/herdr-issue/skills/issue/SKILL.md`
- Modify: `README.md`
- Test: `tests/test_herdr_issue.py`

**Interfaces:**
- Consumes from Task 2: a multi-issue launch renders the plugin prompt with `multi` active.
- Produces: nothing later tasks use.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_herdr_issue.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest tests -q -k "share_one_pr or not_told"`
Expected: `test_several_issues_are_told_they_share_one_pr` FAILS (no multi paragraph yet); `test_one_issue_is_not_told_about_several` PASSES (it guards against a stray blank line from the new section).

- [ ] **Step 3: Add the multi paragraph and reword the closing lines**

In `plugins/herdr-issue/prompts/autonomous-issue.md`, replace the first two lines

```
Let's start working on {{ISSUES}}.

```

with (the blank line sits inside the section so a single issue gets exactly one):

```
Let's start working on {{ISSUES}}.
{{#multi}}

These issues are fixed together, on one branch and in one draft PR. Wherever
this prompt says "the issue", it means each of them: every issue's
requirements and acceptance criteria must be met. The PR description carries
one `Closes #N` line per issue. If one of them turns out to be separate work
that cannot reasonably share this PR, stop and say so rather than dropping or
splitting it off yourself.
{{/multi}}

```

Then make these two count-neutral edits:

- Replace the three lines

  ```
  Create a draft PR early, after the first substantive commit, so the PR serves
  as the active tracking artifact. Create it with `--assignee @me`. Ensure the
  PR closes the referenced issue.
  ```

  with (the test matches the last sentence on one line):

  ```
  Create a draft PR early, after the first substantive commit, so the PR serves
  as the active tracking artifact. Create it with `--assignee @me`.
  Ensure the PR closes every referenced issue.
  ```

- `- verify the PR is merged and the referenced issue is closed.` → `- verify the PR is merged and every referenced issue is closed.`

- [ ] **Step 4: Update the skill**

Replace `plugins/herdr-issue/skills/issue/SKILL.md` frontmatter `description` and the body line:

```markdown
---
name: issue
description: Launch one or more GitHub issues autonomously, together in one isolated Herdr worktree, session and PR (uses Superpowers when it is enabled; --plain runs without it, --ultracode runs an ultracode session)
argument-hint: <issue-number>... [--plain | --ultracode]
disable-model-invocation: true
allowed-tools: Bash
---

Launch GitHub issues $ARGUMENTS together in one Herdr worktree, Claude Code session and PR.
```

(The `!` launcher block below it is unchanged.)

- [ ] **Step 5: Update the README**

In `README.md`:

Replace the opening paragraph

```
A Claude Code plugin that launches GitHub issues autonomously, each in its own
Herdr worktree and Claude Code session.
```

with

```
A Claude Code plugin that launches GitHub issues autonomously in a Herdr
worktree and Claude Code session. Several issues named together are fixed
together, in one worktree, session and PR.
```

Replace the paragraph starting `Each issue gets a worktree on` through `is skipped.` with

```
The issues get one worktree, branched from the remote default branch, with an
`agent` tab running Claude Code and a spare `shell` tab. One issue gets the
branch `issue/<N>-<slug>`; several get `issue/<N>_<N>...-<slug>`, the slug
taken from the first issue's title. The session is given the prompt in
`plugins/herdr-issue/prompts/autonomous-issue.md` and every issue is assigned
to you. If any issue already has a worktree, alone or with others, or is not
open, nothing is launched and every problem is reported.
```

Replace the `## Per-repository prompt` section body with

```
A repository that carries `.claude/prompts/autonomous-issue.md` uses that
template instead of the plugin's. A line `{{#name}}` keeps the lines up to the
matching `{{/name}}` when `name` is active, and `{{^name}}` keeps them when it
is not; sections nest and each marker sits on its own line. The active names
are the mode (`superpowers`, `plain` or `ultracode`) and, when several issues
are launched, `multi`.

`{{ISSUES}}` becomes the issue URLs as one phrase (`A`, `A and B`,
`A, B and C`) and `{{DEFAULT_BRANCH}}` the remote default branch.
`{{ISSUE_URL}}` names a single issue: a template that still uses it outside a
`{{^multi}}` section refuses to launch several issues.
```

- [ ] **Step 6: Run the whole suite**

Run: `uvx pytest tests -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add plugins/herdr-issue/prompts/autonomous-issue.md plugins/herdr-issue/skills/issue/SKILL.md README.md tests/test_herdr_issue.py
git commit -m "Tell a several-issue session it owns one shared PR, and document it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BMNghpGy94vrG1un9cy7pe"
```
