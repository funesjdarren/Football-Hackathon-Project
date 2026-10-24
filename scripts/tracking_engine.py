import math
import random

# Pitch dimensions: 120m length x 80m width
PITCH_LENGTH = 120.0
PITCH_WIDTH = 80.0

# Base 4-3-3 formation anchor positions (Normalized on 0.0 - 1.0 scale)
# Team attacking left-to-right (0 -> 120)
BASE_FORMATION_433 = {
    "GK": (0.05, 0.50),
    "LB": (0.25, 0.15),
    "LCB": (0.22, 0.38),
    "RCB": (0.22, 0.62),
    "RB": (0.25, 0.85),
    "DM": (0.40, 0.50),
    "LCM": (0.50, 0.32),
    "RCM": (0.50, 0.68),
    "LW": (0.75, 0.18),
    "ST": (0.78, 0.50),
    "RW": (0.75, 0.82),
}

ROLE_KEYS = ["GK", "LB", "LCB", "RCB", "RB", "DM", "LCM", "RCM", "LW", "ST", "RW"]

CENTRE_CIRCLE_M = 9.15


class TacticalTrackingEngine:
  """Maintains 22-player coordinates and smoothly morphs their positions

  relative to ball location, team possession, and tactical phases.
  """

  def __init__(self, team_a_name, team_b_name, roster_a, roster_b, rng=None):
    self.team_a = team_a_name
    self.team_b = team_b_name
    self.rng = rng or random.Random()
    # Team A attacks toward x=120, team B toward x=0, for the whole match
    self.direction = {team_a_name: 1, team_b_name: -1}

    # Assign concrete tactical roles to squad players
    self.players = {}
    for i, p in enumerate(roster_a[:11]):
      role = ROLE_KEYS[i]
      self.players[p["id"]] = {
          "id": p["id"],
          "name": p["name"],
          "team": team_a_name,
          "role": role,
          "direction": 1,  # Attacking Left -> Right
          "x": BASE_FORMATION_433[role][0] * PITCH_LENGTH,
          "y": BASE_FORMATION_433[role][1] * PITCH_WIDTH,
          "speed_mps": 0.0,
      }

    for i, p in enumerate(roster_b[:11]):
      role = ROLE_KEYS[i]
      # Team B attacks Right -> Left (mirrored coordinates)
      base_x = (1.0 - BASE_FORMATION_433[role][0]) * PITCH_LENGTH
      base_y = (1.0 - BASE_FORMATION_433[role][1]) * PITCH_WIDTH
      self.players[p["id"]] = {
          "id": p["id"],
          "name": p["name"],
          "team": team_b_name,
          "role": role,
          "direction": -1,  # Attacking Right -> Left
          "x": base_x,
          "y": base_y,
          "speed_mps": 0.0,
      }

  def update_frame(
      self,
      ball_x,
      ball_y,
      possession_team,
      active_actor_id=None,
      defensive_block="Mid Block",
      kick_off=False,
  ):
    """Calculates tactical player shifts based on ball position and defensive posture.

    With kick_off=True everyone but the taker stays in their own half and
    the defending team keeps outside the centre circle.
    """
    # Ball normalized position along the length of pitch (0.0 to 1.0)
    ball_progress = ball_x / PITCH_LENGTH
    frame_snapshot = []

    for pid, p in self.players.items():
      is_in_possession = p["team"] == possession_team
      role = p["role"]
      norm_x, norm_y = BASE_FORMATION_433[role]

      # 1. Base team line shift: push up or drop back with the ball
      if p["direction"] == 1:
        # Team attacking 0 -> 120
        # If in possession, team stretches forward and expands wide
        width_spread = 1.15 if is_in_possession else 0.85
        length_offset = (ball_progress - 0.5) * 35.0  # Team moves with ball
        target_x = (norm_x * PITCH_LENGTH) + length_offset
        target_y = (
            (norm_y - 0.5) * (PITCH_WIDTH * width_spread)
        ) + (PITCH_WIDTH * 0.5)
      else:
        # Team attacking 120 -> 0 (mirrored)
        width_spread = 1.15 if is_in_possession else 0.85
        length_offset = (ball_progress - 0.5) * 35.0
        target_x = ((1.0 - norm_x) * PITCH_LENGTH) + length_offset
        target_y = (
            ((1.0 - norm_y) - 0.5) * (PITCH_WIDTH * width_spread)
        ) + (PITCH_WIDTH * 0.5)

      # 2. Apply Defensive Block adjustments (when defending)
      if not is_in_possession:
        if defensive_block == "High Press":
          # Step forward aggressively towards opponent half
          target_x += 10.0 * (-p["direction"])
        elif defensive_block == "Low Block (Deep Defense)":
          # Compact near penalty box
          target_x += 15.0 * (p["direction"])
          # Pinch inwards (compact horizontally)
          target_y = (target_y * 0.75) + (PITCH_WIDTH * 0.5 * 0.25)

      # 3. Ball Carrier Override: The active actor must be at the ball
      is_actor = bool(active_actor_id) and pid == active_actor_id
      if is_actor:
        target_x = ball_x
        target_y = ball_y
      else:
        # Add organic micro-jitters (breathing room ± 1.2m)
        target_x += self.rng.uniform(-1.2, 1.2)
        target_y += self.rng.uniform(-1.2, 1.2)

      if kick_off and not is_actor:
        target_x, target_y = self._kick_off_spot(
            p, target_x, target_y, ball_x, ball_y, is_in_possession
        )

      target_x = max(1.0, min(PITCH_LENGTH - 1.0, target_x))
      target_y = max(1.0, min(PITCH_WIDTH - 1.0, target_y))

      # Calculate movement delta and instantaneous speed
      dist_moved = math.hypot(target_x - p["x"], target_y - p["y"])
      p["speed_mps"] = round(dist_moved / 2.0, 2)  # assuming ~2s update step
      p["x"] = round(target_x, 1)
      p["y"] = round(target_y, 1)

      frame_snapshot.append({
          "player_id": p["id"],
          "name": p["name"],
          "team": p["team"],
          "role": p["role"],
          "x": p["x"],
          "y": p["y"],
          "speed_mps": p["speed_mps"],
      })

    return frame_snapshot

  def position(self, pid):
    p = self.players[pid]
    return p["x"], p["y"]

  def nearest(self, team, x, y, exclude=(), outfield=False, k=1):
    """The k players of a team closest to (x, y), nearest first."""
    candidates = [
        p
        for p in self.players.values()
        if p["team"] == team
        and p["id"] not in exclude
        and not (outfield and p["role"] == "GK")
    ]
    candidates.sort(key=lambda p: math.hypot(p["x"] - x, p["y"] - y))
    return candidates[:k]

  def keeper(self, team):
    return next(
        (p for p in self.players.values() if p["team"] == team and p["role"] == "GK"),
        None,
    )

  def _kick_off_spot(self, p, x, y, ball_x, ball_y, is_in_possession):
    """Moves a player into their own half, and defenders out of the circle."""
    half = PITCH_LENGTH / 2
    if p["direction"] == 1:
      x = min(x, half - 1.0)
    else:
      x = max(x, half + 1.0)
    if not is_in_possession:
      dx, dy = x - ball_x, y - ball_y
      dist = math.hypot(dx, dy)
      if dist < CENTRE_CIRCLE_M:
        if dist == 0:
          dx, dy, dist = -p["direction"], 0.0, 1.0
        x = ball_x + dx / dist * CENTRE_CIRCLE_M
        y = ball_y + dy / dist * CENTRE_CIRCLE_M
    return x, y