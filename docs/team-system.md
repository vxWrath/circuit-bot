# FF Circuit - Team System

Teams are the league's core entity - every other system (pods, schedules, ratings, rosters, tournaments) hangs off them. This document plans the first team pass: the `teams`, `roster`, and `transaction_log` tables, the `modules/teams` package, the create-and-approve flow, team lifecycle, lineage, and the approver role that gates it.

## Scope

In this pass:

- An **approver** staff role in config, an `approver_only` check, and a new-teams channel
- Migrations `0002_teams.sql`, `0003_roster.sql`, and `0004_transaction_log.sql`, plus the `modules/teams` package (models, service, errors)
- The command flow: `/create team` (with autocomplete), `/pending teams`, `/approve team`, `/deny team`, `/promote`, `/demote`, `/deactivate team`
- Team lineage: the `previous_team_id` chain, gated on coaching history from the transaction log
- A bot-managed Discord role and emoji per team (both created at approval) and an optional uploaded team logo

Deferred - each gets its own pass later:

| Deferred | Why it waits |
| --- | --- |
| Roster rules (shared-player cap, Window 4 selections, the 8-player minimum) | The table is the roster from day one; the caps and locks need windows and ratings to gate on |
| Rating columns and the rating batch | Ratings are written by the window-close batch, not by team commands; the calibrated model ports as a `team_ratings` table with window snapshots |
| Window admission timing (teams join between windows) | Needs the window/circuit entity first |
| Ownership transfer | Allowed until Window 3 closes per the docs - it's a roster-row swap with a log entry, no schema change, but it needs windows to gate on |
| Pod channels and game threads | Need ratings and windows to seed from |
| Hiatus, tournament eligibility fields | Status/columns that only mean something once circuits exist |

## Staff Roles

The first staff role is the **approver**: it runs team creation (approve/deny) and deactivation. Roles are named by their job - more (moderator for disputes, and so on) slot in beside it later.

`config.toml` exists at the repo root now - league settings there, secrets in env - loaded into `bot.config.league` as `LeagueSettings` (override the path with the `CONFIG_PATH` env var):

```toml
[guild]
verified_role = 1553923281239941261
roblox_link_log_channel = 1561244309788037170

[roles]
approver = 1564127606536667267

[teams]
pending_teams_channel = 1564128159950045225
new_teams_channel = 1564127916776890422
```

- `CheckType.APPROVER` in `app_commands.py` with its message, and `approver_only()` in `utils.py` next to `developer_only` and `guild_owner_only`. Developers pass `approver_only` too - devs are individuals, approver is a role, and the dev check stays the strongest gate.
- The **pending-teams channel** is the list of created teams: every `/create team` posts its request embed there, and the approvers act on it - the queue.
- The **new-teams channel** is the list of approved teams: every approval posts an announcement there - the public record of who joined the league.

## The Tables

### `teams`

```sql
CREATE TABLE teams (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text NOT NULL,
    role_snowflake   bigint,
    emoji_snowflake  bigint,
    logo_url         text,
    active           boolean NOT NULL DEFAULT false,
    status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'denied')),
    previous_team_id bigint UNIQUE REFERENCES teams(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- A name is claimed while its team is pending, or approved and active; a
-- denied or deactivated team frees it.
CREATE UNIQUE INDEX teams_lower_name_key
    ON teams (lower(name))
    WHERE status = 'pending' OR (status = 'approved' AND active);
```

### `roster`

The roster table **is team membership** - there is no separate members table. A plain player has no role; ownership and coaching are roles within the roster.

```sql
CREATE TABLE roster (
    team_id   bigint NOT NULL REFERENCES teams(id),
    player    bigint NOT NULL REFERENCES players(snowflake),
    role      text CHECK (role IN ('owner', 'coach')),
    joined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, player)
);

-- One owner per team, one team per owner (as owner).
CREATE UNIQUE INDEX roster_one_owner_per_team ON roster (team_id) WHERE role = 'owner';
CREATE UNIQUE INDEX roster_one_team_per_owner ON roster (player) WHERE role = 'owner';
```

### `transaction_log`

The transaction log: append-only, one row per roster transaction. Nothing here is ever updated or deleted - it is the record of who was what, when. Lineage checks and the autocomplete read it.

```sql
CREATE TABLE transaction_log (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id bigint NOT NULL REFERENCES teams(id),
    player  bigint NOT NULL REFERENCES players(snowflake),
    role    text CHECK (role IN ('owner', 'coach')),
    action  text NOT NULL CHECK (action IN ('join', 'promote', 'demote', 'leave')),
    at      timestamptz NOT NULL DEFAULT now()
);
```

Decisions, and why:

- **No `owner` column on `teams`.** Ownership is a roster row with `role = 'owner'`, created in the same transaction as the team. The partial unique indexes enforce the docs' "one owner per team, one team per owner". Ownership transfer (allowed until Window 3 closes) becomes a roster-row swap plus a log entry, with no schema change. Coaches are roster rows with `role = 'coach'` - and the docs allow a player to coach multiple teams, so no cap index on coaches. A plain player is a roster row with `role = NULL`.
- **Every roster mutation writes a `transaction_log` row in the same transaction.** The log is what makes "were a coach at some point" answerable after demotions and departures, and it serves the league's transparency promise - all roster movement recorded by the bot.
- **`role_snowflake` and `emoji_snowflake` are both created at approval.** The bot creates the team's Discord role when the team is approved and stores the snowflake on the row - pending teams have `NULL`, every approved team has one. If a logo was uploaded, the bot also creates a guild emoji from the image and stores `emoji_snowflake`. Discord owns both: if either is deleted on Discord, the database is not out of sync - nothing repairs or re-syncs. Code that uses them fetches first and falls back. The bot deletes both as cleanup on deactivate.
- **`logo_url` is the team logo** - an image uploaded by the owner during the pending phase (optional). The bot re-uploads the image to the pending-teams channel and stores the durable CDN URL there, so the logo survives the uploader's own message and doubles as the request embed's image for approvers. The URL stays on the row after approval as the source of the emoji. Plain `NULL` when the team has none.
- **`active` is the lifecycle flag.** A team that leaves is never removed or deleted - `active` flips to `false` and the row stays. Teams cannot rejoin: reactivation is impossible by design, and the team that carries on is always a new row (see Lineage).
- **Status vs. active.** `status` is the approval state (`pending -> approved/denied`); `active` is the lifecycle state. Create sets `pending` + `active = false`; approve sets `approved` + `active = true` and creates the Discord role (and emoji, if a logo was uploaded); deny sets `denied`; deactivate keeps `approved` and sets `active = false`.
- **Identity is database-generated** (`GENERATED ALWAYS AS IDENTITY`), not a Discord snowflake. A team has no natural snowflake at creation - pod channels and game threads arrive later and get their own columns. `id` joins `created_at`/`updated_at` in `Meta.generated`; `Repository.insert` already excludes generated columns and `RETURNING *` reads the id back.
- **Names are claimed, not owned forever.** The unique index on `lower(name)` covers only pending and active teams: an active team's name is protected, a pending application holds its name, and a denied or deactivated team frees it. A denied owner can re-submit the same name; a team continuing an inactive predecessor can keep the old name.
- **No rating columns.** The window-close batch will own a `team_ratings` table (current values plus window snapshots) when the model ports - ratings are written by the batch, not by team commands, and the history matters for overturns and backtesting.

## Lineage

A team that leaves is inactive forever and can never rejoin - there must always be a new team. History across one creator's teams is tracked by linking the new team to the previous one: **`previous_team_id`**. The chain walks backward, each link to its own predecessor if applicable.

- **Set at creation** - the `previous_team` argument on `/create team`, offered through autocomplete.
- **Eligibility comes from the transaction log**: the creator may link only a team where the log shows them as an **owner or coach at some point**. A plain player who never coached cannot claim a team's history; a former coach can, even after demotion or departure - the log remembers.
- **Service-enforced rules:**
  - The previous team must be `active = false` - you cannot fork the history of a live team.
  - The creator must have a `transaction_log` row on that team with `role IN ('owner', 'coach')`.
  - `previous_team_id` is `UNIQUE` - the lineage is a chain, not a tree; a team has at most one successor.
- **Why a column, not an array or a table**: the chain is strictly one-to-one backward. A column plus `UNIQUE` enforces the chain shape in the schema itself; an array would need maintenance on every link and a table adds nothing a self-referencing FK doesn't give. Full ancestry is one recursive query (`WITH RECURSIVE`) or a short service walk.
- **Display**: a team's profile shows its chain - "continuing Falcons, continuing Comets" - and the linked teams keep their own histories on their own rows.

## The Module - `modules/teams/`

Mirrors `modules/players/`:

- **`models.py`** - `TeamTable` (abstract; `table = "teams"`, `primary_key = "id"`, `generated = ("id", "created_at", "updated_at")`), `TeamRow`, `CreateTeam` (`kw_only=True`; `name`, `previous_team_id`); `RosterTable` (abstract; `table = "roster"`), `RosterRow`, `CreateRosterEntry` (`kw_only=True`; `team_id`, `player`, `role`); `TransactionLogTable` (abstract; `table = "transaction_log"`), `TransactionLogRow`, `CreateTransactionLog` (`kw_only=True`; `team_id`, `player`, `role`, `action`).
- **`service.py`** - `TeamService`: repositories via `get_repository`, one surface for commands. Methods: `create_pending(name, owner, previous_team_id)` (opens its own transaction - team row, the owner roster row, and the owner's `join` log row, the "several statements" pattern from the players service), `attach_logo(team, url)`, `approve(team)` (creates the Discord role, creates the guild emoji from the logo if one was uploaded, then updates the row to `approved` + `active = true` + both snowflakes), `deny(team)`, `deactivate(team)`, `promote(owner, player)` (infers the owner's active owned team, upserts the player's roster row to `role = 'coach'`, appends the log row), `demote(owner, player)` (same inference, returns a coach to a plain roster row, `role = NULL`, appends the log row), `find_by_name(name)`, `find_by_owner(snowflake)`, `owners(team)`, `pending()`, `lineage(team)`, `previous_teams_for(player)` (the inactive teams the log shows them owning or coaching - feeds the autocomplete). Name and lineage validation lives in the service; commands layer pre-checks on top for UX.
- **`errors.py`** - `TeamUniqueViolationFormatter`, mirroring `PlayerUniqueViolationFormatter`: keyed on constraint names, `report = True` (a constraint hit means a pre-check lost a race):

| Constraint | Reply |
| --- | --- |
| `roster_one_team_per_owner` | "You already own a team." |
| `roster_one_owner_per_team` | "That team already has an owner." |
| `teams_lower_name_key` | "That team name is already taken." |
| `teams_previous_team_id_key` | "That team already has a successor." |

## The Command Flow

1. **`/create team <name> [previous_team]`** - any verified member. A normal command, not a modal, so `previous_team` gets autocomplete: the inactive teams the log shows the invoker owning or coaching. Pre-checks: name format/length, no team already owned, name not claimed. The service inserts the pending row, the owner roster row, and the join log row in one transaction. The bot then opens a modal for the optional logo - a file upload (modals support file inputs now), dismiss to skip. Once uploaded or skipped, the request embed (logo included) is posted to the pending-teams channel - the queue the approvers work from.
2. **`/pending teams`** - approver. Lists every pending request.
3. **`/approve team <name>`** - approver. Creates the team's Discord role and, if a logo was uploaded, the guild emoji from it; flips the row to `approved` + `active = true` with both snowflakes; DMs the owner; updates the request embed in the pending-teams channel to show the decision, and posts an announcement to the new-teams channel - the public list of approved teams.
4. **`/deny team <name> [reason]`** - approver. Flips to `denied` and DMs the owner with the reason. The name is freed; the owner may re-submit.
5. **`/promote <player>`** - the owner only. The bot infers the team from the invoker's owner roster row on their active team - no team argument, an owner has exactly one. Upserts the player's roster row to `role = 'coach'` and appends the log row.
6. **`/demote <player>`** - the owner only, same inference. Returns a coach to a plain roster row, `role = NULL`, and appends the log row; they stay on the roster, and the log still shows they once coached.
7. **`/deactivate team <name> [reason]`** - approver. The team left: `active = false`, and the Discord role and emoji are deleted as cleanup. The row, its roster, its log, and its history stay; the name is freed. No reactivation - returning means a new team, linked by lineage.

Edge cases:

- **Duplicate name / duplicate owner races** - the constraint formatter owns them (above); the pre-checks catch the common case.
- **Re-submission after denial** - a new pending row with the same or a new name. A denied row is excluded from the name index, so the name is claimable again.
- **Owner creates a new team after deactivation** - their owner slot frees when the team goes inactive, and the new team can link to the old one for history - their owner log rows make them eligible.
- **`/promote` or `/demote` with no active owned team** - the inference finds nothing and the command fails with a plain message; no partial writes.
- **A deleted Discord role or emoji** - the snowflakes stay in the database, and nothing syncs. Consumers fetch first and fall back; the values are historical, not live handles.
- **Approval before the owner is verified** - the create pre-check requires the verified role, so this can't happen; approval adds no new checks.
- **Logo never uploaded** - the flow proceeds without one after the modal is dismissed; `logo_url` stays `NULL` and no emoji is created. The logo is optional by design.
- **A demoted coach links their history** - the log row survives the demote, so lineage eligibility is unaffected.

## Deferred - with their hooks

- **Roster rules** - the table already holds players, coaches, and owners; the shared-player cap, Window 4 selections, and the 8-player minimum are constraints that need windows and ratings to gate on. Signing and releasing players will be log actions (`join`/`leave`) on the existing log.
- **Ratings** - `team_ratings` (team, window, overall, offense, defense, confidence, snapshot data for overturns) written by the window-close batch once the model ports from `tools/`.
- **Admission windows** - joining/leaving transitions that need the window entity to time them.
- **Pod channels, hiatus, tournament fields** - columns and channels that need windows and ratings to mean anything.
