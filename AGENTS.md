<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# AGENTS.md — fuchsia-skills

## Purpose

Agent skills for working on the public Fuchsia tree. Split out of
[public-skills](https://github.com/curtisgalloway/public-skills) so that Fuchsia developers can
install just this set. Skills here are written primarily for Claude Code — the setup skills exist
because fuchsia.git ships Gemini-oriented agent config that Claude does not read — but the
non-setup skills are agent-neutral.

## Privacy rules — read before committing

This is a **public** repository, and the tree it describes is the **public** Fuchsia tree. Before
staging or pushing any file, verify it contains none of the following:

- Hostnames, FQDNs, or IP addresses for private machines or networks (the hardware-bench skills
  describe a real lab — keep every host, address, and serial number a placeholder)
- Internal service names or URLs, internal-only doc links, or `go/` shortlinks
- Email addresses, usernames, or account identifiers
- Local filesystem paths that reveal a username or home directory structure (use `~` or `<path>`)
- API keys, tokens, passwords, or any credential — even expired ones
- Organization-internal terminology, project codenames, or team names
- Anything sourced from a non-public Fuchsia checkout, internal bug tracker, or internal design doc

Everything asserted about Fuchsia here must be verifiable from the public tree or public
fuchsia.dev docs.

## Skill structure

Each skill lives in `skills/<skill-name>/` and must contain:

- `SKILL.md` — YAML frontmatter (`name`, `description`), then the SPDX header, then the body:
  purpose, trigger phrases, required tools, inputs/outputs, and caveats
- Any supporting scripts or templates the skill needs, under `scripts/` or `assets/`

Skills should be self-contained. If a skill needs a third-party tool, call that dependency out
clearly in `SKILL.md`.

## Conventions specific to this repo

- **Date-stamp what drifts.** Upstream moves fast. Where a skill states a count, a path, or a
  command's behaviour, note the date it was verified against a public checkout, and say plainly
  that it will drift.
- **Cite the tree, not memory.** Prefer `//path/to/file.cc` references and public fuchsia.dev URLs
  over recalled behaviour.
- **Cross-repo references.** Several skills hand off to skills in other repos: the driver-porting
  skills (`os-investigator`, `cleanroom-spec`, `rpi-expert`, and the other board experts) live in
  [driver-lab](https://github.com/curtisgalloway/driver-lab); the working-style skills
  (`intern-mode`, `handoff`, `learn`) live in `public-skills`. Name the repo when you reference
  them, so a reader who installed only this plugin knows where to look. Both install from the
  `curtisg-skills` marketplace that `public-skills` hosts.
- **Bump the plugin version in every pull request.** Claude Code reinstalls a plugin only when
  the `version` in `.claude-plugin/plugin.json` changes, and the whole repo is the plugin, so
  every pull request runs `python3 utilities/plugin-version.py bump`. Versions are calendar
  dates, `YYYY.MDD.N` (`2026.927.0`, then `2026.927.1` the same day; October 1 is `1001`).
  Keep the version only in `plugin.json`, never in the `marketplace.json` entry. CI runs
  `plugin-version.py check` and fails a pull request that skips the bump. The script is a copy
  of the one in `public-skills`; change both together.

## License

Apache 2.0. The full grant lives in `LICENSE` at the repo root; source files carry only the SPDX
identifier, not the boilerplate:

```
# SPDX-FileCopyrightText: <year> contributors
# SPDX-License-Identifier: Apache-2.0
```

Use the file's own comment syntax — `#` for Python/shell, `//` for Rust, and an HTML comment block
for Markdown (placed *after* the YAML frontmatter, never before it, or the frontmatter will not
parse):

```
<!--
SPDX-FileCopyrightText: <year> contributors
SPDX-License-Identifier: Apache-2.0
-->
```
