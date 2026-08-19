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

## What else in this repo applies to Fuchsia work

Generically useful: `fuchsia-source` (deep source questions via subagent),
`fuchsia-driver-bind-debug` (driver didn't bind), `fuchsia-multi-checkout` (second workstream on
one machine, `fx worktree`). Written against one specific hardware bench and best treated as
templates for your own lab: `fuchsia-hardware-bench`, `fuchsia-boot-test-ci`.
