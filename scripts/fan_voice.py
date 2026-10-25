"""Fan Voice: retells key moments in the viewer's language (Korean).

A second Foundry agent retells the English play-by-play and State of the Game
rows for a casual fan. Each reply must keep the source's numbers and names, be
really Korean and have no markdown, or the English row is shown, tagged (EN).
It tails the feeds beside the match, so the pipeline never waits, and makes no
agent calls until a window shows Korean (the window creates WANTED_FLAG).

  python scripts/fan_voice.py              live (after setup_agents.py)
  python scripts/fan_voice.py --dry-run    placeholder rows, no Azure
"""
import argparse
import functools
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import commentary_io
import paths
import personalize

LANG = "ko"
ENV_KEY, DEFAULT_NAME = "FAN_VOICE_AGENT_NAME", "MS-Football-Hackathon-Fan-Voice"
SOURCES = {"play": "commentary.jsonl", "state": "state_of_game.jsonl"}
OUTPUTS = dict(zip(SOURCES, personalize.FEEDS[LANG]))
FIELDS = ("headline", "line", "tip")
SCHEMA = {"type": "object", "properties": {f: {"type": "string"} for f in FIELDS},
          "required": list(FIELDS), "additionalProperties": False}
IDS = ("event_id", "actor_id", "team_id", "reason")
TEAMS = ("FC Alpha", "Beta United")
NAME_RE = re.compile(r"\b[A-Z]\. [A-Z][a-z]+\b")  # the generator's "E. Garrow"
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Number words the English may use ("down to ten men"), which Korean writes as digits
NUMBER_WORDS = {word: float(n) for n, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
HANGUL_RE = re.compile("[가-힣]")
KEY_REASONS = ("KEY EVENT", "SHOT")  # play rows always retold, plus favourites
STALE_S, POLL_S = 30, 0.5
# State of the Game's fixed "next update" lines
FINAL_NEXT = "Full-time: that was the final State of the Game for this match."
NEXT_RE = re.compile(r"Next update: (.+) \(or sooner after a goal or red card\)")
HALF_NAMES = {"half-time": "하프타임", "full-time": "풀타임"}


def log(msg):
  print(f"[fan_voice] {msg}", file=sys.stderr, flush=True)


def favourites():
  """Players followed by profiles in this language: their moments are retold too."""
  profiles = [personalize.load_profile(name.removesuffix(".json"))
              for name in os.listdir(personalize.PROFILES_DIR)]
  return {p["favourite_player_id"] for p in profiles
          if p["language"] == LANG and p["favourite_player_id"]}


def wanted(kind, row, favs):
  """Every State of the Game take; v2 play rows for key moments and favourites."""
  if kind == "state":
    return True
  reason = row.get("reason") or ""
  return "headline" in row and (
      reason.startswith(KEY_REASONS) or row.get("actor_id") in favs)


def stale(kind, row):
  """A play row older than STALE_S that isn't a goal or card: shown in English."""
  written, reason = row.get("written_at"), row.get("reason") or ""
  if kind == "state" or not written or reason.startswith("KEY EVENT"):
    return False
  age = datetime.now(timezone.utc) - datetime.fromisoformat(written)
  return age.total_seconds() > STALE_S


def build_input(kind, row):
  """The Fan Voice agent's input: the English source and the names to keep."""
  if kind == "play":
    source = {k: row[k] for k in commentary_io.FIELDS if k != "analyst"}
  else:
    body, _, tip = row["text"].partition("\nFan tip:")
    source = {"label": row.get("label", ""), "body": body.strip(),
              "fan_tip": tip.strip()}
  text = " ".join(source.values())
  keep = [t for t in TEAMS if t in text] + sorted(set(NAME_RE.findall(text)))
  return {"target_language": LANG, "kind": kind, "clock": row["clock"],
          "keep_exactly": keep, "source": source}


def parse_reply(reply):
  """The reply's fields as stripped strings, or None if it isn't a valid reply."""
  try:
    data = json.loads(reply)
  except ValueError:
    return None
  if not isinstance(data, dict) or not all(
      isinstance(data.get(f), str) for f in FIELDS):
    return None
  fields = {f: data[f].strip() for f in FIELDS}
  return fields if fields["headline"] and fields["line"] else None


def numbers(text, words=False):
  """The numbers in text by value (06 is 6, 0.40 is 0.4), and number words if asked."""
  found = {float(n.replace(",", ".")) for n in NUMBER_RE.findall(text)}
  if words:
    found |= {NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text.lower())
              if w in NUMBER_WORDS}
  return found


