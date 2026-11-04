# FF Circuit - Team System

Teams are the league's core entity - every other system (pods, schedules, ratings, rosters, tournaments) hangs off them. This document plans the first team pass: the `teams`, `transaction_log`, and `offers` tables with the `roster` view over them, the `modules/teams` package, the create-and-approve flow, team lifecycle, lineage, and the approver role that gates it.

## Scope

In this pass:

- An **approver** staff role in config, an `approver_only` check, and a new-teams channel
- Migrations `0002_teams.sql`, `0003_transaction_log.sql`, `0004_roster.sql` (the view), and `0005_offers.sql`, plus the `modules/teams` package (models, service, errors)
- The `offers` table and its indexes; the offer, release, and demand commands land with the roster rules
- The command flow: `/create team` (with autocomplete), `/pending teams`, `/approve team`, `/deny team`, `/promote`, `/demote`, `/deactivate team`
- Team lineage: the `previous_team_id` chain, gated on ownership or coaching history
- A bot-managed Discord role and emoji per team (both created at approval) and an optional uploaded team logo

Deferred - each gets its own pass later:

| Deferred | Why it waits |
| --- | --- |
| Roster rules (shared-player cap, Window 4 selections, the 8-player minimum) | Membership lives in the log from day one; the caps and locks need windows and ratings to gate on |
| Offer, release, and demand commands | The `offers` table is in place; the commands need the roster rules and window locks |
| Rating columns and the rating batch | Ratings are written by the window-close batch, not by team commands; the calibrated model ports as a `team_ratings` table with window snapshots |
| Window admission timing (teams join between windows) | Needs the window/circuit entity first |
| Ownership transfer | Allowed until Window 3 closes per the docs - it updates `teams.owner` and appends log rows, no schema change, but it needs windows to gate on |
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
    id                        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                      text NOT NULL,
    owner                     bigint NOT NULL REFERENCES players(snowflake),
    role_snowflake            bigint,
    emoji_snowflake           bigint,
    logo_url                  text,
    request_message_snowflake bigint,

    status                    text NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'approved', 'denied', 'inactive')),
    approved_at               timestamptz,
    inactivated_at            timestamptz,

    previous_team_id          bigint UNIQUE REFERENCES teams(id),

    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);

-- A name is claimed while its team is pending or approved; denied and
-- inactive teams free it.
CREATE UNIQUE INDEX teams_lower_name_key
    ON teams (lower(name))
    WHERE status IN ('pending', 'approved');

-- One live team per owner; the slot frees when the team is denied or leaves.
CREATE UNIQUE INDEX teams_one_team_per_owner
    ON teams (owner)
    WHERE status IN ('pending', 'approved');
