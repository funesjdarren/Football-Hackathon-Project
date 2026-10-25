import argparse
import copy
from datetime import datetime, timezone
import json
import math
import os
import random
import secrets
import sys
import time
import paths
from tracking_engine import PITCH_LENGTH, PITCH_WIDTH, SPRINT_MPS, TacticalTrackingEngine

SYNTHETIC_TEAMS = ["FC Alpha", "Beta United"]
TEAM_CODES = {"FC Alpha": "FCA", "Beta United": "BTU"}

HALF_END_MIN = {1: 45, 2: 90}
STOPPAGE_MIN = {1: (1, 4), 2: (2, 6)}  # announced added time per half
MARKER_TYPES = ("KICK_OFF", "HALF_TIME", "SECOND_HALF", "FULL_TIME")
SHOT_REASONS = {
    "Goal": "Goal", "Saved": "Shot Saved",
    "Missed": "Shot Missed", "Blocked": "Shot Blocked",
}

# xG from shot distance (m) and goal-mouth angle (rad): about 0.46 from six
# yards out centrally, 0.20 from the penalty spot, 0.07 from 18 m, 0.02 from 25 m
XG_INTERCEPT, XG_ANGLE, XG_DIST = -0.35, 0.85, -0.145
GOAL_WIDTH_M = 7.32
SHOT_MISS_WEIGHTS = {"Saved": 27, "Blocked": 28, "Missed": 45}

# Discipline, per foul
P_YELLOW = 0.165
P_STRAIGHT_RED = 0.0025
SECOND_YELLOW_MULT = 0.35  # referees are slower to show a booked player a 2nd yellow
BOOKED_FOUL_MULT = 0.3  # booked players foul less
OFFENDER_WEIGHTS = (6, 3, 1)  # the 1st, 2nd and 3rd nearest outfield defender

# Match seconds until a restart is taken (all under a minute, so the State
# of the Game check-ins never fall late)
RESTART_STEP_S = {
    "Kick-off": (30, 50), "Goal Kick": (12, 20),
    "Keeper Ball": (5, 10), "Free Kick": (10, 25),
}

# Event mix per phase (Pass, Dribble, Tackle, Foul). A Shot weight is added
# from the xG of the spot, so most shots come from good positions
ZONE_WEIGHTS = {
    "Build-Up (Defensive Third)": {"Pass": 84, "Dribble": 5, "Tackle": 2, "Foul": 1.01},
    "Midfield Progression": {"Pass": 74, "Dribble": 7, "Tackle": 3.5, "Foul": 1.56},
    "Final Third Attack": {"Pass": 62, "Dribble": 8, "Tackle": 4, "Foul": 1.38},
}
SHOT_MAX_DIST_M = 32.0
SHOT_WEIGHT, SHOT_XG_EXP = 13.0, 0.65  # shot weight = SHOT_WEIGHT * (xG / 0.1) ** SHOT_XG_EXP

# Passing
PASS_BASE = 0.92
PASS_LEN_K = 0.0065  # completion lost per metre beyond 12 m
PASS_BOX_PENALTY = 0.30  # passes played into the box
PASS_PRESSED_PENALTY = 0.06  # the passer has a defender within 2 m
P_BOX_PASS = 0.20  # share of final-third passes aimed into the box
# Open-play pass aim per phase: (forward range, sideways spread), in metres
PASS_AIM = {
    "Build-Up (Defensive Third)": ((-6, 30), 25),
    "Midfield Progression": ((-10, 26), 22),
    "Final Third Attack": ((-12, 16), 20),
}
PASS_LEAD = (0.2, 0.6)  # lands this far from the aimed-at teammate toward the aim
REACH = 0.9  # a player covers at most 90% of a sprint to reach the ball
DRIBBLE_MPS = 7.0
DRIBBLE_SUCCESS = 0.75
TACKLE_WIN = 0.5

# Match seconds until the next packet, drawn before each event resolves
STEP_S = {
    "Pass": (2, 5), "Long Pass": (3, 6),  # longer than 30 m
    "Dribble": (2, 4), "Tackle": (2, 4), "Loose Ball": (2, 4),
}

