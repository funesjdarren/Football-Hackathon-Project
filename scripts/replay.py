"""Plays a recorded match back into a feed folder at its recorded pace.

Rows from every feed in the recording (packets, commentary, State of the Game and
Fan Voice) are written to OUT_DIR in the order and with the gaps they were first
written, so real agent delays are kept. The match window reads OUT_DIR as if the
match were live. Standard library only: no Azure, nothing written to the repo.

  python scripts/replay.py demo/syn-000123 OUT_DIR [--speed 2] [--start-at 60:00]
"""
import argparse
import json
import os
import time
from datetime import datetime

FEEDS = ("match_data.jsonl", "commentary.jsonl", "state_of_game.jsonl",
         "fan_voice.jsonl", "fan_voice_state.jsonl")


def read_lines(path):
  """The non-blank lines of a JSONL file, stripped."""
  with open(path, encoding="utf-8") as f:
    return [line.strip() for line in f if line.strip()]


def timeline(src):
  """(time, file name, line) for every recorded row, in the order to replay them.

  Packets carry timestamp_utc and agent rows written_at. A row without either
  (older feeds) goes out with the first packet showing its match clock.
  """
  rows, clock_time = [], {}
  for line in read_lines(os.path.join(src, FEEDS[0])):
    meta = json.loads(line)["broadcast_metadata"]
    stamp = datetime.fromisoformat(meta["timestamp_utc"])
    clock_time.setdefault(meta["clock"]["display_time"], stamp)
    rows.append((stamp, FEEDS[0], line))
  for name in FEEDS[1:]:
    path = os.path.join(src, name)
    for line in read_lines(path) if os.path.exists(path) else []:
      row = json.loads(line)
      at = row.get("written_at")
      stamp = datetime.fromisoformat(at) if at else clock_time.get(row.get("clock"))
      if stamp:
        rows.append((stamp, name, line))
  return sorted(rows, key=lambda r: r[0])


def replay(src, out_dir, speed=1.0, start_at=None):
  """Writes the recording to out_dir; rows before start_at go out at once."""
  os.makedirs(out_dir, exist_ok=True)
  rows = timeline(src)
  t0 = rows[0][0]
  if start_at:
    t0 = next((stamp for stamp, name, line in rows if name == FEEDS[0]
               and json.loads(line)["broadcast_metadata"]["clock"]["display_time"]
               >= start_at), t0)
  began = time.monotonic()
  for stamp, name, line in rows:
    wait = (stamp - t0).total_seconds() / speed - (time.monotonic() - began)
    if wait > 0:
      time.sleep(wait)
    with open(os.path.join(out_dir, name), "a", encoding="utf-8") as f:
      f.write(line + "\n")


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("recording", help="a folder made by record_demo.py")
  parser.add_argument("out_dir", help="the feed folder the match window reads")
  parser.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
  parser.add_argument("--start-at", metavar="MM:SS", help="jump to this match clock")
  args = parser.parse_args()
  replay(args.recording, args.out_dir, args.speed, args.start_at)
  print("Replay finished.", flush=True)


if __name__ == "__main__":
  main()
