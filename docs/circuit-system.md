# FF Circuit - System Overview

FF Circuit is a Football Fusion league (8v8, arcade-style football) organized around four principles:

- **Continuity** - the league operates without breaks. Competitive cycles ("circuits") run back to back, with no offseason or downtime between them.
- **Freedom** - players may play for multiple teams at once, and teams may enter or leave the league mid-circuit.
- **Fairness** - every team carries a persistent rating, and all ranking and seeding derive from measured performance.
- **Opportunity** - teams choose their own opponents within their pod, rather than playing a predetermined schedule.

The result is a competitive system that compresses a full season into roughly three weeks, where every game affects a team's standing.

## Why FF Circuit

Existing leagues - including the main league (LFG) and especially side leagues - share the same structural problems:

- **Slow seasons.** A full season takes one to two months, with long stretches of dead time between games and between seasons. Engagement decays during the wait. Especially during the playoffs, where eliminated teams are just sitting around, waiting for the next season to start.
- **Gigantic playoff brackets.** When half the league qualifies for the postseason, the regular season stops mattering - most games carry little at stake, and mid-tier teams reach the playoffs.
- **Forfeits are rampant.** With predetermined schedules teams are forced to deal with poor quality coaches, and worse, a forfeit awards a win that was never earned on the field - standings and seeding inflate without a game being played.
- **Management quality varies.** Slow rulings, inconsistent enforcement, and poor decisions erode trust - and side leagues suffer this worst of all.
- **No uniqueness.** Every league runs the same structure: divisions, predetermined schedules, a standard playoff format. There is nothing to differentiate one from the next.

FF Circuit removes each of these problems at the structural level:

- **A full competitive cycle takes roughly three weeks**, including the tournament. Circuits run back to back - there is no dead time, and the next cycle starts before the last one even finishes.
- **Qualifying for the tournament is the achievement.** The field is small (4-16 teams), so every regular season game affects who advances.
- **Unearned wins cannot exist.** Matchups are never predetermined - teams choose their opponents, and ratings move only on games actually played. A team that makes no attempt to schedule affects no one but itself, and is disbanded when the window closes. A team that cannot field enough players simply reschedules or finds a different opponent - no forfeit, no penalty.
- **Administration is consistent by design.** FF Circuit is operated by established, experienced members of the community, and the bot automates scheduling, records, penalties, and roster locks - decisions are built into the structure rather than made case by case.
- **The structure is genuinely different.** Pods instead of divisions, chosen opponents instead of fixed schedules, multi-team rosters, rolling circuits - nothing else in the community operates this way.

## Playing Rules

**Angle glitching is permitted.** This is a deliberate policy decision and is expected to be the league's most controversial rule. FF Circuit treats the game as it is played: all mechanics available in Football Fusion are legal, glitches included. This position is policy, not oversight.

## Different from Traditional Leagues

| Traditional league | FF Circuit |
| --- | --- |
| One team per player | Players may join multiple teams, up to the Window 4 locks |
| Predetermined opponents | Teams choose their opponents within their pod |
| Seasons lasting 1-2 months | Circuits running back to back, 16-18 days each |
| Half the league reaches the playoffs | A small, elite tournament (scaling with league size, capped at 16) |
| A missed season means a month-long wait | The next circuit begins within days |

## The League

The league is a single, ongoing system. Circuits run back to back, and team ratings carry from one circuit to the next. Nothing resets.

## The Circuit System

A **circuit** is the league's equivalent of a season: **four windows, four days each** - roughly eight games and 16-18 days per circuit.

## The Window

A **window** is a four-day period. Every team plays **two games** per window, with an optional third that also counts toward rating, rewarding active teams. At the end of each window, ratings update and pods reshuffle. A window is conceptually similar to a series in a traditional league.

A circuit, day by day:

| Phase | Length | Description |
| --- | --- | ---|
| Window 1 | length of tournament, hopefully ~4 days | teams join their pods and play 2 games; ratings update |
| Window 2 | days 5-8 | new pods; 2 more games; ratings update |
| Window 3 | days 9-12 | final window for teams to join or leave |
| break | 12-18 hours | records finalized; Window 4 roster selections are made |
| Window 4 | days 13-16 | final games; the top teams by rating advance to the tournament |
| break | 12-18 hours | records finalized; tournament roster selections are made; preparations next circuit begin |
| Tournament/next circuit | | the top teams by rating advance to the tournament and play in a double-elimination bracket |

