"""Play-by-play v2: the agent's input, its reply schema and the feed rows.

build_input states the facts of one moment and the agent answers with FIELDS.
to_row turns a reply into a commentary.jsonl line the GUI shows from "clock"
and "text"; the other fields feed personalised views. Standard library only,
so it is safe to import anywhere.
"""
import json
import math
import os
from datetime import datetime, timezone

import paths

COMMENTARY_PATH = os.path.join(paths.DATA_DIR, "commentary.jsonl")
# The goal line each team attacks, by team_id (teams never switch ends)
GOAL_X = {"FCA": 120.0, "BTU": 0.0}
FIELDS = (
    "headline", "casual", "analyst", "why_it_matters", "eli5_term", "eli5_definition"
)
# The response format enforced on the agent: strict mode needs every field
# required and no others
SCHEMA = {
    "type": "object",
    "properties": {name: {"type": "string"} for name in FIELDS},
    "required": list(FIELDS),
    "additionalProperties": False,
}


def metres(a, b):
  return round(math.hypot(a[0] - b[0], a[1] - b[1]), 1)


def build_input(packet, reason=None):
  """The play-by-play agent's input for one moment, with nothing left to infer.

  It names the actor's team, gives the score after the moment and replaces raw
  pitch coordinates with distances. Keys without a value are left out.
  """
  meta, tactics = packet["broadcast_metadata"], packet["tactical_context"]
  ev, details = packet["event"], packet["event"]["details"]
  home, away = meta["scoreboard"]["home"], meta["scoreboard"]["away"]
  opponents = away["team"] if ev["team"] == home["team"] else home["team"]
  start, end = details["coordinates"]["start"], details["coordinates"].get("end")
  kind = ev["type"]
  if kind == "Shot":
    goal = (GOAL_X[ev["team_id"]], 40.0)
    facts = {"distance_to_goal_m": metres(start, goal), "xG": details["xG"],
             "shot_speed_kmh": details["shot_speed_kmh"]}
  elif kind == "Pass":
    facts = {"length_m": metres(start, end),
             "pass_speed_kmh": details["pass_speed_kmh"]}
  elif kind == "Dribble":
    facts = {"kept_the_ball": details["success"], "carried_m": metres(start, end)}
  elif kind == "Tackle":
    facts = {"challenger": details["challenger"], "challenger_team": opponents}
  else:  # Foul
    facts = {"offender": details["offender"], "offender_team": opponents,
             "card": details["card_issued"],
             "second_yellow": details["second_yellow"]}
  change = packet["possession_change"]
  score = f"{home['team']} {home['score']} - {away['score']} {away['team']}"
  data = {
      "clock": meta["clock"]["display_time"],
      "trigger": reason,
      "event_type": kind,
      "outcome": details.get("outcome"),
      "restart": ev.get("restart"),
      "actor": ev["actor"]["name"],
      "actor_role": ev["actor"]["role"],
      "actor_team": ev["team"],
      "opponents": opponents,
      # After a shot the ball goes to the restart, which is not a turnover
      "ball_won_by": change["to_team"] if change["changed"] and kind != "Shot"
                     else None,
      "score_after": score,
      "phase_of_play": tactics["phase_of_play"],
      "opponents_shape": {
          "structure": tactics["defensive_structure"],
          "back_line_from_own_goal_m": tactics["defensive_line_height_meters"],
          "nearest_opponent_m": tactics["pressure_distance_to_ball_meters"],
      },
      "actor_speed_kmh": ev["actor_speed_kmh"],
      "details": facts,
      "possession_share": packet["live_match_stats"]["possession_pct"],
  }
  return {k: v for k, v in data.items() if v is not None}


def parse_reply(reply):
  """The reply's fields as stripped strings, or None if it isn't a v2 reply."""
  try:
    data = json.loads(reply)
  except ValueError:
    return None
  if not (isinstance(data, dict)
          and all(isinstance(data.get(name), str) for name in FIELDS)):
    return None
  fields = {name: data[name].strip() for name in FIELDS}
  return fields if fields["headline"] and fields["casual"] else None


def render_text(fields):
  """Plain text for the GUI: headline, casual line, why it matters, then ELI5."""
  headline = fields["headline"]
  if headline[-1] not in ".!?":
    headline += "."
  text = f"{headline} {fields['casual']}"
  if fields["why_it_matters"]:
    text += f" Why it matters: {fields['why_it_matters']}"
  if fields["eli5_term"] and fields["eli5_definition"]:
    text += f"\n{fields['eli5_term']}: {fields['eli5_definition']}"
  return text


def to_row(packet, reply, reason=None, agent_name=None, agent_version=None):
  """One commentary feed row for an agent reply to this packet, or None if empty.

  A reply that isn't v2 JSON (for example from the v1 agent) is kept as plain
  text, the way read_stream has always written it.
  """
  clock, ev = packet["broadcast_metadata"]["clock"], packet["event"]
  fields = parse_reply(reply)
  text = render_text(fields) if fields else reply.strip()
  if not text:
    return None
  return {
      "clock": clock["display_time"],
      "text": text,
      **(fields or {}),
      "event_id": packet.get("event_id"),
      "period": clock["period"],
      "reason": reason,
      "event_type": ev["type"],
      "actor_id": ev["actor"]["id"],
      "team_id": ev.get("team_id"),
      "agent": agent_name,
      "agent_version": agent_version,
      "written_at": datetime.now(timezone.utc).isoformat(),
  }


def read_rows(path, pos):
  """JSON rows added after byte offset pos, and the new offset.

  A half-written last line waits for the next call; lines that are not JSON
  objects are skipped.
  """
  if not os.path.exists(path):
    return [], pos
  with open(path, "rb") as f:
    f.seek(pos)
    data = f.read()
  cut = data.rfind(b"\n") + 1
  rows = []
  for line in data[:cut].decode("utf-8", "replace").splitlines():
    try:
      row = json.loads(line)
    except ValueError:
      continue
    if isinstance(row, dict):
      rows.append(row)
  return rows, pos + cut


def append_row(row, path):
  """Appends a row as one ASCII line in a single write (the GUI reads cp1252)."""
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "a") as f:
    f.write(json.dumps(row) + "\n")


def write_row(packet, reply, reason=None, agent_name=None, agent_version=None,
              path=COMMENTARY_PATH):
  """to_row plus append_row, skipping empty replies; returns the row or None."""
  row = to_row(packet, reply, reason, agent_name, agent_version)
  if row:
    append_row(row, path)
  return row