```

Decisions, and why:

- **`owner` is on `teams`.** Ownership is one-to-one with the team, so the column is the current-state pointer and the unique index guarantees one live team per owner. The index covers only pending and approved teams, so the slot frees when the team is denied or inactive; a separate membership table had no access to the team's status and would have kept the old owner blocked. The creation `promote` log row is the owner's appointment; the view and lineage read the log, and both are written in the same transaction.
- **Every roster mutation writes a `transaction_log` row in the same transaction.** The log is what makes "were a coach at some point" answerable after demotions and departures, and it serves the league's transparency promise - all roster movement recorded by the bot, with the actor.
- **`role_snowflake` and `emoji_snowflake` are both created at approval.** The bot creates the team's Discord role when the team is approved and stores the snowflake on the row - pending teams have `NULL`, every team that was approved has one. If a logo was uploaded, the bot also creates a guild emoji from the image and stores `emoji_snowflake`. Discord owns both: if either is deleted on Discord, the database is not out of sync - nothing repairs or re-syncs. Code that uses them fetches first and falls back. The bot deletes both as cleanup on deactivate.
- **`logo_url` is the team logo** - an image uploaded by the owner during the pending phase (optional). The bot re-uploads the image to the pending-teams channel and posts it on the request message. `request_message_snowflake` stores that message, so the request embed can be edited and the attachment refetched at approval. Treat `logo_url` as a display cache - Discord attachment URLs are signed and expire. Plain `NULL` when the team has none.
- **`request_message_snowflake` is the approver queue handle.** Every pending request is an embed in the pending-teams channel; the message snowflake is stored when it is posted, so `/approve` and `/deny` can edit the embed after a restart. A pending row with no message (the post failed or never ran) still appears in `/pending teams`; approval skips the embed edit.
- **`status` is the lifecycle.** The four states are `pending -> approved/denied`, and `approved -> inactive` when the team leaves. There is no separate `active` flag: the two-column version allowed impossible pairs and made the partial indexes harder. `approved_at` and `inactivated_at` keep the timing separate from `updated_at`.
- **A team that leaves is never removed or deleted.** `status = 'inactive'` and the row stays. Teams cannot rejoin: reactivation is impossible by design, and the team that carries on is always a new row (see Lineage).
- **Identity is database-generated** (`GENERATED ALWAYS AS IDENTITY`), not a Discord snowflake. A team has no natural snowflake at creation - pod channels and game threads arrive later and get their own columns. `id` joins `created_at`/`updated_at` in `Meta.generated`; `Repository.insert` excludes them, and `RETURNING *` fills the defaulted `id` on `CreateTeam`.
- **Names are claimed, not owned forever.** The unique index on `lower(name)` covers only pending and approved teams: an approved team's name is protected, a pending application holds its name, and a denied or inactive team frees it. A denied owner can re-submit the same name; a team continuing an inactive predecessor can keep the old name. `find_by_name` filters to live statuses, so an inactive team sharing a name with the new one never matches.
- **No rating columns.** The window-close batch will own a `team_ratings` table (current values plus window snapshots) when the model ports - ratings are written by the batch, not by team commands, and the history matters for overturns and backtesting.

### `transaction_log`

The transaction log: append-only, one row per transaction. Nothing here is ever updated or deleted - it is the record of who was what, when, and every offer, release, demand, and role change. Lineage checks and the autocomplete read it, and the `roster` view derives current membership from it.

```sql
CREATE TABLE transaction_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player       bigint NOT NULL REFERENCES players(snowflake),
    from_team_id bigint REFERENCES teams(id),
    to_team_id   bigint REFERENCES teams(id),
    action       text NOT NULL CHECK (action IN (
                     'offer', 'release', 'demand',
                     'promote', 'demote'
                 )),
    role         text CHECK (role IN ('owner', 'coach')),
    actor        bigint REFERENCES players(snowflake),
    details      jsonb,
    at           timestamptz NOT NULL DEFAULT now(),

    CHECK (
        (action IN ('offer', 'promote', 'demote')
            AND to_team_id IS NOT NULL AND from_team_id IS NULL)
        OR (action IN ('release', 'demand')
            AND from_team_id IS NOT NULL AND to_team_id IS NULL)
    )
);

-- `role` is the role held after promote and demote; release and demand
-- record the role held before leaving.  NULL is a plain player.
-- `details` carries the offer id and status, and the end reason.

-- The team a player is offered to or promoted on.
CREATE INDEX transaction_log_to_team_player_idx
    ON transaction_log (to_team_id, player, at DESC, id DESC)
    WHERE to_team_id IS NOT NULL;

-- The team a player leaves or demands out of.
CREATE INDEX transaction_log_from_team_player_idx
    ON transaction_log (from_team_id, player, at DESC, id DESC)
    WHERE from_team_id IS NOT NULL;

-- Player history: the transactions shown on a profile, and the lineage check.
CREATE INDEX transaction_log_player_idx
    ON transaction_log (player, at DESC, id DESC);