The tournament then runs during Window 1 of the next circuit, and the cycle repeats.

## The Pod

A **pod** is a group of 8 teams at a similar rating - the circuit's equivalent of a division. The defining mechanic:

- Teams **choose their two opponents** from within their pod. Scheduling is left entirely to the teams.
- A team **cannot play the same opponent twice** in a window.

Pods keep teams competing against opponents near their level, but because pods reshuffle every window, a team is never locked into the same group for long - two or three games, then a new set of opponents.

Pods are numbered by rating from the top down - **Pod 1** is the strongest pod.

Because the league size is rarely a multiple of eight, the **lowest pod** absorbs the remainder: it may hold anywhere from 8 to 15 teams. If the lowest pod shrinks to 7 teams, it merges with the pod above it to form a new lowest pod. If it grows to 16 teams, it splits into two pods of eight.

## Scheduling Games

At the start of each window, the bot creates a **Discord channel for every pod**, visible only to the coaches and owners of its member teams. The channel serves as the pod's workspace for the window's four days.

Opponents are found through bot commands:

- **General request** - a team announces availability to the entire pod, including the times it can play.
- **Specific request** - a team challenges a particular opponent. The challenged team may accept or decline.

Once a request is accepted, the bot creates a **thread** within the pod channel, restricted to the two teams' coaches and owners. There, the teams agree on a time: one side submits a **proposed game time** by command, and the other accepts or declines it. If declined, the teams propose another time. Both sides may also **mutually agree to cancel** the game and seek different opponents.

After the game, one team **submits the JSON file generated by Football Fusion** (score and stats included). The opponent **confirms or disputes** the report. On confirmation, all records update and the thread closes. On dispute, a moderator joins the thread to resolve it.

If a team cannot field enough players at the agreed time, it is not a forfeit - the teams reschedule or find different opponents. No penalty applies; repeated offenders are investigated.

A team that makes **no attempt to schedule its games** in a window is disbanded when the window closes. This is removal, not punishment - disbanded teams re-enter under the same rules as a departing team: disbanded in Window 1 or 2, an owner may return the next circuit.

## The Rating

Every team carries a rating across circuits - the league's cumulative record of team strength. The rating system is custom-built and calibrated against historical Football Fusion game data. It does several things that simple standings cannot:

- **Consistency outranks streaks.** A team proven strong across multiple circuits outranks a team that performed well for a single window. Reputation is earned circuit over circuit.
- **Every game affects the rating.** With only eight games per circuit, there is no room to coast.
- **Margin of victory matters, within limits.** Blowouts count for more than narrow wins, but the weight is capped so running up the score against weak opponents is not rewarded.
- **Inactivity is penalized.** Missed games carry escalating penalties - a team missing four or five games in a circuit will see its rating fall sharply.
- **New teams start at the bottom.** A team entering the league - new or returning - starts at the lowest rating in the league and works its way up. Multiple circuits are how a new team reaches the top.

The rating model is currently in development; it will be validated against historical game data before launch.

## The Tournament

After Window 4, the **top teams** in the league enter the **Circuit Tournament** - a double-elimination bracket to crown the champion. The field scales with the league:

- **4 teams** by default
- **8 teams** once the league reaches 64 teams
- **16 teams** at 128 teams - the cap, regardless of further growth

- The tournament runs during **Window 1 of the next circuit**, so tournament teams start their next circuit in Window 2.
- To be eligible, a team must play **a minimum of seven games** in a circuit to qualify for the tournament, regardless of when it joined. A team joining in Window 3 can play at most six games, so it cannot qualify for that circuit's tournament - by design, it builds toward the next circuit. A team that played in the previous circuit's tournament starts in Window 2 and needs only **five games**.

## Teams & Players

The roster rules are built around flexibility, tightening progressively over the course of a circuit:

| Phase | Teams | Players |
| --- | --- | --- |
| Windows 1-3 | May join or leave | Unrestricted play - no game limit |
| Window 4 | Locked | Select 3 teams to play for |
| Tournament | Locked | Select 1 of the 3 |

**The open weeks (Windows 1-3)**

- Teams may join or leave. A team joining mid-circuit is admitted between windows - a team that signs up during a window begins play at the start of the next window. In reverse, a team that requests removal stays in the league until the window closes - it plays out the remainder of the window, then leaves. A team that leaves in Window 2 may return the following circuit. A team that leaves in Window 3 is out for the remainder of the circuit *and* the next one.
- Players may play for any team, at any time. There is no limit on games played.

