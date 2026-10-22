---
name: atlas
description: "Build or update a Project Atlas map for a project."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [atlas, documentation, code-map, knowledge-base]
---

# Project Atlas

Atlas turns a repository into two surfaces inside Hermes Desktop: a **skill
tree** of the project's parts and a **documentation tab**. The only stored
artifact is `<project>/.atlas/atlas.yaml`; the pages are derived from it.

## Data

- `.atlas/facts.json` — deterministic scanner evidence. Safe to delete; the
  next scan rebuilds it.
- `.atlas/atlas.yaml` — the map. Human-editable, diff-friendly, goes into git.
- `.atlas/changelog.md` — what each run changed and why.
- `.atlas/cache/hashes.json`, `.atlas/jobs/last.json` — local bookkeeping.
- The project's own code is **read-only** for Atlas. Never write outside
  `.atlas/`.

## When to use

- The user asks to document a project, map its functionality, or update an
  existing map.
- The user asks "what is in this project" and a map already exists — read it
  first (`atlas_status`), then look at code only where the map says `partial`
  or `none`.

## Procedure

1. `atlas_scan({project_path})` — refresh facts. The path must be absolute.
2. `atlas_plan({project_path, scope})` — read the numbers (nodes, files, bytes,
   approximate tokens). **Show them to the user** before a mass build.
3. `atlas_build({project_path, scope, confirm: <plan hash>, deep: true})` — the
   first build; `deep` lets the model refine the branch list. For one node use
   `scope: "node:<id>"` (no confirmation needed). For incremental updates use
   `scope: "stale"`.
4. Report the resulting tree (`tree_text`), coverage, orphans and warnings.
   Point the user at the **Project Atlas** row in the sidebar.

## Rules

- A node exists only with a real path or commit. If the model cannot support a
  claim, it must return `status: partial` and a `todo` item; never invent a
  reason or a dependency.
- `locked: true` nodes are the user's. The builder skips them and only adds a
  `todo` note when their code moved. Do not unlock or rewrite them unless the
  user asks.
- Manual edits to unlocked nodes survive because the builder only touches the
  fields it owns (`summary`, `overview`, `status`, `confidence`, `kind`,
  `decisions`, `links`, `todo`, `sourceHash`). A hand-edited name, parent or
  path list is not rewritten.
- If a build returns `needs_confirmation`, show the plan and call again with the
  hash. Do not bypass the confirmation.
- Prefer `node:<id>` / `stale` over `all`; a full rebuild costs one model call
  per node.
- Never paste secrets into a prompt. The scanner skips `.env`, keys and
  `.gitignore`'d files, and the evidence builder redacts token-shaped strings,
  but do not add secrets by hand either.
- The desktop page and the tools share the same `atlas_core`; there is no second
  implementation to keep in sync.

## Reading a map

- `full` — the page is confirmed by code.
- `partial` — part of it is inferred; check `todo`.
- `none` — the code was found but not described yet.
- `confidence` is the model's own estimate, not a quality guarantee.
