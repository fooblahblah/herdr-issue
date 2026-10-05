# Several issues, one PR

## Goal

`/herdr-issue:issue 12 15 17` launches one worktree, one Claude Code session
and one PR that fixes all three issues. Today it launches one of each per
issue; that fan-out is removed entirely. A single issue launches exactly as it
does today.

## Launcher (`plugins/herdr-issue/scripts/herdr-issue`)

### Parsing and validation

- Argument parsing is unchanged (numbers separated by spaces or commas,
  duplicates dropped, `--plain` / `--ultracode`). The fan-out block that
  re-invokes the script per issue is deleted; every launch goes through one
  path holding an `issues` array.
- Before anything is created, every issue is checked: it must be open, and it
  must not already be part of a worktree. If any check fails, the launch is
  aborted, every problem found is reported (not only the first), and nothing
  is created.

### Naming

For a single issue every name is unchanged. For several, in the order given:

| Thing          | Single issue            | Several issues                        |
|----------------|-------------------------|---------------------------------------|
| Branch         | `issue/12-<slug>`       | `issue/12_15_17-<slug>`               |
| Agent          | `issue-12`              | `issue-12_15_17`                      |
| Settings file  | `issue-12.settings.json`| `issue-12_15_17.settings.json`        |
| Herdr label    | `#12 <title>`           | `#12 #15 #17 <first title>`           |

Herdr caps agent names at 32 characters, so the agent name drops trailing
`_<N>` segments until it fits (one number always fits); the branch and
settings file keep every number. Two launches can only end up with the same
shortened name when they share their first issue, which the worktree check
refuses.

The slug comes from the first issue's title. `_` keeps the number list
unambiguous when a slug itself starts with digits.

### Existing-worktree detection

A worktree's issues are read from its branch with
`^issue/([0-9]+(_[0-9]+)*)-`, split on `_`. Issue N is taken when N is in
that list for any worktree. An old single-issue branch parses as a list of
one, and `/issue 15` notices #15 inside `issue/12_15-…`. The "already
running" check derives the agent name from that branch's list
(`issue-` + numbers joined by `_`, shortened by the same rule).

### After the session starts

- Every issue is assigned with `gh issue edit <N> --add-assignee @me`; each
  failure prints its own warning and does not fail the launch.
- The launch summary lists every issue URL.

## Template engine

The awk program in the launcher stays the engine: no external template
library, only `awk`, which the launcher already uses. Syntax is
Mustache-flavoured but not Mustache: no loops, partials or escaping.

### Sections

Section markers occupy a whole line, as today:

- `{{#name}}` keeps the enclosed lines when `name` is active;
- `{{^name}}` keeps them when `name` is not active;
- `{{/name}}` closes either.

Active names: the mode (`superpowers`, `plain` or `ultracode`), plus `multi`
when more than one issue is launched. Known names are exactly those four.

Sections nest. A line is emitted only when every enclosing section passes. A
close must match the innermost open section.

Errors, reported as `<file> line <N>: <message>` and aborting before anything
is created: an unknown name, a close that does not match the innermost open
section (or has none open), and a section still open at end of file.

### Variables

- `{{ISSUES}}`: the issue URLs as an English list: `A`, `A and B`,
  `A, B and C`.
- `{{ISSUE_URL}}`: the issue URL when one issue is launched. When several are
  launched and `{{ISSUE_URL}}` survives into the rendered prompt, the launch
  aborts before creating anything with: `<template> line <N>: {{ISSUE_URL}}
  names a single issue; use {{ISSUES}} to launch several`. This keeps an older per-repository
  template from silently working only the first issue.
- `{{DEFAULT_BRANCH}}`: unchanged.

Substitution moves from `sed` into awk. Values are passed through `ENVIRON`
and replaced literally (index/substr, not `gsub`), so `&`, `|`, `\` and the
like in values are never interpreted.

The ultracode guard (no "ultracode" in a non-ultracode prompt) is unchanged
and runs on the rendered prompt.

## Prompt (`plugins/herdr-issue/prompts/autonomous-issue.md`)

- Opening line: `Let's start working on {{ISSUES}}.`
- New paragraph directly after it, inside `{{#multi}}`:

  > These issues are fixed together, on one branch and in one draft PR.
  > Wherever this prompt says "the issue", it means each of them: every
  > issue's requirements and acceptance criteria must be met. The PR
  > description carries one `Closes #N` line per issue. If one of them turns
  > out to be separate work that cannot reasonably share this PR, stop and
  > say so rather than dropping or splitting it off yourself.

- Count-neutral rewording, no sections: "Ensure the PR closes the referenced
  issue" becomes "Ensure the PR closes every referenced issue"; "the
  referenced issue is closed" becomes "every referenced issue is closed".
- Everything else, including SCOPE and the mode sections, is unchanged.

## Other files

- `plugins/herdr-issue/skills/issue/SKILL.md`: description and body say the
  issues are launched together in one worktree and PR.
- `README.md`: the opening paragraph and multi-issue example describe one
  shared worktree/PR; the per-repository prompt section documents `multi`,
  `{{^name}}`, nesting, `{{ISSUES}}` and the `{{ISSUE_URL}}` error.

## Tests (`tests/test_herdr_issue.py`)

- Remove the fan-out tests.
- Several issues: one worktree with the `_` branch, the combined agent name
  and label, every issue assigned, the summary lists every URL, and the
  rendered prompt contains the `multi` paragraph and every URL.
- Aborts with nothing created when one issue is closed, or one is already in
  a worktree; detection covered for both a combined branch and an old
  single-issue branch.
- Template: nested sections, inverted sections, mismatched close, unknown
  name, unclosed section, `{{ISSUE_URL}}` with several issues, and literal
  substitution of values containing `&` and `|`.
- Existing single-issue tests pass unchanged.