**Window 4 - the locks**

- Each player selects **three teams** to play for in the final window. Each selected team must have had the player on its roster at some point during the first three windows.
- Rosters are **frozen** - no signings, releases, or demands.
- Selections are made during the 12-18 hour break between Windows 3 and 4, while the league finalizes all records. If a player misses the deadline, the bot selects automatically: the player's top three teams by games played, with time on roster as the tiebreaker. Players do not have to be on a team's current roster to select that team.

**The tournament**

- Each player selects **one team** - it must be one of the three from Window 4. That is the only team the player may represent in the tournament.

**Scope of these rules**

The roster rules apply only to teams **still in contention** for the tournament (or already in it). A team is **mathematically eliminated** once it can no longer reach a tournament-qualifying rating by the end of Window 4. From that point, players may play for it freely - it does not consume one of their selections. Players competing in the tournament may also play for non-tournament teams, which are simply starting Window 1 of the next circuit.

**Roster count**

A team in contention must have **eight players rostered** entering Window 4; otherwise it is **frozen**. The same applies if its roster drops below eight during Window 4. Frozen teams remain frozen until they fill their roster with newcomers. If a team has fewer than eight players when the tournament begins, it forfeits its place and the bid passes to the next-ranked team.

**Shared players**

No two teams may share more than **four players**. A signing that would create a fifth shared player between two rosters will not process. This applies to all teams, in contention or not.

**Newcomers**

A newcomer is a player who joins the league in Window 4, or a player whose tournament-eligible teams all disbanded or were frozen entering Window 4. Newcomers select **one** Window 4 tournament-eligible team to play for, and are locked to that team for Window 4 and the tournament (if applicable).

**Hiatus**

A team that wants a break without leaving the league and starting over may request a **hiatus** instead of removal:

- A hiatus lasts a **minimum of one window and a maximum of one circuit**.
- The team **does not play** during the hiatus - it sits out of pods and is exempt from scheduling requirements and missed-game penalties.
- The team's rating **stays roughly in place**: it moves with the ratings of the teams around it, holding its placement in the league, with a small penalty applied.
- Requests are submitted by ticket and **approved or denied manually** by staff.

For example: a team whose quarterback is away for a week puts in a ticket, sits out two windows, and returns with its rating roughly intact.

## Owners & Coaches

**Owners**

- One owner per team, one team per owner.
- Ownership **cannot transfer** after Window 3 closes.
- An owner's own team always counts as one of the owner's three Window 4 selections.
- An owner's tournament commitment is **always their own team** - even if it misses the tournament. Ownership carries that commitment.
- Owners count toward the shared player cap.

**Coaches**

Coaches hold the same abilities as owners - signing and releasing players, scheduling games - but none of the ownership obligations, aside from the shared player cap. A player may coach multiple teams.

## Awards

Awards are based on **per-game averages** rather than totals, since players may play an unlimited number of games per circuit and totals would reward volume of play. Eligibility requires **eight games** (or **six** for players who made the previous tournament). Awards are limited to players who competed in the top pods - Pod 1, Pod 2, and so on down from the strongest.

## Known Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Tournament exclusivity may disengage teams eliminated early | Circuits run back to back - a missed tournament costs days, not months |
| Multi-team play could enable collusion or win-trading | Shared player cap between rosters; progressive roster locks; all scheduling recorded by the bot |
| Deliberate inactivity to manipulate rating or seeding | Escalating missed-game penalties; no-attempt teams are disbanded; ratings carry across circuits |
| Eight games per circuit makes rankings noisy | Ratings persist across circuits; ranking favors proven consistency over streaks; model validated against historical data |
| The glitching policy will draw criticism | It is a deliberate, documented policy decision (see Playing Rules) |
| Disputed game reports | Dual confirmation of every submission; moderators resolve disputes in-thread |

## Success Metrics

The league tracks, per circuit:

- **Completion** - share of scheduled games played and confirmed on time
- **Participation** - share of teams making a scheduling attempt each window; the disband rate
- **Retention** - share of teams and players returning circuit over circuit
- **Integrity** - dispute rate per reported game; repeated no-show investigations opened
- **Rating accuracy** - how well ratings predict game outcomes, backtested against historical data
