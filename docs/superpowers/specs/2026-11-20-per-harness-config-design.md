# Per-harness config sections

## Goal

Each harness issue-herd can launch (Claude Code and Antigravity) gets its own
section in the config, holding its own coordinator, subagent tiers and final
review. Today only Claude Code has tiers and a final-review model; an
Antigravity session runs every subagent, and the final review, on its own
model. Measured on a live Antigravity run (issue 2797 in F16PDATool), the
session and its one subagent both ran `gemini-4-neon-high`; per-harness tiers
let us test cheaper subagent routing for Antigravity.

Existing configs keep working unchanged.

## Config shape

```json
{
  "agent": "claude",
  "claude": {
    "coordinator": { "model": "opus", "effort": "high" },
    "ultracode_coordinator": { "effort": "xhigh" },
    "tiers": {
      "light": { "model": "haiku" },
      "standard": { "model": "sonnet" },
      "heavy": { "model": "opus" }
    },
    "final_review": { "model": "opus", "effort": "xhigh" }
  },
  "antigravity": {
    "coordinator": { "model": "gemini-4-neon-high" },
    "tiers": {
      "light": { "model": "flash" },
      "standard": { "model": "pro" },
      "heavy": { "model": "inherit" }
    },
    "final_review": { "model": "inherit" }
  }
}
```

- `claude` takes `coordinator`, `ultracode_coordinator`, `tiers` and
  `final_review`, with the keys and values they take today.
- `antigravity` takes `coordinator`, `tiers` and `final_review`. There is no
  `ultracode_coordinator`, since `--ultracode` is refused with Antigravity.
- `agent` stays top-level.

`config/defaults.json` moves to this nested form, with the values shown above.

## Backward compatibility

- **Flat Claude keys.** A top-level `coordinator`, `ultracode_coordinator`,
  `tiers` or `final_review` is read as the same key under `claude`.
- **Folding is per file, before merging.** Each file's flat keys are folded
  into that file's `claude` section, with the file's own `claude` keys winning
  a clash inside that file. Only then are the files deep-merged in order
  (defaults, user config, repo config). So a flat key in a later file still
  overrides a nested key in an earlier one: a user config written in the flat
  form keeps overriding the nested defaults.
- **`antigravity.model`.** Still accepted, as a synonym for
  `antigravity.coordinator.model`. Within one file, an explicit
  `antigravity.coordinator.model` wins over it.

## Validation

| Key | Claude | Antigravity |
|---|---|---|
| `coordinator.model` | alias or full model ID, as today | full model ID, such as `gemini-4-neon-high` |
| tier and `final_review` models | `haiku`, `sonnet`, `opus`, `fable` | `inherit`, `flash_lite`, `flash`, `pro` |
| `effort` (any section) | as today | refused |

- The Antigravity aliases are the values its `invoke_subagent` tool accepts
  for `Model`. Measured with `agy` 1.5.11: `flash_lite` ran
  `gemini-3.5-flash-lite`, `flash` ran `gemini-4-hydrogen-tiered-high`, `pro`
  ran `gemini-4-hydrogen-tiered-xhigh`, and a full model ID was rejected.
- An `effort` key anywhere under `antigravity` is refused with the current
  message: Antigravity takes its effort from the model ID.
- Errors keep naming the file and the full key path, such as
  `antigravity.tiers.light.model`. Unknown keys are still errors at every
  level, including inside `claude` and `antigravity`.
- `--model` overrides the coordinator model of whichever agent launches.
  `--effort` and `--ultracode` stay refused with Antigravity.

## Prompt

The Antigravity `SUBAGENTS` block becomes a `MODEL / SUBAGENT ROUTING`
section:

- The same task descriptions as Claude's routing section for `light`,
  `standard` and `heavy`, each naming the value to pass as `invoke_subagent`'s
  `Model` parameter.
- An instruction to choose a `Model` for every subagent rather than leaving it
  on `inherit` by default; `inherit` is used where a tier is configured as
  `inherit`.
- The final whole-branch review is dispatched as a subagent with the
  `final_review` model, not done inline.
- The fallback stays: if the session cannot start a subagent, it does the work
  itself and reviews each change afresh before moving on.
- The scratchpad note stays.

The tier and review values reach the prompt through the existing
`LIGHT_MODEL`, `STANDARD_MODEL`, `HEAVY_MODEL` and `FINAL_REVIEW_*`
variables, filled from the launching agent's section.

## Launch summary

For Antigravity the summary adds a line with the tier mapping, for example
`Subagents: light flash, standard pro, heavy inherit, review inherit`.

## Tests

New cases in `tests/test_issue_herd.py`:

- flat Claude keys still route the session and tiers;
- a `claude` section overrides flat keys in the same file;
- a later file's flat keys override an earlier file's `claude` section;
- the Antigravity tiers and final-review model appear in the rendered prompt;
- `antigravity.model` still sets the Antigravity coordinator, and
  `antigravity.coordinator.model` wins over it in the same file;
- a bad Antigravity tier alias is rejected, naming the key;
- an `effort` key under `antigravity` is rejected;
- a Claude alias (`opus`) in an Antigravity tier, and an Antigravity alias
  (`flash`) in a Claude tier, are both rejected.

Existing tests keep passing unchanged.

## Docs and release

- The README's config section is rewritten around the nested shape, with a
  note that the flat keys are still read as Claude's. The Antigravity section
  describes its routing and the alias-to-model mapping measured above.
- The plugin version goes to 0.11.0 (a feature users notice), per
  `CLAUDE.md`.

## Out of scope

- Pushing more work out of the Antigravity coordinator to cut context size.
- Supporting `agy --effort`, pending a test of how it interacts with the model
  ID.
