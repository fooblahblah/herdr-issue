Let's start working on {{ISSUES}}.
{{#multi}}

These issues are fixed together, on one branch and in one draft PR. Wherever
this prompt says "the issue", it means each of them: every issue's
requirements and acceptance criteria must be met. The PR description carries
one `Closes #N` line per issue. If one of them turns out to be separate work
that cannot reasonably share this PR, stop and say so rather than dropping or
splitting it off yourself.
{{/multi}}

{{#superpowers}}
Work the issue autonomously through completion using the normal Superpowers
workflow.
{{/superpowers}}
{{#plain}}
Work the issue autonomously through completion. No workflow plugin drives
this session: follow the workflow described below.
{{/plain}}
{{#ultracode}}
Work the issue autonomously through completion in ultracode: orchestrate every
substantive phase with the Workflow tool. Do not use Superpowers skills in
this session, even if any are listed.
{{/ultracode}}

Treat the GitHub issue description, reproduction steps, and acceptance
criteria as the primary requirements. Validate the issue's assumptions against
the repository before implementing.

SCOPE

Fix in this PR what you find along the way when it belongs with this change:
- a defect in code this PR touches, or one this change exposes;
- a review finding about this diff, including tests or docs it should carry;
- a small, related fix in the same area that the same tests cover.

Open a new issue only for genuinely separate work: a different subsystem, a
decision that needs the maintainer, or a change too large or too risky to
review together with this one. When in doubt, fix it here. Do not open an
issue only to record that something was not fixed. In your final message,
name every issue you opened and give one line on why it could not go in this
PR.

CUSTOMER-FACING CHANGES

While understanding the issue, decide whether the change is customer-facing:
whether it changes anything a customer can see or do that a person could check
by hand, such as the UI, pages, emails or notifications, public API responses,
or CLI output. Purely backend work with no change a customer could observe
(internal refactors, background processing, data plumbing, build and CI,
tests, developer tooling) is not. When in doubt, treat it as customer-facing.
With several issues, the PR is customer-facing if any of them is. Give the
classification and the reason in one line of the PR description.

For a customer-facing change, verify it yourself the way a customer would
use it: in a browser for UI, by running the commands for CLI output, by
sending the requests for an API. Run the before-the-fix steps while
understanding the issue, before changing any code, and the after-the-fix
steps once the change is reviewed:
- Where the repository has end-to-end browser tests (Playwright, Cypress, or
  similar), write the reproduction as one of them, see it fail before the
  fix, and keep it as the regression test.
- Otherwise, start the app and drive it with headless Playwright (through
  `npx`, using an installed Chromium or Chrome where there is one) from a
  script in your scratchpad directory, not committed. Read the screenshots
  you take, and check the browser console for new errors.
Start the app on a free port, since other sessions may be running theirs, and
stop every server you started when you are done.

Automated verification is conclusive when the before-the-fix run showed the
problem (or the missing feature), the after-the-fix run showed the expected
result for every acceptance criterion a customer could observe, the "Also
check" items behaved, and no new console errors appeared. It is inconclusive
when you could not reproduce the problem, the app or a path would not run
(missing secrets, accounts, data, outside services, or hardware), results were
flaky, or a criterion needs a person's judgment that screenshots cannot
settle, such as look and feel or wording. Never count a weaker check as
conclusive.

Give the PR description a "Verification" section, kept current as the work
changes:
- Setup: how to run the app or reach the path, and any account, data,
  configuration, or feature flag it needs;
- Before the fix: steps that reproduce the issue on `{{DEFAULT_BRANCH}}`, and what goes
  wrong;
- After the fix: the same steps on this branch, and what happens instead;
- Also check: edge cases and nearby screens or flows this change could affect;
- Automated: what you ran, what you saw, and whether it was conclusive; for
  anything inconclusive, why.
Write the steps for a person who has not read the code: concrete URLs,
routes, commands, inputs, and expected results. Put the before-the-fix steps
in the draft PR as soon as you have reproduced the issue, so it can be checked
while the fix is in progress.

For a backend change, the regression test is the reproduction: name it in the
PR description and say that it fails on `{{DEFAULT_BRANCH}}`.

MODEL / SUBAGENT ROUTING

Use Opus 5.5 as the parent coordinator.

{{#superpowers}}
Explicitly choose an appropriate model for every dispatched subagent rather
than allowing subagents to inherit Opus 5.5 simply because it is the parent
model.
{{/superpowers}}
{{#plain}}
Explicitly choose an appropriate model for every dispatched subagent rather
than allowing subagents to inherit Opus 5.5 simply because it is the parent
model.
{{/plain}}
{{#ultracode}}
Explicitly choose an appropriate model for every workflow agent, through the
`model` option of each `agent()` call, and for every subagent you dispatch
directly, rather than letting them inherit Opus 5.5 simply because it is the
parent model.
{{/ultracode}}

Use Haiku 5.5 for:
- transcription tasks, where the plan already contains the complete code to
  write and the work is applying it and running the tests;
- single-file mechanical fixes.
The cheapest models take more turns on multi-step work, so do not use Haiku
5.5 for reviews or for implementing from prose descriptions.

Use Sonnet 5.5 by default for:
- other mechanical implementation tasks;
- localized 1-2 file changes with clear requirements;
- straightforward regression tests;
- repetitive or same-shape edits;
- routine fixes for concrete review findings.

Use Opus 5.5 for:
- architecture and design;
- difficult root-cause analysis or debugging;
- concurrency, state-management, security, persistence, or other subtle work;
- integration work spanning multiple components where judgment is required;
- tasks where a Sonnet implementer discovers hidden complexity;
- reviews requiring substantial judgment;
- the final whole-branch review.

For task-scoped reviews, use the least expensive model that can reliably judge
the diff. Escalate to Opus 5.5 for subtle, cross-cutting, or high-risk work.

TASK GRANULARITY

{{#superpowers}}
Avoid creating an excessive number of tiny implementation tasks and review
cycles.

Batch small, closely related, same-shape changes into one task when they share
the same implementation and review surface and can be tested coherently.

Prefer the smallest number of independently meaningful review gates.
{{/superpowers}}
{{#plain}}
Split the work into the smallest number of independently meaningful tasks.
Batch small, closely related, same-shape changes into one task when they share
the same implementation and review surface and can be tested coherently.

Give each task to one subagent with everything it needs to know, or do it
yourself when it is small. Subagents that edit files must not work in this
worktree at the same time.
{{/plain}}
{{#ultracode}}
Size each workflow to the work. Fan out where the work is genuinely
independent: separate subsystems to read, separate review dimensions, separate
findings to verify. Give small, closely related, same-shape edits to one agent
that can test them coherently, not one agent each.

Agents that edit files or run mutation checks must not share this worktree at
the same time: run those stages one after another, or give each agent
`isolation: 'worktree'`. Reading, reviewing, and verifying can run in
parallel.
{{/ultracode}}

WORKFLOW

{{#superpowers}}
For bounded, well-specified issues, avoid unnecessary architectural ceremony.
If the work is genuinely architectural or reveals substantial hidden
complexity, follow the appropriate Superpowers design and planning workflow.
{{/superpowers}}
{{#plain}}
Work in this order, keeping each step proportionate to the issue:
1. Understand. Read the affected code and reproduce the problem, or confirm
   the gap, before changing anything. Decide whether the change is
   customer-facing.
2. Design. For bounded, well-specified issues, settle the approach in a few
   lines and move on. If the work is genuinely architectural or reveals
   substantial hidden complexity, write a short plan first (the approach, the
   alternatives you rejected, the tasks in order) and put it in the PR
   description.
3. Implement, task by task. Where the change is testable, write the
   regression test first and see it fail for the right reason.
4. Review. Have each task's diff reviewed by a fresh subagent that did not
   write it, against the issue's requirements and for defects. Fix what it
   finds and have the fixes re-reviewed until a round finds nothing new.
{{/plain}}
{{#ultracode}}
Run one workflow per phase and read each result before starting the next:
understand the affected code, design the change, implement it, review it.
For bounded, well-specified issues, keep the design phase short. If the work
is genuinely architectural or reveals substantial hidden complexity, have the
design workflow produce independent approaches and judge them before
committing to one.
{{/ultracode}}

Create a draft PR early, after the first substantive commit, so the PR serves
as the active tracking artifact. Create it with `--assignee @me`.
Ensure the PR closes every referenced issue.

Continue autonomously through:
- implementation and appropriate regression tests;
{{#superpowers}}
- Superpowers task review and fix/re-review cycles;
{{/superpowers}}
{{#plain}}
- task review and fix/re-review cycles;
{{/plain}}
{{#ultracode}}
- review workflows over the diff that adversarially verify each finding before
  acting on it, then fix and re-review until a round finds nothing new;
{{/ultracode}}
{{#superpowers}}
- final whole-branch review with the `herdr-issue:final-reviewer` agent (Opus 5.5 at xhigh
  effort; this session runs at high);
{{/superpowers}}
{{#plain}}
- final whole-branch review with the `herdr-issue:final-reviewer` agent (Opus 5.5 at xhigh
  effort; this session runs at high);
{{/plain}}
{{#ultracode}}
- final whole-branch review using Opus 5.5;
{{/ultracode}}
- fresh final verification, including the after-the-fix automated
  verification of a customer-facing change.

Own the PR through completion:
- push meaningful progress;
- monitor required CI/status checks;
- fix CI failures caused by this change;
- keep the branch current with {{DEFAULT_BRANCH}} when necessary;
- resolve merge conflicts if needed;
- rerun appropriate verification after changes;
- never bypass required checks, reviews, branch protection, or merge queues;
- mark the draft PR ready when appropriate;
- merge using the repository's normal strategy once all requirements pass
  and, for a customer-facing change, automated verification was conclusive or
  I have approved it;
- verify the PR is merged and every referenced issue is closed.

If automated verification of a customer-facing change fails, fix the change
and run it again; that is not a reason to stop. Stop before merging only when
it is inconclusive: once checks pass, reviews are done, and the PR is marked
ready, end your turn with what the automated verification covered and what it
saw, what was inconclusive and why, the steps for me to check the rest by
hand, and the PR link, and ask me to verify. When I approve, update from
{{DEFAULT_BRANCH}} if needed, confirm checks still pass, and merge. If I report a problem,
fix it, run the automated verification again, update the steps, and ask
again.

Once the PR is merged, always finish with both of these steps. If the session
ends without a merge, leave the branch and `{{DEFAULT_BRANCH}}` as they are.
- delete the local issue branch. It is checked out in this worktree, so run
  `git fetch origin {{DEFAULT_BRANCH}}` and `git switch --detach origin/{{DEFAULT_BRANCH}}` first, then
  `git branch -D <branch>`. Use `-D`: a squash merge leaves the branch looking
  unmerged.
- sync local `{{DEFAULT_BRANCH}}` with `origin/{{DEFAULT_BRANCH}}`, fast-forward only. Where `{{DEFAULT_BRANCH}}` is
  checked out in another worktree (usually the primary checkout, the first
  entry in `git worktree list`), run `git -C <that path> pull --ff-only origin
  {{DEFAULT_BRANCH}}`, but only when that worktree is on `{{DEFAULT_BRANCH}}` with no uncommitted changes.
  Where `{{DEFAULT_BRANCH}}` is not checked out anywhere, run `git fetch origin {{DEFAULT_BRANCH}}:{{DEFAULT_BRANCH}}`.
  Never stash, reset, or switch branches in another worktree. If `{{DEFAULT_BRANCH}}` could
  not be synced, say so in your final message.

You are authorized to create and push the branch, create and update the PR,
update from {{DEFAULT_BRANCH}}, address CI failures, mark the PR ready, merge once all
normal repository requirements are satisfied (and, for a customer-facing
change, once automated verification was conclusive or I have approved it),
and then delete the local branch and sync local `{{DEFAULT_BRANCH}}` as described above.

Do not ask me to choose implementation, review, model-routing, or execution
strategies. Make those decisions yourself.

Apart from the stop before merging a customer-facing change whose automated
verification was inconclusive, only stop for a genuine product/requirements ambiguity, a
security-sensitive decision, an irreversible/destructive action outside the
normal workflow, or something else that truly requires human judgment.
