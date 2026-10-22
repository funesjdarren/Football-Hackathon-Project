from datetime import datetime, timezone
import json
import os
import random
import sys
import time
from tracking_engine import TacticalTrackingEngine

SYNTHETIC_TEAMS = ["FC Alpha", "Beta United"]
FIRST_NAMES = [
    "Liam",
    "Noah",
    "Oliver",
    "Elijah",
    "James",
    "William",
    "Lucas",
    "Kai",
    "Declan",
    "Cole",
]
LAST_NAMES = [
    "Smith",
    "Johnson",
    "Brown",
    "Garcia",
    "Miller",
    "Davis",
    "Saka",
    "Rice",
    "Foden",
    "Palmer",
]


def generate_squad(team_name):
  squad = []
  pid = 100
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
          "id": f"{team_name[:3].upper()}-{pid}",
          "name": (
              f"{random.choice(FIRST_NAMES)[0]}. {random.choice(LAST_NAMES)}"
          ),
          "role": role,
          "pos_key": pos_key,
          "shirt_number": random.randint(1, 99),
          "cards": {"yellow": 0, "red": 0},
      })
  return squad


def run_broadcast_soccer_stream():
  home_team, away_team = SYNTHETIC_TEAMS[0], SYNTHETIC_TEAMS[1]
  rosters = {
      home_team: generate_squad(home_team),
      away_team: generate_squad(away_team),
  }

  tracking_engine = TacticalTrackingEngine(
      home_team, away_team, rosters[home_team], rosters[away_team]
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

  ball_x, ball_y = 35.0, 40.0
  current_actor = next(
      p for p in rosters[attacking_team] if p["pos_key"] == "DEF"
  )

  minute, second = 0, 0
  stoppage_time = 0
  period = 1
  p1_stoppage_set = False
  p2_stoppage_set = False
  event_counter = 1

  os.makedirs("football_data", exist_ok=True)
  save_path = os.path.join("football_data", "match_data.jsonl")

  print(
      f"=== BROADCAST STREAM INITIATED: {home_team} vs {away_team} ===",
      file=sys.stderr,
  )

  while minute < 90 + stoppage_time:
    event_counter += 1

    # Stoppage time handling and period transitions
    if minute >= 45 and period == 1:
      if not p1_stoppage_set:
        stoppage_time = random.randint(2, 4)
        p1_stoppage_set = True
      elif minute >= 45 + stoppage_time:
        # Move to 2nd half
        period = 2
        stoppage_time = 0
        minute = 45
        second = 0

    if minute >= 90 and period == 2 and not p2_stoppage_set:
      stoppage_time = random.randint(3, 6)
      p2_stoppage_set = True

    # 1. Determine Pitch Phase & Defensive Shape
    if ball_x < 35:
      phase = "Build-Up (Defensive Third)"
      defensive_block = "High Press"
      defensive_line_height_m = round(random.uniform(45.0, 55.0), 1)
    elif ball_x < 75:
      phase = "Midfield Progression"
      defensive_block = random.choice(["Mid Block", "Compact Central Shape"])
      defensive_line_height_m = round(random.uniform(30.0, 42.0), 1)
    else:
      phase = "Final Third Attack"
      defensive_block = "Low Block (Deep Defense)"
      defensive_line_height_m = round(random.uniform(12.0, 22.0), 1)

    # 2. Event Selection
    if ball_x >= 75:
      event_type = random.choices(
          ["Shot", "Pass", "Dribble", "Tackle", "Foul"],
          weights=[25, 40, 15, 15, 5],
      )[0]
    else:
      event_type = random.choices(
          ["Pass", "Dribble", "Tackle", "Foul"], weights=[70, 15, 10, 5]
      )[0]

    crowd_intensity = random.randint(35, 55)
    var_status = "Inactive"
    commentary_flag = None

    start_x, start_y = ball_x, ball_y
    actor_speed_kmh = round(random.uniform(12.0, 26.5), 1)
    closest_defender_dist_m = round(random.uniform(0.8, 6.5), 1)

    # 3. Simulate Event Mechanics & Movement
    if event_type == "Pass":
      live_stats[attacking_team]["passes_attempted"] += 1
      end_x = min(118.0, max(2.0, start_x + random.uniform(-8, 28)))
      end_y = min(78.0, max(2.0, start_y + random.uniform(-22, 22)))

      outcome = "Complete" if random.random() > 0.18 else "Incomplete"
      recipient = random.choice(
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
        next_actor = random.choice(rosters[attacking_team])

      payload_details = {
          "coordinates": {
              "start": [start_x, start_y],
              "end": [round(end_x, 1), round(end_y, 1)],
          },
          "outcome": outcome,
          "recipient": recipient if outcome == "Complete" else None,
          "pass_speed_kmh": round(random.uniform(35.0, 75.0), 1),
      }

    elif event_type == "Shot":
      live_stats[attacking_team]["shots"] += 1
      shot_xg = round(
          random.uniform(0.03, 0.45 if ball_x < 105 else 0.75), 2
      )
      live_stats[attacking_team]["xg"] = round(
          live_stats[attacking_team]["xg"] + shot_xg, 2
      )

      crowd_intensity = random.randint(75, 95)
      outcome = random.choices(
          ["Saved", "Blocked", "Missed", "Goal"], weights=[35, 30, 25, 10]
      )[0]

      if outcome in ["Saved", "Goal"]:
        live_stats[attacking_team]["shots_on_target"] += 1

      if outcome == "Goal":
        score[attacking_team] += 1
        crowd_intensity = 100
        commentary_flag = "GOAL_SCORED"
        if random.random() < 0.15:
          var_status = "Checking Possible Offside"

      payload_details = {
          "coordinates": {"start": [start_x, start_y]},
          "outcome": outcome,
          "xG": shot_xg,
          "shot_speed_kmh": round(random.uniform(70.0, 115.0), 1),
      }

      attacking_team, defending_team = defending_team, attacking_team
      ball_x, ball_y = 10.0, 40.0
      next_actor = next(
          p for p in rosters[attacking_team] if p["pos_key"] == "GK"
      )

    elif event_type in ["Tackle", "Foul"]:
      opponent = random.choice(rosters[defending_team])
      won_ball = random.random() > 0.50

      if event_type == "Foul":
        live_stats[defending_team]["fouls"] += 1
        crowd_intensity += 25
        card_issued = random.choices(
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
      actor_speed_kmh = round(random.uniform(22.0, 32.5), 1)
      end_x = min(118.0, max(2.0, start_x + random.uniform(3, 14)))
      end_y = min(78.0, max(2.0, start_y + random.uniform(-8, 8)))
      payload_details = {
          "coordinates": {
              "start": [start_x, start_y],
              "end": [round(end_x, 1), round(end_y, 1)],
          },
          "success": random.random() > 0.35,
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

    # Format Clock Display
    is_stoppage = (minute >= 45 and period == 1) or (
        minute >= 90 and period == 2
    )
    clock_str = (
        f"{minute:02d}:{second:02d}"
        + (f" +{stoppage_time}" if is_stoppage else "")
    )

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
            "actor": current_actor,
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

    # Append single line JSONL (fast, non-blocking I/O)
    with open(save_path, "a") as f:
      f.write(json.dumps(payload) + "\n")

    # CRITICAL: Output JSON to stdout so read_stream.py receives it
    print(json.dumps(payload), flush=True)

    # Log readable status to stderr
    print(
        f"[{clock_str}] {score[home_team]}-{score[away_team]} | {attacking_team}"
        f" ({event_type}) vs {defending_team} [{defensive_block}]",
        file=sys.stderr,
        flush=True,
    )

    # Increment match clock
    second += random.randint(2, 6)
    if second >= 60:
      minute += second // 60
      second %= 60

    time.sleep(1)


if __name__ == "__main__":
  run_broadcast_soccer_stream()