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

BACK_LINE = ("LB", "LCB", "RCB", "RB")

CENTRE_CIRCLE_M = 9.15
SPRINT_MPS = 9.0  # 32.4 km/h: with a dt, nobody moves faster than this
JITTER_M = 1.0

# How far the defending team shifts along its own attacking direction
BLOCK_SHIFT_M = {"High Press": 10.0, "Low Block (Deep Defense)": -2.0}
# How far goal-side of the ball the nearest defender stands, per block
PRESS_GAP_M = {
    "High Press": (1.0, 3.5),
    "Mid Block": (2.5, 7.0),
    "Compact Central Shape": (2.5, 7.0),
    "Low Block (Deep Defense)": (1.5, 5.0),
}


class TacticalTrackingEngine:
  """Moves 22 players each frame with the ball, possession and defensive block."""

  def __init__(self, team_a_name, team_b_name, roster_a, roster_b, rng=None):
    self.rng = rng or random.Random()
    # Team A attacks toward x=120, team B toward x=0, for the whole match
    self.direction = {team_a_name: 1, team_b_name: -1}
    self.players = {}
    for team, roster in ((team_a_name, roster_a), (team_b_name, roster_b)):
      for p, role in zip(roster, BASE_FORMATION_433):  # 11 players, one per role
        x, y = self._formation_spot(role, self.direction[team])
        self.players[p["id"]] = {
            "id": p["id"],
            "name": p["name"],
            "team": team,
            "role": role,
            "direction": self.direction[team],
            "x": x * PITCH_LENGTH,
            "y": y * PITCH_WIDTH,
            "speed_mps": 0.0,
        }

  def update_frame(
      self, ball_x, ball_y, possession_team, active_actor_id=None,
      defensive_block="Mid Block", kick_off=False, dt=None,
  ):
    """Calculates tactical player shifts based on ball position and defensive posture.

    With kick_off=True everyone but the taker stays in their own half and
    the defending team keeps outside the centre circle. With dt (match
    seconds since the last frame) every move is capped at sprint speed and
    dt=0 freezes the frame; without it players jump to their targets.
    """
    length_offset = (ball_x / PITCH_LENGTH - 0.5) * 35.0  # teams move with the ball
    presser = cover = None
    if not kick_off:  # the defender nearest the ball presses it; the next covers
      defending = next(t for t in self.direction if t != possession_team)
      near = self.nearest(defending, ball_x, ball_y, outfield=True, k=2)
      presser, cover = near[0]["id"], near[1]["id"]

    for pid, p in self.players.items():
      is_in_possession = p["team"] == possession_team
      norm_x, norm_y = self._formation_spot(p["role"], p["direction"])

      # 1. Push up or drop back with the ball, wider when in possession
      width_spread = 1.15 if is_in_possession else 0.85
      target_x = (norm_x * PITCH_LENGTH) + length_offset
      target_y = ((norm_y - 0.5) * (PITCH_WIDTH * width_spread)) + (PITCH_WIDTH * 0.5)

      # 2. A High Press pushes up the pitch, a Low Block drops deeper and pinches in
      if not is_in_possession:
        target_x += BLOCK_SHIFT_M.get(defensive_block, 0.0) * p["direction"]
        if defensive_block == "Low Block (Deep Defense)":
          target_y = (target_y * 0.75) + (PITCH_WIDTH * 0.5 * 0.25)

      # 3. The presser goes goal-side of the ball; the cover sits behind him
      jitter = JITTER_M
      if pid == presser:
        low, high = PRESS_GAP_M[defensive_block]
        gap, angle = self.rng.uniform(low, high), self.rng.uniform(-0.9, 0.9)
        target_x = ball_x - p["direction"] * gap * math.cos(angle)
        target_y = ball_y + gap * math.sin(angle)
        jitter = 0.0
      elif pid == cover:
        target_x = ball_x - p["direction"] * self.rng.uniform(7.0, 12.0)
        target_y = (ball_y + PITCH_WIDTH * 0.5) / 2

      # 4. The actor stands on the ball; all but the actor and presser get jitter
      is_actor = pid == active_actor_id
      if is_actor:
        target_x, target_y, jitter = ball_x, ball_y, 0.0
      target_x += self.rng.uniform(-jitter, jitter)
      target_y += self.rng.uniform(-jitter, jitter)
      if kick_off and not is_actor:
        target_x, target_y = self._kick_off_spot(
            p, target_x, target_y, ball_x, ball_y, is_in_possession
        )
      target_x = max(1.0, min(PITCH_LENGTH - 1.0, target_x))
      target_y = max(1.0, min(PITCH_WIDTH - 1.0, target_y))

      # 5. Move toward the target and record the speed
      dx, dy = target_x - p["x"], target_y - p["y"]
      dist_moved = math.hypot(dx, dy)
      if dt is None:
        p["speed_mps"] = round(dist_moved / 2.0, 2)  # assuming ~2s update step
      else:
        max_move = SPRINT_MPS * dt
        if dist_moved > max_move:
          scale = max_move / dist_moved
          dx, dy, dist_moved = dx * scale, dy * scale, max_move
        p["speed_mps"] = round(dist_moved / dt, 2) if dt else 0.0
      p["x"] = round(p["x"] + dx, 1)
      p["y"] = round(p["y"] + dy, 1)

    keys = ("name", "team", "role", "x", "y", "speed_mps")
    return [
        {"player_id": p["id"], **{k: p[k] for k in keys}}
        for p in self.players.values()
    ]

  def nearest(self, team, x, y, exclude=(), outfield=False, k=1):
    """The k players of a team closest to (x, y), nearest first."""
    candidates = [
        p for p in self.players.values()
        if p["team"] == team and p["id"] not in exclude
        and not (outfield and p["role"] == "GK")
    ]
    candidates.sort(key=lambda p: math.hypot(p["x"] - x, p["y"] - y))
    return candidates[:k]

  def pressure_at(self, team, x, y):
    """Metres from (x, y) to the nearest player of a team."""
    p = self.nearest(team, x, y)[0]
    return round(math.hypot(p["x"] - x, p["y"] - y), 1)

  def line_height(self, team):
    """Mean distance of the team's back four from its own goal line."""
    mine = [p for p in self.players.values() if p["team"] == team]
    backs = [p for p in mine if p["role"] in BACK_LINE] or [
        p for p in mine if p["role"] != "GK"
    ]
    own_goal_x = 0.0 if self.direction[team] == 1 else PITCH_LENGTH
    return round(sum(abs(p["x"] - own_goal_x) for p in backs) / len(backs), 1)

  def remove_player(self, pid):
    """A sent-off player leaves the pitch and every later frame."""
    del self.players[pid]

  def keeper(self, team):
    return next(
        p for p in self.players.values() if p["team"] == team and p["role"] == "GK"
    )

  @staticmethod
  def _formation_spot(role, direction):
    """The role's 4-3-3 spot (0-1 scale), mirrored for the team attacking x=0."""
    x, y = BASE_FORMATION_433[role]
    return (x, y) if direction == 1 else (1.0 - x, 1.0 - y)

  def _kick_off_spot(self, p, x, y, ball_x, ball_y, is_in_possession):
    """Moves a player into their own half, and defenders out of the circle."""
    half = PITCH_LENGTH / 2
    x = min(x, half - 1.0) if p["direction"] == 1 else max(x, half + 1.0)
    if not is_in_possession:
      radius = CENTRE_CIRCLE_M + 0.1  # still outside after rounding to 0.1 m
      dx, dy = x - ball_x, y - ball_y
      dist = math.hypot(dx, dy)
      if dist < radius:
        x, y = ball_x + dx / dist * radius, ball_y + dy / dist * radius
    return x, y