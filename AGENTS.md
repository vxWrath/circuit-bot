<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **circuit-bot** (1796 symbols, 3166 relationships, 110 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "master"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/circuit-bot/context` | Codebase overview, check index freshness |
| `gitnexus://repo/circuit-bot/clusters` | All functional areas |
| `gitnexus://repo/circuit-bot/processes` | All execution flows |
| `gitnexus://repo/circuit-bot/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

# Project Conventions

Shared ruleset for every agent tool in this repo (Claude Code pulls it in via `@AGENTS.md` from CLAUDE.md).

## Stack

Python 3.14 (uv). Bot: `discord` v2.x (the rewrite; the pyproject dep is `discord`, not `discord.py`). Data: asyncpg for PostgreSQL, redis-py async for Redis, msgspec + orjson for serialization, structlog for logging.

## Layout

- Entry point: `circuit_bot:main` in `src/circuit_bot/__init__.py`, runs `CircuitBot` from `circuit_bot.core`.
- `core/` is the kernel, one-way dependency: modules import from core, core never imports modules.
  - `core/database/` asyncpg Database + Repository base; `core/cache/` Redis client; `core/api/` outbound aiohttp client with retry + singleflight; `core/roblox/` and `core/bloxlink/` external API clients built on it; `core/bluerobot/` discord glue.
- `modules/<name>/` one package per domain (players, teams): msgspec models, repositories, services, formatters. New entities go here (model → repo → service → formatter → command), never in core.
- `commands/` slash commands, `events/` discord handlers, `tools/` offline data tooling.
- `config.toml` holds league settings; secrets come from env vars only.

## Conventions

- Strict type annotations everywhere; all I/O must be non-blocking asyncio.
- msgspec.Struct for every model, DTO, and DB row. Plain frozen dataclasses are tolerated only where already established (`core/ratelimit.py`).
- Ruff and pyright are the style and type sources of truth (line length 120, double quotes, `circuit_bot` first-party).

## Verification

- Callers and shared-schema changes: GitNexus MCP (`query`, `impact`, `context`, `detect_changes`) over blind grep; run `impact` before editing any shared msgspec model or DB table.
- Library APIs: check Context7 before writing non-trivial `discord`, `msgspec`, `asyncpg`, or `redis` code.
- Never finish an edit with pyright errors in touched files (no C/C++/Rust sources here, so clangd/rust-analyzer do not apply).
- Reuse ladder first: repo helper → stdlib → installed dependency. Never add a dependency for a few lines.
