"""Optional checks for demo recording and replay. Safe to delete.

Run from the repo root (no network; writes only to pytest's temp folder):
  python -B -m pytest -p no:cacheprovider tests/test_replay.py -q
"""
import json
import os
import random
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import generate_events as g  # noqa: E402
import record_demo  # noqa: E402
import replay  # noqa: E402


@pytest.fixture(scope="module")
def packets():
  return [p for _, p in zip(range(400), g.simulate(random.Random(2)))]


def write_feeds(folder, packets):
  """A small recording: packets, a timed commentary row and an untimed state row."""
  folder.mkdir(parents=True, exist_ok=True)
  rows = "".join(json.dumps(p) + "\n" for p in packets)
  (folder / "match_data.jsonl").write_text(rows)
  mid = packets[200]["broadcast_metadata"]
  (folder / "commentary.jsonl").write_text(json.dumps(
      {"clock": mid["clock"]["display_time"], "text": "row",
       "written_at": mid["timestamp_utc"], "agent_version": "2"}) + "\n")
  (folder / "state_of_game.jsonl").write_text(json.dumps(
      {"clock": packets[100]["broadcast_metadata"]["clock"]["display_time"],
       "text": "take", "label": "15'"}) + "\n")


def test_replay_writes_every_row_in_order(tmp_path, packets):
  write_feeds(tmp_path / "rec", packets)
  replay.replay(str(tmp_path / "rec"), str(tmp_path / "out"), speed=1e6)
  for name in ("match_data.jsonl", "commentary.jsonl", "state_of_game.jsonl"):
    out, rec = tmp_path / "out" / name, tmp_path / "rec" / name
    assert out.read_text() == rec.read_text()
  order = [name for _, name, _ in replay.timeline(str(tmp_path / "rec"))]
  assert order.index("state_of_game.jsonl") < order.index("commentary.jsonl")


def test_start_at_jumps_ahead(tmp_path, packets):
  write_feeds(tmp_path / "rec", packets)
  last_clock = packets[-1]["broadcast_metadata"]["clock"]["display_time"]
  began = time.monotonic()
  replay.replay(str(tmp_path / "rec"), str(tmp_path / "out"), start_at=last_clock)
  assert time.monotonic() - began < 2
  assert len((tmp_path / "out" / "match_data.jsonl").read_text().splitlines()) == 400


def test_record_demo(tmp_path, packets, capsys):
  write_feeds(tmp_path / "data", packets)
  with open(tmp_path / "data" / "commentary.jsonl", "a") as f:
    f.write(json.dumps({"clock": "00:00", "text": "a v1 row"}) + "\n")
  dest = record_demo.record(str(tmp_path / "data"), str(tmp_path / "demo"))
  manifest = json.loads(open(os.path.join(dest, "manifest.json")).read())
  assert manifest["match_id"] == packets[0]["match_id"] == os.path.basename(dest)
  assert manifest["files"] == {"match_data.jsonl": 400, "commentary.jsonl": 2,
                               "state_of_game.jsonl": 1}
  for name in manifest["files"]:
    copy, src = os.path.join(dest, name), tmp_path / "data" / name
    assert open(copy).read() == src.read_text()
  out = capsys.readouterr().out
  assert "did not reach full time" in out and "1 commentary rows" in out


def test_record_demo_replaces_an_earlier_take(tmp_path, packets):
  write_feeds(tmp_path / "data", packets)
  dest = record_demo.record(str(tmp_path / "data"), str(tmp_path / "demo"))
  with open(os.path.join(dest, "fan_voice_state.jsonl"), "w") as f:
    f.write("{}\n")  # left over from an earlier take with a Korean window
  record_demo.record(str(tmp_path / "data"), str(tmp_path / "demo"))
  assert not os.path.exists(os.path.join(dest, "fan_voice_state.jsonl"))
