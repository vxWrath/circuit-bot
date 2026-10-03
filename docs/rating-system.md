# FF Circuit - Rating System

Every team in FF Circuit carries a persistent rating. The rating decides pods, seeds the tournament, and follows the team from one circuit to the next. Nothing resets.

Behind every displayed rating sits a hidden **true rating** - the model's honest measure of a team's strength. The two differ only when a team has missed games (see [Skipping Games](#skipping-games)).

Ratings update once per window - a single batch at the window's close, the same moment pods reshuffle. The numbers in this document are illustrative; the exact values are set during calibration (see [Calibration](#calibration)).

## The Number

A team's rating is a single number where **0 is the league average**:

- A team at **0** is exactly average.
- A team at **+50** is expected to beat an average team by about a touchdown.
- A team at **-50** is expected to lose to an average team by about a touchdown.

A rating is how much better than average a team is, measured in points. Most teams fall between roughly -150 and +150.

## Two Halves: Offense and Defense

Every rating has two halves that add up to the overall: an **offense number** and a **defense number**. A team that wins 55-49 and a team that wins 21-28 won by the same margin - but the wins say opposite things.

| Team | Offense | Defense | Overall | What it means |
| --- | --- | --- | --- | --- |
| The Renegades | +60 | -20 | +40 | Scores a lot, gives up a lot |
| The Vipers | +10 | +50 | +60 | Defense-first grinder |

The Renegades win shootouts; the Vipers win slogs. Same 7-point win, opposite information - so a game moves the two halves differently, even when the margin is identical.

## How Games Move the Numbers

Before every game, the model makes a prediction: how many points each team is expected to score and allow, given both teams' ratings and their offense and defense halves. After the game, it compares reality to the prediction.

- The bigger the surprise, the bigger the move.
- No single game can swing a rating past a cap - one wild game can't undo a season.
- Expected results are cheap, surprises are expensive. A favorite that wins barely moves; a favorite that loses drops hard. An underdog that loses barely moves; an underdog that wins jumps.

Example: the Renegades are heavy favorites against the Comets. When the Renegades win by the expected margin, both ratings barely move - the model already knew. When the Comets pull the upset, the Comets jump and the Renegades drop - the model learned something.

## Confidence and Provisional Teams

Every team also carries a **confidence** level: how sure the model is about the rating.

- More games mean more confidence.
- A veteran team with a long history moves in small steps.
- A new team has almost no confidence and moves in leaps until it proves itself.

Teams with low confidence are marked **provisional**.

Example: the Huskies join the league at the bottom, starting at -160 with a provisional tag. Three 20-point wins rocket them up: -160 to -60 to +20 to +70 - each leap a little smaller as confidence grows. The veteran Titans, sitting at +150 with a long history, win the same three games and gain +6 total. Same results, different step sizes - the model is certain about the Titans and still learning the Huskies.

Confidence grows with every game played and shrinks with every game missed.

## New and Returning Teams

Every new team starts at **the bottom of the league** with a provisional tag, then works its way up. The same applies to teams that left or were disbanded and return.

Leaving and rejoining is never a reset. A team with a negative rating cannot take a circuit off and come back to average - it comes back to the bottom and climbs again. The only way to keep a spot is a hiatus.

## Skipping Games

A missed game never touches the **true rating** - the model does not punish a rating for a game that never happened. (The team's confidence does shrink, as it always does when games go unplayed.)

What a missed game touches is the **displayed number**. Each missed game applies a penalty to it - about -15 points, restored by playing. The displayed number is what decides pods and tournament seeding, so a team that misses games drops: it falls to a lower pod, and its tournament seed drops with it.

Example: the Wraiths show +80. They miss a game and drop to +65, with the penalty shown openly on their rating: **+65 (-15: 1 missed)**. They spend the next window in a lower pod.

**The grind.** In the lower pod, the model still knows how strong the Wraiths really are - it predicts their games using the true rating. Beating weaker opponents was expected, so their wins gain almost nothing. They are barely moving - and they must play two games to restore every one missed game. Beating up on a lower pod is boring, gains nothing, and takes time: that is the punishment.

Every game played - win or lose - restores half of one missed game's penalty. The optional third game restores it faster: one more reward for active teams. A team that misses two games needs about four games to climb back - the grind grows with the misses.

**Why podmates don't suffer.** When a penalized team smashes a weaker pod 40-0, the model saw it coming - the true rating predicted exactly that. The podmate's rating barely moves. The punishment lands on the team that missed games, never on the teams around it.

**But losing in the lower pod is costly.** If a supposedly strong team loses to the weak opponents it was demoted to face, the model is genuinely surprised - and the true rating takes a real hit. A punished team cannot coast; it has to prove it belongs above that pod.

Tournament qualification uses the same displayed number, so a missed game costs a team both its pod and its seed - until it plays its way back.

The rating system is the gentle half of the inactivity rules - the harsh half is the league's own: a team that makes no attempt to schedule is disbanded.

## Hiatus

A team on hiatus does not play, so the model has nothing new to measure. Its rating **floats**: it moves with the average movement of the teams around it - the pod it would have been in - and takes a small penalty. A team on hiatus is excused from playing, so it takes no missed-game penalties - the float and the small penalty are the whole cost.

Example: the Titans at +40 take a two-window hiatus. Their pod-mates average -10 over that stretch. The Titans float to +30, then take a small penalty: they return at +25. Roughly in place, slightly punished.

## Games That Don't Count

A game with a **combined score under 45** was not a real game - it is a forfeit convention. The model skips it entirely: nobody gains, nobody loses. Unearned wins cannot exist, in the ratings or anywhere else.

## Every Real Game Counts the Same

The model does not play favorites. A tournament game counts the same as a Window 1 game. A rematch counts the same as a first meeting. A game against a mathematically eliminated team counts the same for both sides. Any two teams that complete a game move the ratings - always.

## Player Impact Ratings

Every game also records **who actually stepped onto the field**. Across every game a player appears in, the model learns their impact: when this player shows up, their team performs about this much better or worse than expected.

Because players can play for multiple teams, the model can tell the difference between a good player and a good team. Each player carries their own number on the same 0-centered scale, shown on a public leaderboard.

A team's expected strength is its **team number plus the impact of whoever is likely to show up**. If a team's stars stop showing, its number falls - pods reflect who actually plays, not who is merely rostered.

Example: Razor plays for both the Vipers and the Comets. Across every game, teams perform about +6 better than expected when Razor is on the field - Razor's number is +6. The Vipers' team number is +10, and their likely lineup's players add up to +14 - the Vipers' expected strength is +24.

## Keeping Zero at Zero

After every window's updates, all ratings shift together by the same amount so the league average sits at exactly 0. No ordering changes - the numbers simply keep meaning "above average" and "below average" honestly as the league grows and changes.

## Where the Ideas Come From

The rating system is a custom build from proven parts:

| Idea | Source |
| --- | --- |
| Confidence - how sure the model is about a team | Chess's Glicko system |
| Offense and defense halves | Football power ratings |
| Player impact | Basketball's adjusted plus-minus |
| Displayed number drops while the true rating stays | Rank decay in competitive games (League of Legends, Overwatch) |

What is custom is how these are stitched to the FF Circuit structure: pods, windows, chosen opponents, multi-team rosters, hiatus, and the 0-centered scale.

## Calibration

The numbers in this document are illustrative. The exact values - step sizes, the movement cap, the missed-game penalty and its restoration rate, the hiatus penalty - will be set by testing the model against roughly two thousand historical Football Fusion games before launch. Success is measured by the league's own standard: how well ratings predict game outcomes.
