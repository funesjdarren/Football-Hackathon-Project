"""Saves the last live run as a demo recording that replays without Azure.

Copies the feed files in football_data/ into demo/<match_id>/ with a manifest of
the agent versions that wrote them (from agents.lock.json). Run it right after a
live run of scripts/run_match.py; replay with run_match.py --replay.
"""
import argparse
import json
import os
import shutil
from datetime import datetime, timezone

import paths
import replay

DEMO_DIR = os.path.join(paths.REPO_ROOT, "demo")
# foundry_client.LOCK_PATH too, but importing it would pull in Azure
LOCK_PATH = os.path.join(paths.REPO_ROOT, "agents.lock.json")


def record(data_dir=paths.DATA_DIR, demo_dir=DEMO_DIR):
  """Copies the feeds into demo_dir/<match_id>/ and returns that folder."""
  packets = replay.read_lines(os.path.join(data_dir, replay.FEEDS[0]))
  match_id = json.loads(packets[0])["match_id"]
  if json.loads(packets[-1])["event"]["type"] != "FULL_TIME":
    print(f"Warning: {match_id} did not reach full time; recording it anyway.")
  dest = os.path.join(demo_dir, match_id)
  shutil.rmtree(dest, ignore_errors=True)  # an earlier take of this match goes
  os.makedirs(dest)
  files = {}
  for name in replay.FEEDS:
    src = os.path.join(data_dir, name)
    if not os.path.exists(src):
      continue
    shutil.copyfile(src, os.path.join(dest, name))
    rows = replay.read_lines(src)
    files[name] = len(rows)
    v1_rows = sum('"agent_version"' not in row for row in rows)
    if name == "commentary.jsonl" and v1_rows:
      print(f"Warning: {v1_rows} commentary rows are not from the v2 agent.")
  lock = {}
  if os.path.exists(LOCK_PATH):
    with open(LOCK_PATH, encoding="utf-8") as f:
      lock = json.load(f)
  manifest = {
      "match_id": match_id, "recorded_at": datetime.now(timezone.utc).isoformat(),
      "files": files, "agents": lock,
  }
  with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
    f.write(json.dumps(manifest, indent=2) + "\n")
  return dest


def main():
  argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
  dest = record()
  print(f"Saved {dest}. Replay it with: python scripts/run_match.py --replay {dest}")


if __name__ == "__main__":
  main()