```

Decisions, and why:

- **`from_team_id` / `to_team_id` carry the direction.** `to_team_id` is the team a player is offered to or promoted on; `from_team_id` is the team a player leaves or demands out of. An accepted offer sets `to_team_id`; a release or demand sets `from_team_id`. Both sides set is a transfer, which FF Circuit does not use for players - memberships are additive, so an accepted offer never removes a player from another team. The table CHECK enforces the shape: offer/promote/demote carry `to_team_id` only, release/demand carry `from_team_id` only.
- **`action` names the transaction.** The team creator's creation is a `promote` to `to_team_id` with `role='owner'`; `offer` is how another player joins, and an accepted offer is the membership event. `promote` also adds a coach when the target is not already on the roster; `demote` lowers a coach to a plain player. `release` is the team letting a player go; `demand` is the player forcing their way off. The `roster` view reads accepted offers and promote/demote on the `to` side and release/demand on the `from` side; pending, declined, and expired offers are history and do not change membership. `teams.owner` is the current-state constraint; the log carries the appointment.
- **`role` is the role held after promote and demote** (`owner`, `coach`, or `NULL` for a plain player); release and demand record the role held before leaving. Offer rows carry no role. That one rule lets the view, the lineage check, and the audit read the same rows without special cases.
- **`actor` records who wrote the row** - the offerer on an offer, the player on a demand, the owner on promote/demote, and the team's owner or coach on a release. It is nullable for system actions, and the command layer ensures the actor has a `players` row.
- **`details` is where the offer lives.** Offer rows carry the offer id and status (`pending`, `accepted`, `declined`, `expired`); a release or demand can carry the end reason. The offers table holds the terms; `details` is the log's pointer to it.
- **The indexes match the reads.** The `to_team_id` and `from_team_id` indexes serve the `roster` view and transaction history per team; `transaction_log_player_idx` serves a player's transaction history and the lineage check.

### `offers`

An offer is a team's invitation to a player. It expires and is accepted or declined; a player may hold offers from several teams.

```sql
CREATE TABLE offers (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id      bigint NOT NULL REFERENCES teams(id),
    player       bigint NOT NULL REFERENCES players(snowflake),
    offered_by   bigint NOT NULL REFERENCES players(snowflake),
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
    terms        jsonb NOT NULL DEFAULT '{}'::jsonb,
    expires_at   timestamptz NOT NULL,
    responded_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- A team has one pending offer to a player; a player may hold pending
-- offers from several teams.
CREATE UNIQUE INDEX offers_one_pending_per_team_player
    ON offers (team_id, player)
    WHERE status = 'pending';

CREATE INDEX offers_player_pending_idx
    ON offers (player)
    WHERE status = 'pending';
```

- `status` is `pending`, `accepted`, `declined`, or `expired`. `expires_at` is required; acceptance checks it, and a pending offer past it is expired.
- One pending offer per `(team, player)`; a player can hold pending offers from several teams.
- `offered_by` is the owner or coach who made the offer; the command checks their role.
- `terms` is the agreement itself; an accepted offer is the player-team agreement.
- Acceptance checks the shared-player cap and that the player is not already on the team's roster, then flips the offer to accepted and writes an `offer` log row with `to_team_id` and the accepted status in one transaction.
- Each offer event writes an `offer` log row with `to_team_id` and the offer id/status; the accepted row is the membership event the view reads. A declined or expired offer changes no membership.

### Demands

A demand is a player forcing their way off a team. There is no demands table: the log gets a `demand` row with `from_team_id`, the role held, and `actor` = the player; the accepted offer row remains the history. Add a state table only if demands gain a quota, cooldown, or approval step.

### Membership - the `roster` view

There is no roster table and no separate members table. Every membership change is a log row, and current membership is the latest membership event per `(team, player)`, minus departures:

```sql
CREATE VIEW roster AS
WITH events AS (
    SELECT player, to_team_id AS team_id, role, action, at, id
    FROM transaction_log
    WHERE action = 'offer'
      AND to_team_id IS NOT NULL
      AND details->>'status' = 'accepted'

    UNION ALL

    SELECT player, to_team_id AS team_id, role, action, at, id
    FROM transaction_log
    WHERE action IN ('promote', 'demote') AND to_team_id IS NOT NULL

    UNION ALL

    SELECT player, from_team_id AS team_id, role, action, at, id
    FROM transaction_log
    WHERE action IN ('release', 'demand') AND from_team_id IS NOT NULL
),
latest AS (
    SELECT DISTINCT ON (team_id, player)
        team_id, player, role, action, at, id
    FROM events
    ORDER BY team_id, player, at DESC, id DESC
)
SELECT team_id, player, role
FROM latest
WHERE action NOT IN ('release', 'demand');
```

The view is read-only and entirely log-derived. A plain player is `role IS NULL`; a coach is `role = 'coach'`; the owner is the creation `promote` row with `role = 'owner'`. `teams.owner` remains the current-state pointer and the unique-index constraint, written in the same transaction. For an inactive team these are its final rows; roster queries over live teams join `teams` on `status = 'approved'`. The log answers what the roster table used to:

- current membership and role - the view
- time on roster - the creation `promote` for the owner, the accepted offer's `at`, or the promote for a coach added directly
- "was ever an owner or coach" - any log row with that role, including a release or demand row
- the shared-player cap and the 8-player minimum - counts over the view

Only accepted offers enter the view; pending, declined, and expired offers are history. A release/demand removes membership even though the accepted offer row remains. The schema enforces the one current-state rule that needs a constraint: ownership. One owner per team is the `teams.owner` column; one live team per owner is `teams_one_team_per_owner`; the creation `promote` row is the owner's appointment in the log. Every other membership rule is a service check, and the shared-player cap would have to be one even with a roster table.

## Lineage

A team that leaves is inactive forever and can never rejoin - there must always be a new team. History across one creator's teams is tracked by linking the new team to the previous one: **`previous_team_id`**. The chain walks backward, each link to its own predecessor if applicable.

- **Set at creation** - the `previous_team` argument on `/create team`, offered through autocomplete.
- **Eligibility comes from the transaction log**: the creator may link only a team where the log shows them as an **owner or coach at some point**. The owner's creation `promote` row is that record; a plain player who never coached cannot claim a team's history, and a former owner or coach can, even after demotion or departure - the log remembers.
- **Service-enforced rules:**
  - The previous team must be `status = 'inactive'` - you cannot fork the history of a live team.
  - The creator must have a `transaction_log` row with `role IN ('owner', 'coach')` and that team as `from_team_id` or `to_team_id`.
  - `previous_team_id` is `UNIQUE` - the lineage is a chain, not a tree; a team has at most one successor.
- **Why a column, not an array or a table**: the chain is strictly one-to-one backward. A column plus `UNIQUE` enforces the chain shape in the schema itself; an array would need maintenance on every link and a table adds nothing a self-referencing FK doesn't give. Full ancestry is one recursive query (`WITH RECURSIVE`) or a short service walk.
- **Display**: a team's profile shows its chain - "continuing Falcons, continuing Comets" - and the linked teams keep their own histories on their own rows.

## The Module - `modules/teams/`

Mirrors `modules/players/`:

- **`models.py`** - `TeamTable` (abstract; `table = "teams"`, `primary_key = "id"`, `generated = ("id", "created_at", "updated_at")`), `TeamRow`, `CreateTeam` (`kw_only=True`; `name`, `owner`, `previous_team_id`, plus a defaulted `id` so `RETURNING *` reads the generated key back); `TransactionLogTable` (abstract; `table = "transaction_log"`, `primary_key = "id"`, `generated = ("id", "at")`), `TransactionLogRow`, `CreateTransactionLog` (`kw_only=True`; `player`, `from_team_id`, `to_team_id`, `action`, `role`, `actor`, `details`); `RosterRow` (the read model over the `roster` view: `team_id`, `player`, `role`; no writes).
- **`service.py`** - `TeamService`: repositories via `get_repository`, one surface for commands. Methods: `create_pending(name, owner, previous_team_id)` (opens its own transaction - the team row and the owner's `promote` log row with `to_team_id`, `role='owner'`, `actor` = the owner), `attach_logo(team, url)`, `attach_request_message(team, snowflake)`, `approve(team)` (fetches the request message for a fresh logo URL, creates the Discord role and, if a logo was uploaded, the guild emoji, then updates the row to `approved` + `approved_at` + both snowflakes), `deny(team)`, `deactivate(team)` (status `inactive`, `inactivated_at`, deletes the role and emoji), `promote(owner, player)` (infers the owner's approved team from `teams.owner`, appends a `promote` log row with `to_team_id`, `role='coach'`, `actor` = the owner), `demote(owner, player)` (same inference, appends `demote` with `to_team_id`, `role=NULL`), `find_by_name(name)` (lower-case, live statuses), `find_by_owner(snowflake)` (live statuses), `owner(team)`, `pending()`, `lineage(team)`, `previous_teams_for(player)` (the inactive teams where the log shows them owning or coaching on either side - feeds the autocomplete), `roster(team)` (the view). Name and lineage validation lives in the service; commands layer pre-checks on top for UX.
- **`errors.py`** - `TeamUniqueViolationFormatter`, mirroring `PlayerUniqueViolationFormatter`: keyed on constraint names, `report = True` (a constraint hit means a pre-check lost a race):

| Constraint | Reply |
| --- | --- |
| `teams_one_team_per_owner` | "You already own a team." |
| `teams_lower_name_key` | "That team name is already taken." |
| `teams_previous_team_id_key` | "That team already has a successor." |

The `offers` table is in place; its model and service land with the offer, release, and demand commands.

## The Command Flow

1. **`/create team <name> [previous_team]`** - any verified member. A normal command, not a modal, so `previous_team` gets autocomplete: the inactive teams the invoker owns or the log shows them coaching. Pre-checks: name format/length, no live team already owned, name not claimed. The command ensures the invoker's `players` row, then the service inserts the pending row and the owner's `promote` log row (`to_team_id`, `role='owner'`) in one transaction. The bot then opens a modal for the optional logo - a file upload (modals support file inputs now), dismiss to skip. Once uploaded or skipped, the request embed (logo included) is posted to the pending-teams channel and its message snowflake is stored on the row - the queue the approvers work from.
2. **`/pending teams`** - approver. Lists every pending request.
3. **`/approve team <name>`** - approver. Fetches the request message, creates the team's Discord role and, if a logo was uploaded, the guild emoji from the message's attachment; flips the row to `approved` with `approved_at` and both snowflakes; DMs the owner; updates the request embed in the pending-teams channel to show the decision, and posts an announcement to the new-teams channel - the public list of approved teams.
4. **`/deny team <name> [reason]`** - approver. Flips to `denied`, DMs the owner with the reason, and updates the request embed to show the decision. The name and the owner slot are freed; the owner may re-submit.
5. **`/promote <player>`** - the owner only. The bot infers the team from `teams.owner` on the invoker's approved team - no team argument, an owner has exactly one. The command ensures the target's `players` row, then appends a `promote` log row (`to_team_id`, `role='coach'`, `actor` = the owner); the view adds the player if they were not already on the roster.
6. **`/demote <player>`** - the owner only, same inference. Appends `demote` (`to_team_id`, `role=NULL`, `actor` = the owner); they stay on the roster, and the log still shows they once coached.
7. **`/deactivate team <name> [reason]`** - approver. The team left: status `inactive`, `inactivated_at`, and the Discord role and emoji are deleted as cleanup. The row, its log, and its history stay; the name and the owner slot are freed. No reactivation - returning means a new team, linked by lineage.

Edge cases:

- **Duplicate name / duplicate owner races** - the constraint formatter owns them (above); the pre-checks catch the common case.
- **Re-submission after denial** - a new pending row with the same or a new name. A denied row is excluded from the name and owner indexes, so both are claimable again.
- **Owner creates a new team after deactivation** - `teams_one_team_per_owner` excludes inactive teams, so the slot frees; the old team's owner `promote` row makes them eligible to link it for history.
- **`/promote` or `/demote` with no approved owned team** - the inference finds nothing and the command fails with a plain message; no partial writes.
- **A deleted Discord role or emoji** - the snowflakes stay in the database, and nothing syncs. Consumers fetch first and fall back; the values are historical, not live handles.
- **Approval before the owner is verified** - the create pre-check requires the verified role, so this can't happen; approval adds no new checks.
- **Logo never uploaded** - the flow proceeds without one after the modal is dismissed; `logo_url` stays `NULL` and no emoji is created. The logo is optional by design.
- **Logo URL expired or request message gone** - approval refetches the attachment from the request message; if the message or attachment is unavailable, it skips the emoji and proceeds. `logo_url` is a cache, not the source.
- **Pending request message missing** - the row still appears in `/pending teams`; approval skips the embed edit.
- **A demoted coach links their history** - the log row survives the demote, so lineage eligibility is unaffected.
- **A pending application never completes** - the row holds its name and owner slot until an approver denies it; `/pending teams` is the list.

## Deferred - with their hooks

- **Roster rules** - the log already holds players, coaches, and owners; the shared-player cap, Window 4 selections, and the 8-player minimum are constraints that need windows and ratings to gate on. Releases and demands are transaction rows on the existing log, and the `offers` table is defined above.
- **Ownership transfer** - an update to `teams.owner` plus log rows, no schema change; it needs windows to gate on.
- **Ratings** - `team_ratings` (team, window, overall, offense, defense, confidence, snapshot data for overturns) written by the window-close batch once the model ports from `tools/`.
- **Admission windows** - joining/leaving transitions that need the window entity to time them.
- **Pod channels, hiatus, tournament fields** - columns and channels that need windows and ratings to mean anything.
- **Offer, release, and demand commands** - the `offers` table and the log actions are in place; the commands need the roster rules and window locks.
