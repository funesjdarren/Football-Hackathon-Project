"""Match intel: Player Cards, pass difficulty and milestones from the packets.

MatchIntel.observe takes every packet in order and keeps one card per player
plus a list of milestones. pass_quality rates a single Pass from the tracking
frame of its own packet. Standard library only, no file or network access.
"""
import math

DIRECTION = {"FCA": 1, "BTU": -1}  # FC Alpha attacks toward x = 120
WEIGHTS = {"length": 30, "pressure": 25, "lane": 25, "progress": 20}
LABELS = ((65, "Elite"), (45, "Tricky"), (0, "Routine"))
LANE_M, END_M = 2.5, 1.0
FAST_SHOT_KMH = 100.0
COUNTERS = (
    "passes_attempted", "passes_completed", "difficulty_total", "elite_passes",
    "shots", "shots_on_target", "goals", "xg", "max_shot_speed_kmh",
    "max_pass_speed_kmh", "top_speed_kmh", "fouls", "yellow", "red",
)


def dist_to_line(p, a, b):
  """Metres from p to the segment from a to b."""
  dx, dy = b[0] - a[0], b[1] - a[1]
  t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1)
  t = min(max(t, 0.0), 1.0)
  return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def pass_quality(packet):
  """How hard a Pass was, from its own tracking frame; None for other packets.

  difficulty (0-100) = 30 for length (all 30 at 40 m) + 25 for pressure (all 25
  with an opponent on the passer, none from 5 m) + 25 for the lane (12.5 per
  opponent within 2.5 m of the pass line) + 20 for forward progress (all 20 at
  30 m). Routine below 45, Tricky below 65, Elite from 65.
  """
  ev = packet["event"]
  if ev["type"] != "Pass":
    return None
  coords = ev["details"]["coordinates"]
  start, end = coords["start"], coords["end"]
  opponents = [
      (p["x"], p["y"]) for p in packet["player_tracking"]["players"]
      if p["team"] != ev["team"]
  ]
  distance = math.dist(start, end)
  progress = (end[0] - start[0]) * DIRECTION[ev["team_id"]]
  pressure = min(math.dist(start, o) for o in opponents)
  lane = sum(
      dist_to_line(o, start, end) <= LANE_M
      and min(math.dist(o, start), math.dist(o, end)) > END_M
      for o in opponents
  )
  shares = {
      "length": distance / 40, "pressure": 1 - pressure / 5,
      "lane": lane / 2, "progress": progress / 30,
  }
  difficulty = round(sum(w * min(max(shares[k], 0), 1) for k, w in WEIGHTS.items()))
  return {
      "distance_m": round(distance, 1), "progress_m": round(progress, 1),
      "pressure_m": round(pressure, 1), "lane_defenders": lane,
      "difficulty": difficulty,
      "label": next(name for cut, name in LABELS if difficulty >= cut),
  }


class MatchIntel:
  """Player Cards and milestones for the match being fed in."""

  def __init__(self):
    self.reset(None)

  def reset(self, match_id):
    self.match_id, self.players, self.milestones = match_id, {}, []
    self.best = {"shot": 0.0, "pass": 0.0}  # match-best ball speeds
    self.on_target = set()  # teams that have had a shot on target

  def observe(self, packet):
    """Updates the cards from the next packet; returns its new milestones."""
    ev, details = packet["event"], packet["event"]["details"]
    meta = packet["broadcast_metadata"]
    if ev["type"] == "KICK_OFF" or packet["match_id"] != self.match_id:
      self.reset(packet["match_id"])
    for p in packet["player_tracking"]["players"]:
      self.players.setdefault(p["player_id"], {
          "player_id": p["player_id"], "name": p["name"], "team": p["team"],
          "role": p["role"], **dict.fromkeys(COUNTERS, 0),
      })
    new = []

    def note(kind, card, text):
      new.append({
          "clock": meta["clock"]["display_time"], "kind": kind,
          "player_id": card["player_id"], "team": card["team"], "text": text,
      })

    actor = self.players.get(ev["actor"]["id"])  # None for the referee
    if actor:  # the actor's own speed: tracking frames all hit the sprint cap
      actor["top_speed_kmh"] = max(actor["top_speed_kmh"], ev["actor_speed_kmh"])
    if ev["type"] == "Pass":
      quality, done = pass_quality(packet), details["outcome"] == "Complete"
      actor["passes_attempted"] += 1
      actor["passes_completed"] += done
      actor["difficulty_total"] += quality["difficulty"]
      actor["elite_passes"] += done and quality["label"] == "Elite"
      self._ball_speed(actor, "pass", details["pass_speed_kmh"])
    elif ev["type"] == "Shot":
      name, team, speed = actor["name"], actor["team"], details["shot_speed_kmh"]
      on_target = details["outcome"] in ("Goal", "Saved")
      actor["shots"] += 1
      actor["shots_on_target"] += on_target
      actor["xg"] += details["xG"]
      if on_target and team not in self.on_target:
        self.on_target.add(team)
        note("first_on_target", actor, f"First shot on target for {team}: {name}")
      if details["outcome"] == "Goal":
        actor["goals"] += 1
        home, away = meta["scoreboard"]["home"], meta["scoreboard"]["away"]
        note("goal", actor, f"GOAL! {name} scores for {team}. {home['team']} "
             f"{home['score']}-{away['score']} {away['team']}")
      if speed > self.best["shot"] and speed >= FAST_SHOT_KMH:
        note("fastest_shot", actor, f"Fastest shot of the match: {name}, {speed} km/h")
      self._ball_speed(actor, "shot", speed)
    elif ev["type"] == "Foul":
      offender, shown = self.players[ev["offender_id"]], details["card_issued"]
      second = details["second_yellow"]
      offender["fouls"] += 1
      offender["yellow"] += shown == "Yellow" or second
      offender["red"] += shown == "Red"
      if shown != "None":
        note(shown.lower(), offender, f"{shown} card for {offender['name']} "
             f"({offender['team']}){' after a second yellow' if second else ''}")
    self.milestones += new
    return new

  def _ball_speed(self, card, kind, speed):
    card[f"max_{kind}_speed_kmh"] = max(card[f"max_{kind}_speed_kmh"], speed)
    self.best[kind] = max(self.best[kind], speed)

  def player_card(self, player_id):
    """The GUI's Player Card for a player, or None for an unknown id."""
    if player_id not in self.players:
      return None
    card = dict(self.players[player_id])
    n, total = card["passes_attempted"], card.pop("difficulty_total")
    card["completion_pct"] = round(100 * card["passes_completed"] / n, 1) if n else None
    card["avg_pass_difficulty"] = round(total / n, 1) if n else None
    card["xg"] = round(card["xg"], 2)
    card["share_of_match_max"] = {
        f"{k}_speed": round(card[f"max_{k}_speed_kmh"] / best, 2) if best else 0.0
        for k, best in self.best.items()
    }
    return card