# Invented names and venues only: the clubs and players are fictional, so no
# real footballer can be matched by initial and surname
FIRST_NAMES = "Liam Noah Oliver Elijah James William Lucas Kai Arlo Milo".split()
LAST_NAMES = """
    Ashgrove Bellmont Corvane Dunholt Elswick Fairmoor Garrow Halvard Iverly
    Jarrell Kestley Lorrimer Merrivale Northam Orwin Penhale Quillon Rendell
    Stanmore Tarrow Ulvane Varden Westcott Yarrowby Zellick
""".split()
VENUES = [
    {"name": "Brightwater Bowl", "city": "Eastmere", "capacity": 38500},
    {"name": "Kestrel Ground", "city": "Wexbury", "capacity": 27200},
    {"name": "Larkspur Stadium", "city": "Thornhaven", "capacity": 45800},
]


def generate_squad(team_name, surnames, rng):
  """11 players with the given unique surnames and unique shirt numbers."""
  numbers = [1] + rng.sample(range(2, 31), 10)  # keeper wears 1
  roles = [("Goalkeeper", "GK")] + [("Defender", "DEF")] * 4
  roles += [("Midfielder", "MID")] * 3 + [("Forward", "FWD")] * 3
  return [
      {
          "id": f"{TEAM_CODES[team_name]}-{101 + i}",
          "name": f"{rng.choice(FIRST_NAMES)[0]}. {surnames[i]}",
          "role": role,
          "pos_key": pos_key,
          "shirt_number": numbers[i],
          "cards": {"yellow": 0, "red": 0},
      }
      for i, (role, pos_key) in enumerate(roles)
  ]


def kick_off_taker(engine, roster):
  """The striker, or the outfield player nearest the centre spot if sent off."""
  on_pitch = [p for p in roster if p["id"] in engine.players]
  for p in on_pitch:
    if engine.players[p["id"]]["role"] == "ST":
      return p
  team = engine.players[on_pitch[0]["id"]]["team"]
  nearest = engine.nearest(team, PITCH_LENGTH / 2, PITCH_WIDTH / 2, outfield=True)[0]
  return next(p for p in on_pitch if p["id"] == nearest["id"])


def rel_x(direction, x):
  """Distance up the pitch from the attacking team's own goal line."""
  return x if direction == 1 else PITCH_LENGTH - x


def in_box(rx, y):
  """Inside the penalty box being attacked."""
  return rx >= 102.0 and 18.0 <= y <= 62.0


def clamp_on_pitch(x, y):
  return min(118.0, max(2.0, x)), min(78.0, max(2.0, y))


def toward(src, dst, max_d):
  """dst, pulled toward src so it is at most max_d away from src."""
  dx, dy = dst[0] - src[0], dst[1] - src[1]
  dist = math.hypot(dx, dy)
  if dist <= max_d:
    return dst
  return src[0] + dx * max_d / dist, src[1] + dy * max_d / dist


def reach(player, x, y, step):
  """(x, y) pulled within the player's reach by the next packet, to 0.1 m."""
  x, y = toward((player["x"], player["y"]), (x, y), SPRINT_MPS * step * REACH)
  return round(x, 1), round(y, 1)


def shot_geometry(rx, y):
  """Distance to the goal centre, and the angle the goal mouth makes."""
  dx = PITCH_LENGTH - rx
  dy = abs(y - PITCH_WIDTH / 2)
  half = GOAL_WIDTH_M / 2
  angle = math.atan2(GOAL_WIDTH_M * dx, dx * dx + dy * dy - half * half)
  if angle < 0:
    angle += math.pi
  return math.hypot(dx, dy), angle


def expected_goals(rx, y):
  dist, angle = shot_geometry(rx, y)
  return 1.0 / (1.0 + math.exp(-(XG_INTERCEPT + XG_ANGLE * angle + XG_DIST * dist)))


def book(offender, rng):
  """Card for a foul, as (card_issued, commentary_trigger, second_yellow).

  A booked player can only get a second yellow, which counts as a red.
  """
  cards = offender["cards"]
  r = rng.random()
  if cards["yellow"]:
    if r < P_YELLOW * SECOND_YELLOW_MULT:
      cards["yellow"] += 1
      cards["red"] += 1
      return "Red", "SENDING_OFF_RED", True
  elif r < P_STRAIGHT_RED:
    cards["red"] += 1
    return "Red", "SENDING_OFF_RED", False
  elif r < P_STRAIGHT_RED + P_YELLOW:
    cards["yellow"] += 1
    return "Yellow", "BOOKING_YELLOW", False
  return "None", None, False