def check(payload, fields):
  """Why a retelling can't be shown (a short reason), or None if it passes."""
  source = " ".join([payload["clock"], *payload["source"].values()])
  out = " ".join(fields.values())
  added = [f"{n:g}" for n in sorted(numbers(out) - numbers(source, words=True))]
  changed = [name for name in payload["keep_exactly"] if name not in out]
  problems = {
      f"numbers not in the source: {added}": added,
      f"names changed: {changed}": changed,
      "not Korean": not HANGUL_RE.search(out),
      "markdown": any(mark in out for mark in "*#`"),
      "too long": len(out) > len(source) + 20,  # Korean runs shorter, not longer
  }
  return next((why for why, failed in problems.items() if failed), None)


def localized_next(text):
  """State of the Game's "next update" line in Korean; unknown lines as they are."""
  if text == FINAL_NEXT:
    return "풀타임: 이번 경기의 마지막 경기 흐름 업데이트였습니다."
  match = NEXT_RE.fullmatch(text)
  if not match:
    return text
  when = HALF_NAMES.get(match[1], match[1])
  return f"다음 업데이트: {when} (골이나 레드카드가 나오면 더 일찍)"


def to_row(kind, row, fields, agent_name=None, agent_version=None):
  """The feed row the GUI shows: Korean if it passed the checks, else English (EN)."""
  out = {"clock": row["clock"], "lang": LANG if fields else "en",
         "fallback": fields is None, "agent": agent_name,
         "agent_version": agent_version,
         "written_at": datetime.now(timezone.utc).isoformat()}
  if fields is None:
    kept = {k: row[k] for k in ("label", "next", *IDS) if k in row}
    return {**out, **kept, "text": f"{row['text']} (EN)"}
  if kind == "play":
    head = fields["headline"]
    head += " " if head[-1] in ".!?" else ". "
    text = f"{head}{fields['line']}\n{fields['tip']}"
    return {**out, "text": text, **{k: row.get(k) for k in IDS}}
  return {**out, "text": f"{fields['line']}\n{fields['tip']}",
          "label": fields["headline"], "next": localized_next(row.get("next") or "")}


def retell(ask, kind, row):
  """The checked Korean fields for a row, or None to show it in English."""
  payload = build_input(kind, row)
  try:
    fields = parse_reply(ask(payload))
  except Exception as exc:  # a timeout or service error must not stop the sidecar
    fields = None
    log(f"[{row['clock']}] agent call failed: {exc!r}")
  problem = "no valid reply" if fields is None else check(payload, fields)
  if problem:
    log(f"[{row['clock']}] showing English: {problem}")
  return None if problem else fields


def dry_run(payload):
  """A placeholder reply that passes the checks, for runs without Azure."""
  source = payload["source"]
  return json.dumps({
      "headline": f"[모의] {source.get('headline') or source.get('label')}",
      "line": f"모의 번역: {source.get('casual') or source.get('body')}",
      "tip": source.get("fan_tip") or source.get("eli5_definition") or "-",
  })


def run(ask, agent_name, agent_version, from_start):
  favs = favourites()
  src = {k: os.path.join(paths.DATA_DIR, name) for k, name in SOURCES.items()}
  dst = {k: os.path.join(paths.DATA_DIR, name) for k, name in OUTPUTS.items()}
  pos = {k: 0 if from_start or not os.path.exists(p) else os.path.getsize(p)
         for k, p in src.items()}
  log(f"Fan Voice watching {paths.DATA_DIR} "
      f"(language {LANG}, favourites {sorted(favs)})")
  flag = os.path.join(paths.DATA_DIR, personalize.WANTED_FLAG)
  if not os.path.exists(flag):
    log(f"No agent calls until a window shows language {LANG}.")
  while not os.path.exists(flag):
    time.sleep(POLL_S)
  log("A window is showing this language: retelling the match so far.")
  while True:
    for kind, path in src.items():
      rows, pos[kind] = commentary_io.read_rows(path, pos[kind])
      for row in rows:
        if wanted(kind, row, favs):
          fields = None if stale(kind, row) else retell(ask, kind, row)
          commentary_io.append_row(
              to_row(kind, row, fields, agent_name, agent_version), dst[kind])
    time.sleep(POLL_S)


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--dry-run", action="store_true",
                      help="placeholder rows, no Azure")
  parser.add_argument("--from-start", action="store_true",
                      help="retell the feeds from the top instead of from now")
  args = parser.parse_args()
  ask, name, version = dry_run, None, None
  if not args.dry_run:
    import foundry_client  # only live runs need Azure
    _, client = foundry_client.connect(timeout=20.0, max_retries=1)
    name = os.getenv(ENV_KEY) or DEFAULT_NAME
    reference = foundry_client.agent_reference(name)
    ask = functools.partial(foundry_client.ask, client, reference)
    version = reference.get("version")
  try:
    run(ask, name, version, args.from_start)
  except KeyboardInterrupt:
    log("Stopped.")


if __name__ == "__main__":
  main()
