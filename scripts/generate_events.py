import argparse
import copy
from datetime import datetime, timezone
import json
import os
import random
import secrets
import sys
import time
import paths
from tracking_engine import TacticalTrackingEngine

SYNTHETIC_TEAMS = ["FC Alpha", "Beta United"]
TEAM_CODES = {"FC Alpha": "FCA", "Beta United": "BTU"}

HALF_END_MIN = {1: 45, 2: 90}
STOPPAGE_MIN = {1: (1, 4), 2: (2, 6)}  # announced added time per half
MARKER_TYPES = ("KICK_OFF", "HALF_TIME", "SECOND_HALF", "FULL_TIME")

# Invented names only: the clubs and players are fictional, so no real
# footballer can be matched by initial and surname
FIRST_NAMES = [
    "Liam",
    "Noah",
    "Oliver",
    "Elijah",
    "James",
    "William",
    "Lucas",
    "Kai",
    "Arlo",
    "Milo",
]
LAST_NAMES = [
    "Ashgrove",
    "Bellmont",
    "Corvane",
    "Dunholt",
    "Elswick",
    "Fairmoor",
    "Garrow",
    "Halvard",
    "Iverly",
    "Jarrell",
    "Kestley",
    "Lorrimer",
    "Merrivale",
    "Northam",
    "Orwin",
    "Penhale",
    "Quillon",
    "Rendell",
    "Stanmore",
    "Tarrow",
    "Ulvane",
    "Varden",
    "Westcott",
    "Yarrowby",
    "Zellick",
]


def generate_squad(team_name, surnames, rng):
  """11 players with the given unique surnames and unique shirt numbers."""
  squad = []
  pid = 100
  names = iter(surnames)
  numbers = iter([1] + rng.sample(range(2, 31), 10))  # keeper wears 1
  layout = [
      ("Goalkeeper", "GK", 1),
      ("Defender", "DEF", 4),
      ("Midfielder", "MID", 3),
      ("Forward", "FWD", 3),
  ]
  for role, pos_key, count in layout:
    for _ in range(count):
      pid += 1
      squad.append({
          "id": f"{TEAM_CODES[team_name]}-{pid}",
          "name": f"{rng.choice(FIRST_NAMES)[0]}. {next(names)}",
          "role": role,
          "pos_key": pos_key,
          "shirt_number": next(numbers),
          "cards": {"yellow": 0, "red": 0},
      })
  return squad


def referee_actor(name):
  """Actor for whistle packets, with every key a player actor has."""
  return {
      "id": "REF-01",
      "name": name,
      "role": "Referee",
      "pos_key": "REF",
      "shirt_number": None,
      "cards": {"yellow": 0, "red": 0},
  }


def kick_off_taker(tracking_engine, roster):
  """The centre forward, who stands on the ball at kick-off."""
  return next(
      p for p in roster if tracking_engine.players[p["id"]]["role"] == "ST"
  )