def kick_off_metadata(match_id, rosters, engine, referee, rng):
  """Match details carried by the KICK_OFF packet (never a line of its own)."""
  venue = rng.choice(VENUES)
  info = ("id", "name", "shirt_number", "role", "pos_key")
  teams = {}
  for side, team in zip(("home", "away"), SYNTHETIC_TEAMS):
    teams[side] = {
        "name": team,
        "team_id": TEAM_CODES[team],
        "formation": "4-3-3",
        "attacking_direction": engine.direction[team],
        "lineup": [
            dict({k: p[k] for k in info}, tactical_role=engine.players[p["id"]]["role"])
            for p in rosters[team]
        ],
    }
  return {
      "match_id": match_id,
      "generator": "v2",
      "competition": "Synthetic Friendly",
      "venue": dict(venue),
      "attendance": rng.randint(venue["capacity"] * 6 // 10, venue["capacity"]),
      "referee": {"id": referee["id"], "name": referee["name"]},
      "teams": teams,
  }


def simulate(rng, match_id="syn-unseeded"):
  """Yields one full match of packets, deterministic for a given rng.

  Packets are independent copies, so they can be collected safely. The
  caller decides where they go and how fast (see main). Every rng call
  happens in a fixed order, so reordering them changes every seed's match.
  """
  home_team, away_team = SYNTHETIC_TEAMS
  surnames = rng.sample(LAST_NAMES, 23)  # 22 unique players + the referee
  rosters = {
      home_team: generate_squad(home_team, surnames[:11], rng),
      away_team: generate_squad(away_team, surnames[11:22], rng),
  }
  referee = {  # the whistle packets' actor, with every key a player actor has
      "id": "REF-01", "name": f"{rng.choice(FIRST_NAMES)[0]}. {surnames[22]}",
      "role": "Referee", "pos_key": "REF", "shirt_number": None,
      "cards": {"yellow": 0, "red": 0},
  }
  engine = TacticalTrackingEngine(
      home_team, away_team, rosters[home_team], rosters[away_team], rng=rng
  )
  by_id = {p["id"]: p for roster in rosters.values() for p in roster}
  metadata = kick_off_metadata(match_id, rosters, engine, referee, rng)

  score = {home_team: 0, away_team: 0}
  live_stats = {
      team: {
          "shots": 0, "shots_on_target": 0, "xg": 0.0,
          "passes_completed": 0, "passes_attempted": 0, "fouls": 0,
      }
      for team in SYNTHETIC_TEAMS
  }
  attacking_team, defending_team = home_team, away_team
  ball_x, ball_y = 60.0, 40.0
  current_actor = kick_off_taker(engine, rosters[home_team])
  phase = "Midfield Progression"

  # Period state machine: the clock is absolute match seconds (the 2nd half
  # restarts at 45:00), added time is announced once the half's 45/90 minutes
  # are up, and a whistle packet marks each kick-off and the end of each half
  period, clock_s, stoppage = 1, 0, None
  whistle = "KICK_OFF"
  event_counter = 0
  possession_id, last_possession = 1, None
  restart = None  # set by an event; the next packet takes it with a Pass
  frame_dt = 60.0  # match seconds since the last frame (teams walk out first)

  while True:
    event_counter += 1
    event_team = reason = recipient_id = challenger_id = offender_id = None
    var_status, commentary_flag = "Inactive", None
    restart_now, restart = restart, None
    step = None  # match seconds to the next packet, set by every action
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
      # Whistle packets have the full packet shape so no consumer breaks, and
      # change no stats; the kick-off taker stands on the centre spot
      event_type, actor, actor_speed_kmh = whistle, referee, 0.0
      restart_now = None  # a whistle cancels any pending restart
      kick_off = whistle in ("KICK_OFF", "SECOND_HALF")
      if kick_off:
        phase = "Midfield Progression"
        restart = "Kick-off"  # the next packet is the kick-off pass
      if whistle == "SECOND_HALF":
        reason = "Kick-off"  # the away team takes over for the 2nd half
      defensive_block = "Mid Block"
      crowd_intensity = rng.randint(60, 75)
      active_id = current_actor["id"] if kick_off else None
    else:
      # Phase and defensive shape from where the ball is, relative to the team
      # on the ball (each team attacks its own goal)
      d = engine.direction[attacking_team]
      rx = rel_x(d, ball_x)
      if rx < 35:
        phase, defensive_block = "Build-Up (Defensive Third)", "High Press"
      elif rx < 75:
        phase = "Midfield Progression"
        defensive_block = rng.choice(["Mid Block", "Compact Central Shape"])
      else:
        phase, defensive_block = "Final Third Attack", "Low Block (Deep Defense)"

      # A restart is always taken with a pass. Otherwise the phase's mix, with
      # no fouls in the box (no penalties) and a shot weight that grows with xG
      if restart_now:
        event_type = "Pass"
      else:
        weights = dict(ZONE_WEIGHTS[phase])
        if in_box(rx, ball_y):
          weights["Foul"] = 0.0
        if shot_geometry(rx, ball_y)[0] <= SHOT_MAX_DIST_M:
          xg = expected_goals(rx, ball_y)
          weights["Shot"] = SHOT_WEIGHT * (xg / 0.1) ** SHOT_XG_EXP
        event_type = rng.choices(list(weights), weights=list(weights.values()))[0]
      crowd_intensity = rng.randint(35, 55)
      start_x, start_y = ball_x, ball_y
      actor, event_team = current_actor, attacking_team
      actor_speed_kmh = round(rng.uniform(12.0, 26.5), 1)
      kick_off, active_id = restart_now == "Kick-off", current_actor["id"]

    # The frame shows the moment the event starts, with the player on the ball
    # (nobody at half and full time); the open-play picks below use it
    player_tracking_frame = engine.update_frame(
        ball_x, ball_y, attacking_team, active_actor_id=active_id,
        defensive_block=defensive_block, kick_off=kick_off, dt=frame_dt,
    )
    defensive_line_height_m = engine.line_height(defending_team)
    closest_defender_dist_m = engine.pressure_at(defending_team, ball_x, ball_y)

    if whistle:
      payload_details = {"coordinates": {"start": [ball_x, ball_y]}}
      next_actor = current_actor

    elif event_type == "Pass":
      stats = live_stats[attacking_team]
      stats["passes_attempted"] += 1
      # Aim, in metres from the passer's own goal line
      if restart_now == "Kick-off":
        aim = (rx - rng.uniform(6, 14), 40 + rng.uniform(-15, 15))
      elif restart_now in ("Goal Kick", "Keeper Ball"):
        if rng.random() < 0.5:
          aim = (rng.uniform(55, 80), rng.uniform(12, 68))  # long
        else:
          aim = (rng.uniform(12, 28), rng.uniform(8, 72))  # short
      elif phase == "Final Third Attack" and rng.random() < P_BOX_PASS:
        aim = (rng.uniform(104, 116), rng.uniform(28, 52))  # into the box
      else:
        (low, high), spread = PASS_AIM[phase]
        aim = (rx + rng.uniform(low, high), start_y + rng.uniform(-spread, spread))
      aim_rx, aim_y = clamp_on_pitch(*aim)
      aim_x = rel_x(d, aim_rx)

      # The ball lands part of the way from the aimed-at teammate to the aim
      passer = (actor["id"],)
      target = engine.nearest(attacking_team, aim_x, aim_y, exclude=passer)[0]
      lead = rng.uniform(*PASS_LEAD)
      land_x = target["x"] + (aim_x - target["x"]) * lead
      land_y = target["y"] + (aim_y - target["y"]) * lead
      length = math.hypot(land_x - start_x, land_y - start_y)
      into_box = in_box(rel_x(d, land_x), land_y) and not in_box(rx, start_y)
      p_complete = PASS_BASE - PASS_LEN_K * max(0.0, length - 12.0)
      if into_box:
        p_complete -= PASS_BOX_PENALTY
      if closest_defender_dist_m < 2.0:
        p_complete -= PASS_PRESSED_PENALTY
      p_complete = min(0.97, max(0.2, p_complete))
      step = rng.randint(*STEP_S["Long Pass" if length > 30 else "Pass"])

      outcome = "Complete" if rng.random() < p_complete else "Incomplete"
      recipient = None
      if outcome == "Complete":
        stats["passes_completed"] += 1
        receiver = engine.nearest(attacking_team, land_x, land_y, exclude=passer)[0]
        recipient = by_id[receiver["id"]]
        recipient_id = recipient["id"]
      else:
        crowd_intensity += 15
        receiver = engine.nearest(defending_team, land_x, land_y)[0]
        attacking_team, defending_team = defending_team, attacking_team
        reason = "Interception"
      next_actor = by_id[receiver["id"]]
      # Pulling toward the nearest player keeps them the nearest
      ball_x, ball_y = reach(receiver, land_x, land_y, step)
      payload_details = {
          "coordinates": {"start": [start_x, start_y], "end": [ball_x, ball_y]},
          "outcome": outcome,
          "recipient": recipient,
          "pass_speed_kmh": round(rng.uniform(35.0, 75.0), 1),
      }

    elif event_type == "Shot":
      stats = live_stats[attacking_team]
      stats["shots"] += 1
      shot_xg = expected_goals(rx, start_y)
      stats["xg"] = round(stats["xg"] + shot_xg, 2)
      crowd_intensity = rng.randint(75, 95)
      if rng.random() < shot_xg:  # goals happen as often as xG says
        outcome = "Goal"
      else:
        outcome = rng.choices(
            list(SHOT_MISS_WEIGHTS), weights=list(SHOT_MISS_WEIGHTS.values())
        )[0]
      if outcome in ("Saved", "Goal"):
        stats["shots_on_target"] += 1
      if outcome == "Goal":
        score[attacking_team] += 1
        crowd_intensity, commentary_flag = 100, "GOAL_SCORED"
        if rng.random() < 0.15:
          var_status = "Checking Possible Offside"
      payload_details = {
          "coordinates": {"start": [start_x, start_y]},
          "outcome": outcome,
          "xG": round(shot_xg, 2),
          "shot_speed_kmh": round(rng.uniform(70.0, 115.0), 1),
      }
      if outcome != "Blocked":
        # Where the ball crosses the goal line: in the goal mouth, or wide
        if outcome in ("Goal", "Saved"):
          off_centre = rng.uniform(-3.4, 3.4)
        else:
          off_centre = rng.choice([-1, 1]) * rng.uniform(4.5, 9.0)
        payload_details["coordinates"]["end"] = [
            rel_x(d, PITCH_LENGTH), round(PITCH_WIDTH / 2 + off_centre, 1)
        ]

      attacking_team, defending_team = defending_team, attacking_team
      reason = SHOT_REASONS[outcome]
      keeper = engine.keeper(attacking_team)
      if outcome == "Goal":  # the conceding team restarts from the centre
        restart, ball_x, ball_y = "Kick-off", PITCH_LENGTH / 2, PITCH_WIDTH / 2
        next_actor = kick_off_taker(engine, rosters[attacking_team])
      elif outcome == "Saved":
        restart, ball_x, ball_y = "Keeper Ball", keeper["x"], keeper["y"]
        next_actor = by_id[keeper["id"]]
      elif outcome == "Missed":
        restart = "Goal Kick"
        ball_x = rel_x(engine.direction[attacking_team], 5.5)
        ball_y = round(PITCH_WIDTH / 2 + rng.uniform(-9.0, 9.0), 1)
        next_actor = by_id[keeper["id"]]
      else:  # Blocked: the nearest defender picks up the loose ball
        step = rng.randint(*STEP_S["Loose Ball"])
        picker = engine.nearest(attacking_team, ball_x, ball_y, outfield=True)[0]
        ball_x, ball_y = reach(picker, ball_x, ball_y, step)
        next_actor = by_id[picker["id"]]
      if restart:
        step = rng.randint(*RESTART_STEP_S[restart])

    elif event_type == "Tackle":
      challenger = engine.nearest(defending_team, ball_x, ball_y, outfield=True)[0]
      opponent = by_id[challenger["id"]]
      challenger_id = opponent["id"]
      won_ball = rng.random() < TACKLE_WIN
      payload_details = {
          "coordinates": {"start": [start_x, start_y]},
          "challenger": opponent["name"],
          "outcome": "Ball Won" if won_ball else "Unsuccessful",
      }
      step = rng.randint(*STEP_S["Tackle"])
      next_actor = current_actor
      if won_ball:  # the tackler comes away with the ball, within reach
        attacking_team, defending_team = defending_team, attacking_team
        reason, next_actor = "Tackle", opponent
        ball_x, ball_y = reach(challenger, ball_x, ball_y, step)

    elif event_type == "Foul":
      # One of the 3 nearest outfield defenders; booked players foul less
      near = engine.nearest(defending_team, ball_x, ball_y, outfield=True, k=3)
      weights = [
          w * (BOOKED_FOUL_MULT if by_id[c["id"]]["cards"]["yellow"] else 1)
          for w, c in zip(OFFENDER_WEIGHTS, near)
      ]
      opponent = by_id[rng.choices(near, weights=weights)[0]["id"]]
      offender_id = opponent["id"]
      rng.random()  # unused (a Tackle's ball-won draw), kept so seeds replay the same
      live_stats[defending_team]["fouls"] += 1
      crowd_intensity += 25
      card_issued, commentary_flag, second_yellow = book(opponent, rng)
      if card_issued == "Red":
        engine.remove_player(opponent["id"])  # off for good
      payload_details = {
          "coordinates": {"start": [start_x, start_y]},
          "fouled_player": current_actor["name"],
          "offender": opponent["name"],
          "card_issued": card_issued,
          "second_yellow": second_yellow,
      }
      restart = "Free Kick"  # taken by the fouled player
      step = rng.randint(*RESTART_STEP_S[restart])
      next_actor = current_actor

    else:  # Dribble: carry the ball forward, no faster than DRIBBLE_MPS
      actor_speed_kmh = round(rng.uniform(22.0, 32.5), 1)
      step = rng.randint(*STEP_S["Dribble"])
      aim_rx = rx + rng.uniform(3, 14)
      aim_rx, aim_y = clamp_on_pitch(aim_rx, start_y + rng.uniform(-8, 8))
      end_x, end_y = toward(
          (start_x, start_y), (rel_x(d, aim_rx), aim_y), DRIBBLE_MPS * step
      )
      success = rng.random() < DRIBBLE_SUCCESS
      next_actor = current_actor
      if success:
        ball_x, ball_y = round(end_x, 1), round(end_y, 1)
      else:  # the nearest outfield defender wins it, within reach
        tackler = engine.nearest(defending_team, end_x, end_y, outfield=True)[0]
        ball_x, ball_y = reach(tackler, end_x, end_y, step)
        attacking_team, defending_team = defending_team, attacking_team
        next_actor, reason = by_id[tackler["id"]], "Dribble Lost"
      payload_details = {
          "coordinates": {"start": [start_x, start_y], "end": [ball_x, ball_y]},
          "success": success,
      }

    # Possession chain: possession_team/defending_team keep their v1 meaning
    # (the team on the ball AFTER the event); from_team is who started it
    from_team = event_team or last_possession
    changed = from_team is not None and from_team != attacking_team
    if changed:
      possession_id += 1
    possession_change = {
        "changed": changed,
        "from_team": from_team,
        "to_team": attacking_team,
        "reason": reason if changed else None,
        "possession_id": possession_id,
    }
    last_possession = attacking_team
    # Possession % is each team's share of pass attempts
    attempts = [live_stats[team]["passes_attempted"] for team in SYNTHETIC_TEAMS]
    home_poss = round(attempts[0] / sum(attempts) * 100) if sum(attempts) else 50

    payload = {
        "event_id": f"syn-ev-{event_counter:06d}",
        "match_id": match_id,
        "broadcast_metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "clock": {
                "period": period, "display_time": clock_str,
                "minute": minute, "second": second,
            },
            "scoreboard": {
                side: {"team": team, "score": score[team], "team_id": TEAM_CODES[team]}
                for side, team in zip(("home", "away"), SYNTHETIC_TEAMS)
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
            "possession_team_id": TEAM_CODES[attacking_team],
            "defending_team_id": TEAM_CODES[defending_team],
        },
        "possession_change": possession_change,
        "event": {
            "type": event_type,
            "actor": actor,
            "actor_speed_kmh": actor_speed_kmh,
            "details": payload_details,
            "team": event_team,
            "team_id": TEAM_CODES.get(event_team),
            "restart": restart_now,
            "recipient_id": recipient_id,
            "challenger_id": challenger_id,
            "offender_id": offender_id,
        },
        "live_match_stats": {
            "possession_pct": {home_team: home_poss, away_team: 100 - home_poss},
            "box_score": live_stats,
        },
        "player_tracking": {
            "frame_rate_hz": 0.5,
            "pitch_units": "meters (120x80)",
            "players": player_tracking_frame,
        },
    }
    if whistle == "KICK_OFF":
      payload["match_metadata"] = metadata

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
      current_actor = kick_off_taker(engine, rosters[away_team])
      frame_dt = 900.0  # half-time: everyone walks back out
    elif whistle:
      whistle = None  # the kick-off pass happens at the same clock time
      frame_dt = 0.0
    else:
      next_clock = clock_s + step
      if stoppage is not None:
        # Never run past the end of added time: the whistle lands exactly on it
        next_clock = min(next_clock, half_end + stoppage * 60)
      frame_dt, clock_s = next_clock - clock_s, next_clock


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
      "--speed", type=float, default=1.0,
      help="packets per real second; 0 means no delay (default: 1)",
  )
  parser.add_argument(
      "--out", default=os.path.join(paths.DATA_DIR, "match_data.jsonl"),
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
  for packet in simulate(random.Random(seed), match_id=f"syn-{seed:06d}"):
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
