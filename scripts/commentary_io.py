"""Play-by-play v2: the reply schema and the commentary feed rows.

The agent answers with the JSON fields in FIELDS. to_row turns a reply into one
line of football_data/commentary.jsonl that today's GUI can still show (it reads
only "clock" and "text"); the other fields are for personalised views. Standard
library only, so it is safe to import anywhere.
"""
import json
import os
from datetime import datetime, timezone

import paths

COMMENTARY_PATH = os.path.join(paths.DATA_DIR, "commentary.jsonl")
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


def parse_reply(reply):
  """The reply's fields as stripped strings, or None if it isn't a v2 reply."""
  try:
    data = json.loads(reply)
  except ValueError:
    return None
  if not isinstance(data, dict):
    return None
  if not all(isinstance(data.get(name), str) for name in FIELDS):
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