def simulate(rng):
  """Yields one full match of packets, deterministic for a given rng.

  Packets are independent copies, so they can be collected safely. The
  caller decides where they go and how fast (see main).
  """
  home_team, away_team = SYNTHETIC_TEAMS[0], SYNTHETIC_TEAMS[1]
  surnames = rng.sample(LAST_NAMES, 23)  # 22 unique players + the referee
  rosters = {
      home_team: generate_squad(home_team, surnames[:11], rng),
      away_team: generate_squad(away_team, surnames[11:22], rng),
  }
  referee = referee_actor(f"{rng.choice(FIRST_NAMES)[0]}. {surnames[22]}")

  tracking_engine = TacticalTrackingEngine(
      home_team, away_team, rosters[home_team], rosters[away_team], rng=rng
  )

  score = {home_team: 0, away_team: 0}
  live_stats = {
      home_team: {
          "shots": 0,
          "shots_on_target": 0,
          "xg": 0.0,
          "passes_completed": 0,
          "passes_attempted": 0,
          "fouls": 0,
      },
      away_team: {
          "shots": 0,
          "shots_on_target": 0,
          "xg": 0.0,
          "passes_completed": 0,
          "passes_attempted": 0,
          "fouls": 0,
      },
  }

  attacking_team = home_team
  defending_team = away_team

  ball_x, ball_y = 60.0, 40.0
  current_actor = kick_off_taker(tracking_engine, rosters[home_team])
  phase = "Midfield Progression"

  # Period state machine: the clock is absolute match seconds (the 2nd half
  # restarts at 45:00), added time is announced once the half's 45/90 minutes
  # are up, and a whistle packet marks each kick-off and the end of each half
  period, clock_s = 1, 0
  stoppage = None
  whistle = "KICK_OFF"
  event_counter = 0

  while True:
    event_counter += 1
    half_end = HALF_END_MIN[period] * 60
    if stoppage is None and clock_s >= half_end:
      stoppage = rng.randint(*STOPPAGE_MIN[period])
    if whistle is None and stoppage is not None and clock_s >= half_end + stoppage * 60:
      whistle = "HALF_TIME" if period == 1 else "FULL_TIME"
    minute, second = divmod(clock_s, 60)
    clock_str = f"{minute:02d}:{second:02d}" + (
        f" +{stoppage}" if stoppage is not None else ""
    )

    if whistle:
      # Whistle packets have the full packet shape so no consumer breaks,
      # and change no stats; the kick-off taker stands on the centre spot
      event_type = whistle
      kicking_off = whistle in ("KICK_OFF", "SECOND_HALF")
      if kicking_off:
        phase = "Midfield Progression"
      defensive_block = "Mid Block"
      defensive_line_height_m = 35.0
      closest_defender_dist_m = 9.15
      crowd_intensity = rng.randint(60, 75)
      var_status = "Inactive"
      commentary_flag = None
      actor, actor_speed_kmh = referee, 0.0
      payload_details = {"coordinates": {"start": [ball_x, ball_y]}}
      player_tracking_frame = tracking_engine.update_frame(
          ball_x=ball_x,
          ball_y=ball_y,
          possession_team=attacking_team,
          active_actor_id=current_actor["id"] if kicking_off else None,
          defensive_block=defensive_block,
          kick_off=kicking_off,
      )
      next_actor = current_actor
    else:
      # 1. Determine Pitch Phase & Defensive Shape
      if ball_x < 35:
        phase = "Build-Up (Defensive Third)"
        defensive_block = "High Press"
        defensive_line_height_m = round(rng.uniform(45.0, 55.0), 1)
      elif ball_x < 75:
        phase = "Midfield Progression"
        defensive_block = rng.choice(["Mid Block", "Compact Central Shape"])
        defensive_line_height_m = round(rng.uniform(30.0, 42.0), 1)
      else:
        phase = "Final Third Attack"
        defensive_block = "Low Block (Deep Defense)"
        defensive_line_height_m = round(rng.uniform(12.0, 22.0), 1)

      # 2. Event Selection
      if ball_x >= 75:
        event_type = rng.choices(
            ["Shot", "Pass", "Dribble", "Tackle", "Foul"],
            weights=[25, 40, 15, 15, 5],
        )[0]
      else:
        event_type = rng.choices(
            ["Pass", "Dribble", "Tackle", "Foul"], weights=[70, 15, 10, 5]
        )[0]

      crowd_intensity = rng.randint(35, 55)
      var_status = "Inactive"
      commentary_flag = None

      start_x, start_y = ball_x, ball_y
      actor = current_actor
      actor_speed_kmh = round(rng.uniform(12.0, 26.5), 1)
      closest_defender_dist_m = round(rng.uniform(0.8, 6.5), 1)

      # 3. Simulate Event Mechanics & Movement
      if event_type == "Pass":
        live_stats[attacking_team]["passes_attempted"] += 1
        end_x = min(118.0, max(2.0, start_x + rng.uniform(-8, 28)))
        end_y = min(78.0, max(2.0, start_y + rng.uniform(-22, 22)))

        outcome = "Complete" if rng.random() > 0.18 else "Incomplete"
        recipient = rng.choice(
            [p for p in rosters[attacking_team] if p["id"] != current_actor["id"]]
        )

        if outcome == "Complete":
          live_stats[attacking_team]["passes_completed"] += 1
          ball_x, ball_y = round(end_x, 1), round(end_y, 1)
          next_actor = recipient
        else:
          crowd_intensity += 15
          attacking_team, defending_team = defending_team, attacking_team
          ball_x, ball_y = round(end_x, 1), round(end_y, 1)
          next_actor = rng.choice(rosters[attacking_team])

        payload_details = {
            "coordinates": {
                "start": [start_x, start_y],
                "end": [round(end_x, 1), round(end_y, 1)],
            },
            "outcome": outcome,
            "recipient": recipient if outcome == "Complete" else None,
            "pass_speed_kmh": round(rng.uniform(35.0, 75.0), 1),
        }

      elif event_type == "Shot":
        live_stats[attacking_team]["shots"] += 1
        shot_xg = round(rng.uniform(0.03, 0.45 if ball_x < 105 else 0.75), 2)
        live_stats[attacking_team]["xg"] = round(
            live_stats[attacking_team]["xg"] + shot_xg, 2
        )

        crowd_intensity = rng.randint(75, 95)
        outcome = rng.choices(
            ["Saved", "Blocked", "Missed", "Goal"], weights=[35, 30, 25, 10]
        )[0]

        if outcome in ["Saved", "Goal"]:
          live_stats[attacking_team]["shots_on_target"] += 1

        if outcome == "Goal":
          score[attacking_team] += 1
          crowd_intensity = 100
          commentary_flag = "GOAL_SCORED"
          if rng.random() < 0.15:
            var_status = "Checking Possible Offside"

        payload_details = {
            "coordinates": {"start": [start_x, start_y]},
            "outcome": outcome,
            "xG": shot_xg,
            "shot_speed_kmh": round(rng.uniform(70.0, 115.0), 1),
        }

        attacking_team, defending_team = defending_team, attacking_team
        ball_x, ball_y = 10.0, 40.0
        next_actor = next(
            p for p in rosters[attacking_team] if p["pos_key"] == "GK"
        )

      elif event_type in ["Tackle", "Foul"]:
        opponent = rng.choice(rosters[defending_team])
        won_ball = rng.random() > 0.50

        if event_type == "Foul":
          live_stats[defending_team]["fouls"] += 1
          crowd_intensity += 25
          card_issued = rng.choices(
              ["None", "Yellow", "Red"], weights=[75, 23, 2]
          )[0]
          if card_issued == "Yellow":
            opponent["cards"]["yellow"] += 1
            commentary_flag = "BOOKING_YELLOW"
          elif card_issued == "Red":
            opponent["cards"]["red"] += 1
            commentary_flag = "SENDING_OFF_RED"

          payload_details = {
              "coordinates": {"start": [start_x, start_y]},
              "fouled_player": current_actor["name"],
              "offender": opponent["name"],
              "card_issued": card_issued,
          }
          next_actor = current_actor
        else:
          payload_details = {
              "coordinates": {"start": [start_x, start_y]},
              "challenger": opponent["name"],
              "outcome": "Ball Won" if won_ball else "Unsuccessful",
          }
          if won_ball:
            attacking_team, defending_team = defending_team, attacking_team
            next_actor = opponent
          else:
            next_actor = current_actor

      else:  # Dribble
        actor_speed_kmh = round(rng.uniform(22.0, 32.5), 1)
        end_x = min(118.0, max(2.0, start_x + rng.uniform(3, 14)))
        end_y = min(78.0, max(2.0, start_y + rng.uniform(-8, 8)))
        payload_details = {
            "coordinates": {
                "start": [start_x, start_y],
                "end": [round(end_x, 1), round(end_y, 1)],
            },
            "success": rng.random() > 0.35,
        }
        ball_x, ball_y = round(end_x, 1), round(end_y, 1)
        next_actor = current_actor

      # 4. Synchronize Tracking Frame with Resolved Position
      player_tracking_frame = tracking_engine.update_frame(
          ball_x=ball_x,
          ball_y=ball_y,
          possession_team=attacking_team,
          active_actor_id=current_actor["id"],
          defensive_block=defensive_block,
      )

    # 5. Calculate Possession %
    total_passes = (
        live_stats[home_team]["passes_attempted"]
        + live_stats[away_team]["passes_attempted"]
    )
    home_poss = (
        round((live_stats[home_team]["passes_attempted"] / total_passes) * 100)
        if total_passes > 0
        else 50
    )
    possession_share = {home_team: home_poss, away_team: 100 - home_poss}

    # 6. Form Broadcast Payload
    payload = {
        "event_id": f"syn-ev-{event_counter:06d}",
        "broadcast_metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "clock": {
                "period": period,
                "display_time": clock_str,
                "minute": minute,
                "second": second,
            },
            "scoreboard": {
                "home": {"team": home_team, "score": score[home_team]},
                "away": {"team": away_team, "score": score[away_team]},
            },
            "var_status": var_status,
            "crowd_intensity_index": crowd_intensity,
            "commentary_trigger": commentary_flag,
        },
        "tactical_context": {
            "phase_of_play": phase,
            "possession_team": attacking_team,
            "defending_team": defending_team,
            "defensive_structure": defensive_block,
            "defensive_line_height_meters": defensive_line_height_m,
            "pressure_distance_to_ball_meters": closest_defender_dist_m,
        },
        "event": {
            "type": event_type,
            "actor": actor,
            "actor_speed_kmh": actor_speed_kmh,
            "details": payload_details,
        },
        "live_match_stats": {
            "possession_pct": possession_share,
            "box_score": live_stats,
        },
        "player_tracking": {
            "frame_rate_hz": 0.5,
            "pitch_units": "meters (120x80)",
            "players": player_tracking_frame,
        },
    }

    current_actor = next_actor
    yield copy.deepcopy(payload)  # stats and cards keep changing after this

    # Advance the clock, or move on to the next phase of the match
    if whistle == "FULL_TIME":
      return
    if whistle == "HALF_TIME":
      period, clock_s, stoppage = 2, 45 * 60, None
      whistle = "SECOND_HALF"
      attacking_team, defending_team = away_team, home_team
      ball_x, ball_y = 60.0, 40.0
      current_actor = kick_off_taker(tracking_engine, rosters[away_team])
    elif whistle:
      whistle = None  # the kick-off pass happens at the same clock time
    else:
      clock_s += rng.randint(2, 6)
      if stoppage is not None:
        # Never run past the end of added time: the whistle lands exactly on it
        clock_s = min(clock_s, half_end + stoppage * 60)


