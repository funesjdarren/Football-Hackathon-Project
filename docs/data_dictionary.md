# Match packet data dictionary

`scripts/generate_events.py` streams one synthetic match as JSON packets. This page describes what each packet contains and the rules every consumer (`read_stream.py`, `state_of_game.py`, `match_gui.py`) can rely on.

## Stream rules

- One complete packet per line: ASCII JSON, appended to `football_data/match_data.jsonl` (or `--out PATH`) **first**, then printed to stdout. Readable status lines go to stderr only.
- The file is append-only and never truncated. There is no header line: match details travel on the `KICK_OFF` packet.
- `python scripts/generate_events.py [--seed N] [--speed P] [--out PATH]`. The seed is printed at start; the same seed and code give the same packets apart from `timestamp_utc`. `--speed` is packets per real second (default 1; 0 = no delay).

## Clock and periods

- `period` is 1 or 2. Minutes are absolute: the second half restarts at 45:00 and added time keeps counting (47:12, 93:00).
- `display_time` is `MM:SS`, plus ` +N` once added time is announced (from 45:00 in the first half, 90:00 in the second) until that half ends. N is 1–4 in the first half and 2–6 in the second.
- `(period, minute*60 + second)` never goes backwards within a match.
- Four whistle packets per match, in this order: `KICK_OFF` (period 1, 00:00), `HALF_TIME` (period 1, (45+N):00 ` +N`), `SECOND_HALF` (period 2, 45:00), `FULL_TIME` (period 2, (90+N):00 ` +N`). They have the full packet shape, the referee as actor, a null `commentary_trigger`, crowd below 80, and unchanged stats.
- Teams don't switch ends: FC Alpha (`FCA`) attacks toward x = 120, Beta United (`BTU`) toward x = 0. The pitch is 120 x 80 m.

## Fields

