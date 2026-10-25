"""Optional checks for the play-by-play v2 reply format. Safe to delete.

Run from the repo root (writes only to pytest's temp folder, no network):
  python -B -m pytest -p no:cacheprovider tests/test_commentary_io.py -q
"""
import json
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import commentary_io as cio  # noqa: E402
import generate_events as g  # noqa: E402

INSTRUCTIONS = os.path.join(ROOT, "agents", "play_by_play.md")
REPLY = {
    "headline": "Ashgrove's long shot saved",
    "casual": "FC Alpha's striker shot from distance; the keeper got down well.",
    "analyst": "Shot from 24 m (xG 0.04) with space outside the low block.",
    "why_it_matters": "Still 0-0 at 38', so Alpha are starting to test the keeper.",
    "eli5_term": "Low block",
    "eli5_definition": "The defending team sits deep near its own goal and waits.",
}


@pytest.fixture(scope="module")
def shot_packet():
  return next(p for p in g.simulate(random.Random(1)) if p["event"]["type"] == "Shot")


def gui_line(line):
  """What match_gui.py does with each commentary line (it reads only these keys)."""
  entry = json.loads(line)
  return f"\n[{entry['clock']}] {entry['text']}\n"


def test_schema_is_strict():
  assert cio.SCHEMA["required"] == list(cio.FIELDS) == list(cio.SCHEMA["properties"])
  assert cio.SCHEMA["additionalProperties"] is False
  assert all(p == {"type": "string"} for p in cio.SCHEMA["properties"].values())


def test_parse_reply_strips_and_drops_extra_keys():
  padded = {k: f"  {v} " for k, v in REPLY.items()}
  assert cio.parse_reply(json.dumps({**padded, "extra": "x"})) == REPLY


@pytest.mark.parametrize("reply", [
    "1. **What happened:** a long shot",  # v1-style markdown
    "",
    json.dumps([REPLY]),
    json.dumps({k: v for k, v in REPLY.items() if k != "analyst"}),
    json.dumps({**REPLY, "analyst": 3}),
    json.dumps({**REPLY, "headline": "   "}),
])
def test_parse_reply_rejects(reply):
  assert cio.parse_reply(reply) is None


def test_render_text():
  text = cio.render_text(REPLY)
  assert text.startswith("Ashgrove's long shot saved. FC Alpha's striker")
  assert "Why it matters: Still 0-0" in text
  assert text.endswith("\nLow block: " + REPLY["eli5_definition"])
  bare = {**REPLY, "headline": "Saved!", "why_it_matters": "", "eli5_term": ""}
  assert cio.render_text(bare) == "Saved! " + REPLY["casual"]


def test_to_row_v2(shot_packet):
  row = cio.to_row(shot_packet, json.dumps(REPLY), reason="SHOT: Saved",
                   agent_name="PBP", agent_version="1")
  ev = shot_packet["event"]
  assert row["clock"] == shot_packet["broadcast_metadata"]["clock"]["display_time"]
  assert row["text"] == cio.render_text(REPLY)
  assert {k: row[k] for k in cio.FIELDS} == REPLY
  assert row["event_id"] == shot_packet["event_id"]
  assert (row["event_type"], row["actor_id"], row["team_id"]) == (
      "Shot", ev["actor"]["id"], ev["team_id"])
  assert (row["reason"], row["agent"], row["agent_version"]) == (
      "SHOT: Saved", "PBP", "1")
  assert row["written_at"].endswith("+00:00")


def test_to_row_keeps_plain_text_replies(shot_packet):
  row = cio.to_row(shot_packet, "\n1. **Shot** from distance.\n")
  assert row["text"] == "1. **Shot** from distance."
  assert not set(cio.FIELDS) & set(row)
  assert cio.to_row(shot_packet, "  \n") is None


def test_write_row_is_ascii_and_gui_readable(tmp_path, shot_packet):
  path = tmp_path / "football_data" / "commentary.jsonl"
  spanish = "Gol de cabeza, un remate preciso \u2013 \u00a1qu\u00e9 golazo!"
  accented = {**REPLY, "casual": spanish}
  for reply in (json.dumps(REPLY), "  ", json.dumps(accented)):
    cio.write_row(shot_packet, reply, path=str(path))  # the empty reply is skipped
  data = path.read_bytes()
  assert data.isascii() and data.endswith(b"\n")
  lines = data.decode("ascii").splitlines()
  assert len(lines) == 2
  assert "\u00a1qu\u00e9 golazo!" in gui_line(lines[1])


def test_instructions_file():
  with open(INSTRUCTIONS, "rb") as f:
    raw = f.read()
  assert raw.isascii() and b"\r" not in raw
  text = raw.decode("ascii")
  assert "\\\n" not in text  # sent to the agent verbatim: no line-continuation marks
  for name in cio.FIELDS:
    assert f'"{name}"' in text
  assert "fictional" in text and "Inside the Game" not in text