def status_line(packet):
  """The readable one-line summary printed to stderr for each packet."""
  meta, tactics = packet["broadcast_metadata"], packet["tactical_context"]
  board = meta["scoreboard"]
  return (
      f"[{meta['clock']['display_time']}]"
      f" {board['home']['score']}-{board['away']['score']}"
      f" | {tactics['possession_team']} ({packet['event']['type']})"
      f" vs {tactics['defending_team']} [{tactics['defensive_structure']}]"
  )


def main():
  parser = argparse.ArgumentParser(
      description="Streams one synthetic football match as JSON packets."
  )
  parser.add_argument(
      "--seed", type=int, help="replay an exact match (default: a random seed)"
  )
  parser.add_argument(
      "--speed",
      type=float,
      default=1.0,
      help="packets per real second; 0 means no delay (default: 1)",
  )
  parser.add_argument(
      "--out",
      default=os.path.join(paths.DATA_DIR, "match_data.jsonl"),
      help="file to append packets to (default: football_data/match_data.jsonl)",
  )
  args = parser.parse_args()
  seed = secrets.randbelow(1_000_000) if args.seed is None else args.seed

  os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
  print(
      f"=== BROADCAST STREAM INITIATED: {SYNTHETIC_TEAMS[0]} vs"
      f" {SYNTHETIC_TEAMS[1]} (replay with --seed {seed}) ===",
      file=sys.stderr,
  )

  for packet in simulate(random.Random(seed)):
    line = json.dumps(packet)
    # Append to the file first, then stdout, so the file is never behind the pipe
    with open(args.out, "a") as f:
      f.write(line + "\n")
    print(line, flush=True)  # read_stream.py reads this
    print(status_line(packet), file=sys.stderr, flush=True)
    if args.speed > 0:
      time.sleep(1 / args.speed)


if __name__ == "__main__":
  main()