| Field | Meaning |
|---|---|
| `event_id` | `syn-ev-000001`, ... unique within a match |
| `match_id` | `syn-<seed>`, the same on every packet of a match |
| `broadcast_metadata.timestamp_utc` | Wall-clock UTC time the packet was produced (timezone-aware ISO 8601, never seeded) |
| `broadcast_metadata.clock` | `period`, `minute`, `second`, `display_time` (see above) |
| `broadcast_metadata.scoreboard.home/away` | `team`, `score`, `team_id`. On a `GOAL_SCORED` packet the score already includes that goal |
| `broadcast_metadata.var_status` | `Inactive` or `Checking Possible Offside` (a goal always stands) |
| `broadcast_metadata.crowd_intensity_index` | 0–100. `read_stream.py` calls its agent at 80 or above |
| `broadcast_metadata.commentary_trigger` | `null`, `GOAL_SCORED`, `BOOKING_YELLOW` or `SENDING_OFF_RED` |
| `tactical_context.phase_of_play` | `Build-Up (Defensive Third)`, `Midfield Progression` or `Final Third Attack`, measured from the goal of the team that starts the event |
| `tactical_context.possession_team` / `defending_team` | The team on the ball **after** the event, and the other team. `*_team_id` give their codes |
| `tactical_context.defensive_structure` | `High Press`, `Mid Block`, `Compact Central Shape` or `Low Block (Deep Defense)` |
| `tactical_context.defensive_line_height_meters` | Mean distance of the defending team's back four from its own goal line, from the tracking frame |
| `tactical_context.pressure_distance_to_ball_meters` | Metres from the ball to the nearest defending player, from the tracking frame |
| `possession_change` | `changed`, `from_team` (who started the event), `to_team` (always `possession_team`), `reason` (`Interception`, `Tackle`, `Dribble Lost`, `Goal`, `Shot Saved`, `Shot Missed`, `Shot Blocked`, `Kick-off`; null when unchanged), `possession_id` (starts at 1, +1 per change) |
| `event.type` | `Pass`, `Shot`, `Dribble`, `Tackle`, `Foul`, or a whistle type |
| `event.actor` | The player starting the event: `id`, `name`, `role`, `pos_key`, `shirt_number`, `cards`. On whistles it is the referee (`role` `Referee`, `shirt_number` null) |
| `event.team` / `team_id` | The team starting the event (null on whistles) |
| `event.restart` | Set when this Pass restarts play: `Kick-off` (start of each half, and after a goal by the conceding team), `Goal Kick` (after a missed shot), `Keeper Ball` (after a save) or `Free Kick` (taken by the fouled player); null otherwise |
| `event.recipient_id`, `challenger_id`, `offender_id` | Set on a completed Pass, a Tackle and a Foul respectively; null otherwise |
| `event.actor_speed_kmh` | Speed of the actor (0 on whistles) |
| `event.details` | Type-specific details; `coordinates.start` is always `[x, y]`. Forwarded whole to the play-by-play agent, so it holds no ids. On a Shot: `xG` (from distance and angle; the shot is a goal with that probability) and, unless blocked, `coordinates.end` where the ball crosses the goal line. On a Foul: `card_issued` and `second_yellow` (true when a second yellow made it a red) |
| `live_match_stats.possession_pct` | Each team's share of pass attempts so far (sums to 100) |
| `live_match_stats.box_score` | Cumulative per team: `shots`, `shots_on_target`, `xg`, `passes_completed`, `passes_attempted`, `fouls` |
| `player_tracking.players[]` | `player_id`, `name`, `team`, `role` (tactical: `GK`, `LB`, ... `ST`), `x`, `y`, `speed_mps`. Nobody moves faster than 9 m/s of match time between frames; the defender nearest the ball presses goal-side of it and the next one covers. Frames are 0–50 match seconds apart (60 s before kick-off, 900 s across half-time), so `frame_rate_hz` is nominal |
| `match_metadata` (`KICK_OFF` only) | `match_id`, `generator`, `competition`, invented `venue` (`name`, `city`, `capacity`), `attendance`, `referee` (`id`, `name`), and `teams.home/away` (`name`, `team_id`, `formation`, `attacking_direction`, `lineup` with each player's ids, shirt number and roles) |

## Discipline and restarts

- A foul is committed by one of the three nearest outfield defenders. Keepers are never carded.
- Two yellows make a red: that Foul packet has `card_issued` `Red`, `second_yellow` true and `SENDING_OFF_RED`. `state_of_game.py` therefore counts one yellow fewer per second-yellow red.
- A sent-off player leaves `player_tracking` from the next packet and never acts again.
- Fouls never change possession. On a card packet `defending_team` is the offender's team; on a `GOAL_SCORED` packet it is the scoring team (possession changes on every shot).
- Restarts are ordinary Pass packets labelled by `event.restart`, never extra whistle packets. The clock waits 5–50 match seconds before one.

## How values are generated

- **Event mix:** each phase has fixed Pass / Dribble / Tackle / Foul weights (`ZONE_WEIGHTS`), with no fouls inside the box. Within 32 m of goal a Shot weight of `13 x (xG / 0.1) ^ 0.65` is added, so most shots come from good positions.
- **xG:** `1 / (1 + e^-(-0.35 + 0.85 x angle - 0.145 x distance))`, using the angle the goal mouth makes (radians) and the distance to its centre (m). About 0.46 from six yards out centrally, 0.20 from the penalty spot, 0.07 from 18 m.
- **Passes:** aimed by phase (20% of final-third passes into the box); completion `0.92 - 0.0065 per m beyond 12 m`, `-0.30` into the box, `-0.06` if the passer is pressed within 2 m, kept between 0.2 and 0.97. The ball ends within the receiver's reach before the next packet, so the ball carrier is always on the ball.
- **Clock:** 2–5 s per pass (3–6 s beyond 30 m), 2–4 s per dribble, tackle or loose ball, and 5–50 s before restarts.
- **Calibration** (means over seeds 1–300): 2.87 goals (7% of matches end 0-0), 26 shots, 9.1 on target, 2.74 xG, 21.6 fouls, 3.3 yellows, 0.12 reds; half-time around 47', full-time around 94'; about 1,480 packets, so about 25 minutes at `--speed 1`.
- **Determinism:** the same seed and code give the same match apart from `timestamp_utc`. Changing any constant changes every seed's match.

## Start of the event vs after it

| Describes the moment the event **starts** | Describes the state **after** the event |
|---|---|
| `phase_of_play`, `defensive_structure`, line height, pressure | `possession_team`, `defending_team` |
| `player_tracking` (the actor stands on the ball at `coordinates.start`) | `scoreboard`, `box_score`, `possession_pct` |
| `event.team`, `event.actor`, `details.coordinates.start` | `possession_change.to_team` |
