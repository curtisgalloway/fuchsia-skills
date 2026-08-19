<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# Using Claude Code on the public Fuchsia tree

For engineers used to the Gemini-based agent setup that fuchsia.git ships natively, moving to
Claude Code. Short version: the tree's agent content (project instructions, ~80 in-tree skills)
is all reusable — Claude just doesn't discover any of it until you install a small local bridge.

## Setup, in order

1. **Install Claude Code** and sign in (`claude` from any terminal).
2. **Install this repo's plugin** — in a Claude session:

   ```
   /plugin marketplace add curtisgalloway/fuchsia-skills
   /plugin install fuchsia-skills@fuchsia-skills
   ```

3. **No Fuchsia tree yet?** Ask Claude to check one out — the `fuchsia-checkout` skill drives the
   bootstrap end to end, including the multi-hour background steps and the `.gitcookies`
   authentication failure Googlers hit on anonymous checkouts.
4. **Bridge the tree** — from inside the checkout, ask Claude to run `fuchsia-claude-setup` (or
   run its `scripts/link_fuchsia_skills.py` yourself). This creates, locally and git-excluded:
   - `AGENTS.md -> GEMINI.md` symlinks, so Claude loads the same project instructions gemini-cli
     does (root and nested);
   - a generated `.claude/skills/` symlink farm over every tracked in-tree `SKILL.md`, replacing
     `skills.json`/`fx manage-skills` discovery;
   - `.git/info/exclude` entries so none of it ever shows up in `git status`.

   Start a fresh session and confirm in-tree skills (e.g. `debugging-driver-binding`,
   `manage-emulator`) are listed.

## What maps to what

| You had with Gemini | You get with Claude |
|---|---|
| `GEMINI.md` loaded automatically | same content via the `AGENTS.md` symlink |
| global skills from `//.agents/skills/` | same skills via the `.claude/skills/` farm |
| team skills opted in via `fx manage-skills` / `skills.json` | `link_fuchsia_skills.py --only <path> ...` (persisted; default is *all* skills) |
| `.geminiignore` widening search into `third_party/` etc. | no equivalent — use `jiri grep` or `rg --no-ignore-vcs` for cross-petal searches |

The in-tree skills themselves are plain `SKILL.md` files and work unmodified under Claude.

## Two habits

- **Re-run the bridge after `jiri update`.** Upstream renames and adds skill directories; the
  script fixes renames and prunes dead links (a farm left alone for two months had 4 dangling
  links and ~15 missing skills).
- **Never commit the bridge.** `AGENTS.md`, `.claude/`, and `.gitignore` edits don't belong in
  fuchsia.git; the script keeps everything in `.git/info/exclude` for exactly that reason.

## What else applies to Fuchsia work

In this repo — generically useful: `fuchsia-source` (deep source questions via subagent),
`fuchsia-driver-bind-debug` (driver didn't bind), `fuchsia-multi-checkout` (second workstream on
one machine, `fx worktree`). Written against one specific hardware bench and best treated as
templates for your own lab: `fuchsia-hardware-bench`, `fuchsia-boot-test-ci`.

In [curtisgalloway/public-skills](https://github.com/curtisgalloway/public-skills), worth
installing alongside this one:

```
/plugin marketplace add curtisgalloway/public-skills
/plugin install public-skills@public-skills
```

- **Porting a driver into Fuchsia from a differently-licensed OS** — `os-investigator` (reads the
  original source, returns hardware facts in original words, never code, each tagged by
  provenance), `peripheral-spec` (turns that into a complete implementation spec for one
  peripheral), `cleanroom-implementer` (the rules and audit for the agent that writes the Fuchsia
  driver from the spec). Pair with `fuchsia-source` for the target-side DFv2 / bind / CML question.
- **Board experts** — `rpi-expert` (Pi 5, BCM2712 + RP1) and `indiedroid-nova-expert` (RK3588S) for
  memory maps, boot chain, interrupts, clocks, and datasheet citations. `fuchsia-source` and
  `fuchsia-driver-bind-debug` hand off to these by name for board-specific hardware questions.
- **Working style** — `intern-mode` (stop and report rather than thrash, handy on long bring-up
  sessions), `design-partner`, `learn`, `wrapup`.
