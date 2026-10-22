"""State of the Game color commentator.

Tails football_data/match_data.jsonl and asks a second Foundry agent for a
big-picture take every 15 match minutes, at half-time and full-time, and
shortly after goals and red cards. Takes go to football_data/state_of_game.jsonl
for the GUI's State of the Game panel.
"""

import argparse
import collections
from datetime import datetime, timezone
import json
import os
import re
import sys
import time

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

MATCH_PATH = os.path.join("football_data", "match_data.jsonl")
STATE_PATH = os.path.join("football_data", "state_of_game.jsonl")

# Schedule (*_S values are match seconds; 1 match minute is ~15 real seconds unpiped)
CHECK_IN_MINUTES = {1: (15, 30), 2: (60, 75)}
HALF_END_MINUTE = {1: 45, 2: 90}
HALF_END_NAME = {1: "half-time", 2: "full-time"}
REACTION_DELAY_S = 60  # let play-by-play react to a goal / red card first
COOLDOWN_S = 6 * 60  # min gap since the previous take
ROOM_S = 6 * 60  # min gap before the next check-in or whistle
LATE_CHECK_IN_S = 60  # a check-in this late is a catch-up after a pause
MAX_CLOCK_STEP_S = 6  # generator advances 2-6 match seconds per event

# Full-time has no marker: fire once the feed goes quiet after 89:54
FT_IDLE_MIN_S = 8.0
FT_IDLE_MAX_S = 45.0

MAX_PACKET_AGE_S = 120  # older packets are history, not the live match
WINDOW_S = 10 * 60
POLL_SECONDS = 0.5
AGENT_TIMEOUT_S = 30.0
AGENT_MAX_RETRIES = 1
PREVIOUS_TAKE_CHARS = 300

LABELS = {
    "GOAL_REACTION": "After the goal",
    "RED_CARD_REACTION": "After the red card",
    "HALF_TIME": "Half-time",
    "FULL_TIME": "Full-time",
}

KICK_OFF_TEXT = (
    "Kick-off! Your State of the Game commentator checks in every 15 minutes,"
    " at half-time and full-time, and shortly after goals and red cards."
)
JOINED_TEXT = (
    "State of the Game is now following this match ({score}). The next"
    " big-picture update is on its way."
)

# (lead-in, word limit, numbered points) for each update type
INSTRUCTIONS = {
    "CHECK_IN": (
        "This is the {label}.", 45,
        "1. The story so far: the score and how it happened. 2. Who is on top"
        " right now and why, comparing 'last_10_minutes' with the whole-match"
        " 'box_score'. 3. One thing to watch before the next update.",
    ),
    "GOAL_REACTION": (
        "A goal has just changed the game.", 45,
        "1. What the goal means: a lead, an equaliser, a comeback, or game"
        " over? 2. Whether it came with or against the run of play, using"
        " 'last_10_minutes'. 3. What the team that is behind or has been"
        " pegged back needs now.",
    ),
    "RED_CARD_REACTION": (
        "A player has just been sent off.", 45,
        "1. Who was sent off and what a red card means. 2. How the game stood"
        " before it. 3. How playing with one player fewer changes the rest of"
        " the match.",
    ),
    "HALF_TIME": (
        "It is half-time.", 55,
        "1. The story of the first half. 2. The one number that best explains"
        " the score. 3. What each team needs to change after the break.",
    ),
    "FULL_TIME": (
        "The final whistle has gone.", 55,
        "1. The verdict: who won, or why it finished level. 2. The turning"
        " point, chosen from 'goals' or 'sent_off'. 3. Whether the result was"
        " fair, based on xG and shots.",
    ),
}
RULES = (
    " Plain text only, in one short paragraph, using at most three numbers."
    " Finish with one line starting 'Fan tip:' that gives an ELI5"
    " (beginner-friendly) definition of one stat or idea you used, in at most"
    " 15 words."
)


def log(msg):
  print(msg, file=sys.stderr, flush=True)


def clock_of(packet):
  """Returns (period, match seconds, display time) for a packet."""
  clock = packet["broadcast_metadata"]["clock"]
  return clock["period"], clock["minute"] * 60 + clock["second"], clock["display_time"]


def packet_ts(packet):
  """The generator's own UTC timestamp for a packet."""
  return datetime.fromisoformat(packet["broadcast_metadata"]["timestamp_utc"])


def parse_packets(lines):
  """Yields the match packets among JSON lines, skipping anything else."""
  for line in lines:
    try:
      packet = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(packet, dict) and "broadcast_metadata" in packet and "event" in packet:
      yield packet


def read_new_lines(path, pos):
  """Returns complete new lines after byte offset pos, and the new offset.

  A half-written last line is left for the next poll. If the file shrank
  (restored or recreated), reading starts again from the top.
  """
  try:
    size = os.path.getsize(path)
    if size < pos:
      log(f"{path} shrank - reading from the start")
      pos = 0
    with open(path, "rb") as f:
      f.seek(pos)
      data = f.read(size - pos)
  except OSError:
    return [], pos
  cut = data.rfind(b"\n") + 1
  return data[:cut].decode("utf-8", "replace").splitlines(), pos + cut


def sides(meta):
  return meta["scoreboard"]["home"], meta["scoreboard"]["away"]


def score_map(meta):
  return {side["team"]: side["score"] for side in sides(meta)}


def score_line(meta):
  home, away = sides(meta)
  return f"{home['team']} {home['score']} - {away['score']} {away['team']}"


def situation(meta):
  home, away = sides(meta)
  if home["score"] == away["score"]:
    return f"Level at {home['score']}-{away['score']}" if home["score"] else "Goalless"
  leader = home if home["score"] > away["score"] else away
  return f"{leader['team']} lead by {abs(home['score'] - away['score'])}"


def clean_take(text):
  """Strips markdown and keeps the body in one paragraph plus a Fan tip line."""
  text = text.replace("**", "").replace("__", "")
  lines = [re.sub(r"^\s*(#+|[-*•]|\d+[.)])\s+", "", raw) for raw in text.splitlines()]
  joined = re.sub(r"\s+", " ", " ".join(lines)).strip()
  tip_at = joined.lower().find("fan tip:")
  if tip_at > 0:
    return f"{joined[:tip_at].strip()}\n{joined[tip_at:].strip()}"
  return joined


def build_instruction(update_type, label, new_goals, new_reds):
  """Per-call instruction, in the same numbered style as read_stream.py."""
  lead, limit, points = INSTRUCTIONS[update_type]
  text = (
      f"{lead.format(label=label)} Explain in at most {limit} words in total,"
      f" including the Fan tip: {points}"
  )
  moments = [
      f"{g['clock']} goal for {g['team']} ({g['scorer']}), making it {g['score_after']}"
      for g in new_goals
  ] + [f"{r['clock']} red card for {r['team']} ({r['player']})" for r in new_reds]
  if moments:
    text += " Since the last update: " + "; ".join(moments) + "."
    if update_type == "CHECK_IN":
      text += " Lead with that."
  return text + RULES


class GameStateTracker:
  """Turns raw match packets into State of the Game takes."""

  def __init__(self):
    self.reset()

  def reset(self):
    self.latest = self.period = self.s = self.display = self.last_ts = None
    self.max_gap = 0.0
    self.marks_done = set()
    self.pending = []
    self.last_take = None
    self.goals = []
    self.sent_off = []
    self.cards = {}
    self.since_clock = "kick-off"
    self.since_goals = self.since_reds = 0
    self.history = collections.deque()
    self.previous_take = None
    self.ft_done = False
    self.ft_score = None

  def observe(self, packet):
    """Feeds one packet in; returns the takes it triggers (often none)."""
    period, s, _ = clock_of(packet)
    if self.latest is None:
      return self._start_match(packet)
    if (period, s) < (self.period, self.s):
      # Clock went backwards: a new match. Give the old one its full-time
      # first, flagged so its text doesn't seed the new match.
      take = self.finish()
      takes = [dict(take, previous_match=True)] if take else []
      return takes + self._start_match(packet)

    self.ft_done = False  # a packet after full-time means it fired early: re-arm
    takes = []
    if self.period == 1 and period == 2:
      # No half-time marker: the first second-half packet ends the half
      takes.append(self._fire("HALF_TIME", LABELS["HALF_TIME"], self.latest, (2, s)))
      self.history.clear()

    self._record_moments(packet)
    self._remember(packet)

    # The clock jumps 2-6 s, so test whether a check-in mark was crossed
    crossed = [m for m in CHECK_IN_MINUTES[period] if m not in self.marks_done and s >= m * 60]
    if crossed:
      self.marks_done.update(crossed)
      mark = max(crossed)
      late = s - mark * 60 > LATE_CHECK_IN_S
      if not takes and (not late or self._cooled(period, s)):
        takes.append(self._fire("CHECK_IN", f"{mark}' check-in", packet, (period, s)))
      return takes

    if (
        self.pending
        and s >= self.pending[0]["due_s"]
        and self._cooled(period, s)
        and self._room(period, s)
    ):
      goal = any(p["kind"] == "GOAL" for p in self.pending)
      kind = "GOAL_REACTION" if goal else "RED_CARD_REACTION"
      takes.append(self._fire(kind, LABELS[kind], packet, (period, s)))
    return takes

  def full_time_due(self, now_utc):
    """Full-time take once the feed has gone quiet after 89:54, else None."""
    if self.last_ts is None:
      return None
    idle = (now_utc - self.last_ts).total_seconds()
    threshold = min(max(2 * self.max_gap, FT_IDLE_MIN_S), FT_IDLE_MAX_S)
    return self.finish() if idle >= threshold else None

  def finish(self):
    """Full-time take without waiting for the feed to go quiet."""
    if self.latest is None or self.ft_done or not self._ft_armed():
      return None
    self.ft_done = True
    score = score_map(self.latest["broadcast_metadata"])
    if score == self.ft_score:
      log("Late packets after full-time changed nothing")
      return None
    label = "Full-time (updated)" if self.ft_score else LABELS["FULL_TIME"]
    self.ft_score = score
    return self._fire("FULL_TIME", label, self.latest, (self.period, self.s))

  def _start_match(self, packet):
    self.reset()
    period, s, display = clock_of(packet)
    meta = packet["broadcast_metadata"]
    self.cards = {team: {"yellow": 0, "red": 0} for team in score_map(meta)}
    self.marks_done = {m for m in CHECK_IN_MINUTES[period] if s >= m * 60}
    kick_off = period == 1 and s < 60
    self.since_clock = "kick-off" if kick_off else display
    self._record_moments(packet)
    self._remember(packet)
    if kick_off:
      log(f"Kick-off: {score_line(meta)}")
      return [self._take("KICK_OFF", "Kick-off", packet, text=KICK_OFF_TEXT)]
    log(
        f"Joined mid-match at {display}; earlier goals and cards are not"
        " listed (the score is still right)"
    )
    text = JOINED_TEXT.format(score=score_line(meta))
    return [self._take("JOINED", "Joined", packet, text=text)]

  def _record_moments(self, packet):
    meta = packet["broadcast_metadata"]
    trigger = meta["commentary_trigger"]
    _, s, display = clock_of(packet)
    defending = packet["tactical_context"]["defending_team"]
    if trigger == "GOAL_SCORED":
      # Possession flips after a Shot, so defending_team is only a fallback
      home, away = sides(meta)
      self.goals.append({
          "clock": display,
          "team": self._scoring_team(meta) or defending,
          "scorer": packet["event"]["actor"]["name"],
          "score_after": f"{home['score']}-{away['score']}",
      })
      self.pending.append({"kind": "GOAL", "due_s": s + REACTION_DELAY_S})
    elif trigger == "SENDING_OFF_RED":
      self.cards[defending]["red"] += 1  # fouls don't flip possession
      self.sent_off.append({
          "clock": display,
          "team": defending,
          "player": packet["event"]["details"]["offender"],
      })
      self.pending.append({"kind": "RED", "due_s": s + REACTION_DELAY_S})
    elif trigger == "BOOKING_YELLOW":
      self.cards[defending]["yellow"] += 1

  def _scoring_team(self, meta):
    if self.latest is None:
      return None
    before = score_map(self.latest["broadcast_metadata"])
    return next((t for t, score in score_map(meta).items() if score > before[t]), None)

  def _remember(self, packet):
    period, s, display = clock_of(packet)
    ts = packet_ts(packet)
    if self.last_ts:
      self.max_gap = max(self.max_gap, (ts - self.last_ts).total_seconds())
    self.last_ts = ts
    self.latest, self.period, self.s, self.display = packet, period, s, display
    box = packet["live_match_stats"]["box_score"]
    self.history.append((s, display, box, score_map(packet["broadcast_metadata"])))
    while len(self.history) > 1 and self.history[1][0] <= s - WINDOW_S:
      self.history.popleft()

  def _ft_armed(self):
    end_s = (90 + int(self.display.partition("+")[2] or 0)) * 60
    return self.period == 2 and self.s >= end_s - MAX_CLOCK_STEP_S

  def _cooled(self, period, s):
    return (
        self.last_take is None
        or self.last_take[0] != period
        or s - self.last_take[1] >= COOLDOWN_S
    )

  def _room(self, period, s):
    marks = CHECK_IN_MINUTES[period] + (HALF_END_MINUTE[period],)
    upcoming = [m * 60 for m in marks if m * 60 > s]
    return bool(upcoming) and min(upcoming) - s >= ROOM_S

  def _next_text(self, update_type):
    if update_type == "FULL_TIME":
      return "Full-time: that was the final State of the Game for this match."
    if update_type == "HALF_TIME":
      upcoming = "60'"
    else:
      left = [m for m in CHECK_IN_MINUTES[self.period] if m not in self.marks_done]
      upcoming = f"{left[0]}'" if left else HALF_END_NAME[self.period]
    return f"Next update: {upcoming} (or sooner after a goal or red card)"

  def _take(self, update_type, label, snapshot, context=None, text=None):
    meta = snapshot["broadcast_metadata"]
    return {
        "update_type": update_type,
        "label": label,
        "clock": meta["clock"]["display_time"],
        "score": score_line(meta),
        "context": context,
        "text": text,
        "next": self._next_text(update_type),
    }

  def _fire(self, update_type, label, snapshot, at):
    """Builds an agent take and records it as the latest take, at clock `at`."""
    context = self.build_context(update_type, label, snapshot)
    self.since_clock = snapshot["broadcast_metadata"]["clock"]["display_time"]
    self.since_goals, self.since_reds = len(self.goals), len(self.sent_off)
    self.pending = []
    self.last_take = at
    return self._take(update_type, label, snapshot, context=context)

  def _window(self, snapshot):
    """Shots, xG, goals and possession over roughly the last 10 match minutes."""
    _, start_display, start_box, start_score = self.history[0]
    box = snapshot["live_match_stats"]["box_score"]
    score = score_map(snapshot["broadcast_metadata"])
    passes = {t: box[t]["passes_attempted"] - start_box[t]["passes_attempted"] for t in box}
    total = sum(passes.values())
    window = {"from_clock": start_display}
    for team, stats in box.items():
      before = start_box[team]
      window[team] = {
          "shots": stats["shots"] - before["shots"],
          "shots_on_target": stats["shots_on_target"] - before["shots_on_target"],
          "xg": round(stats["xg"] - before["xg"], 2),
          "goals": score[team] - start_score[team],
          "possession_pct": round(100 * passes[team] / total) if total else 50,
      }
    return window

  def build_context(self, update_type, label, snapshot):
    """The JSON packet sent to the agent, mirroring read_stream's commentary_input."""
    meta = snapshot["broadcast_metadata"]
    clock = meta["clock"]
    stats = snapshot["live_match_stats"]
    box = stats["box_score"]
    score = score_map(meta)
    new_goals = self.goals[self.since_goals:]
    new_reds = self.sent_off[self.since_reds:]
    whistle = update_type in ("HALF_TIME", "FULL_TIME")
    return {
        "update_type": update_type,
        "update_label": label,
        "match_clock": clock["display_time"],
        "half": "First half" if clock["period"] == 1 else "Second half",
        "score": score_line(meta),
        "match_situation": situation(meta),
        "minutes_left_in_half": (
            None if whistle else max(0, HALF_END_MINUTE[clock["period"]] - clock["minute"])
        ),
        "players_on_pitch": {team: 11 - self.cards[team]["red"] for team in score},
        "possession_share": stats["possession_pct"],
        "box_score": box,
        "pass_accuracy_pct": {
            team: round(100 * t["passes_completed"] / t["passes_attempted"])
            if t["passes_attempted"]
            else None
            for team, t in box.items()
        },
        "finishing_vs_xg": {
            team: round(score[team] - t["xg"], 2) for team, t in box.items()
        },
        "last_10_minutes": self._window(snapshot),
        "since_last_update": {
            "from_clock": self.since_clock,
            "goals": new_goals,
            "red_cards": new_reds,
        },
        "goals": list(self.goals),
        "cards": {team: dict(c) for team, c in self.cards.items()},
        "sent_off": list(self.sent_off),
        "previous_take": (
            self.previous_take[:PREVIOUS_TAKE_CHARS] if self.previous_take else None
        ),
        "instruction": build_instruction(update_type, label, new_goals, new_reds),
    }


def connect_agent():
  """Connects to the State of the Game agent; returns its send function."""
  project_endpoint = os.getenv("PROJECT_ENDPOINT")
  agent_name = os.getenv("STATE_OF_GAME_AGENT_NAME")
  if not project_endpoint or not agent_name:
    raise ValueError(
        "PROJECT_ENDPOINT and STATE_OF_GAME_AGENT_NAME must be set in .env file"
        " (run python scripts/setup_state_of_game_agent.py)"
    )
  if agent_name == os.getenv("AGENT_NAME"):
    raise ValueError(
        "STATE_OF_GAME_AGENT_NAME must differ from AGENT_NAME (the play-by-play agent)"
    )

  # Connect to Azure AI Foundry project and agent
  credential = DefaultAzureCredential(
      exclude_environment_credential=True,
      exclude_managed_identity_credential=True,
  )
  project_client = AIProjectClient(
      credential=credential, endpoint=project_endpoint
  )
  openai_client = project_client.get_openai_client(
      timeout=AGENT_TIMEOUT_S, max_retries=AGENT_MAX_RETRIES
  )
  agent = project_client.agents.get(agent_name=agent_name)
  log(f"Connected to State of the Game agent '{agent.name}'.")

  def send_to_agent(context_packet):
    """Sends the state-of-the-game packet to the color commentator agent."""
    response = openai_client.responses.create(
        input=json.dumps(context_packet),
        extra_body={
            "agent_reference": {"name": agent.name, "type": "agent_reference"}
        },
    )
    return response.output_text

  return send_to_agent


def dry_run_take(context):
  """Placeholder about as long as a real take, so the panel can be sized."""
  return (
      f"[dry run] {context['update_label']} at {context['match_clock']}:"
      f" {context['score']}. This placeholder is about as long as a real take,"
      " so you can check that the whole update and its tip fit inside the"
      " panel during the demo without anyone needing to scroll.\n"
      "Fan tip: xG, or expected goals, measures how good a team's chances were."
  )


def with_context_log(ask):
  def ask_and_log(context):
    log(json.dumps(context, indent=2))
    return ask(context)

  return ask_and_log


def publish(tracker, take, ask):
  """Gets the take's text, appends it to the GUI feed, then prints it."""
  text = take["text"]
  if take["context"] is not None:
    log(f"[{take['clock']}] Asking for the {take['label']} take...")
    try:
      text = clean_take(ask(take["context"]))
    except Exception as exc:
      log(f"State of the Game agent call failed: {exc!r}")
      return
    if not text:
      log("Empty take - skipped")
      return
    if not take.get("previous_match"):
      tracker.previous_take = text

  entry = {"clock": take["clock"], "text": text, "label": take["label"], "next": take["next"]}
  os.makedirs("football_data", exist_ok=True)
  with open(STATE_PATH, "a") as f:
    f.write(json.dumps(entry) + "\n")
  print(
      f"\n--- [{take['clock']}] {take['score']} (STATE OF THE GAME:"
      f" {take['label']}) ---\nSTATE OF THE GAME:\n{text}\n",
      flush=True,
  )


def run_live(ask, pos):
  """Follows the live match from byte offset pos (end of file at startup)."""
  log(
      f"Watching {os.path.abspath(MATCH_PATH)} (skipping {pos} bytes of"
      " earlier matches)"
  )
  log(f"Writing takes to {os.path.abspath(STATE_PATH)}")
  tracker = GameStateTracker()

  while True:
    lines, pos = read_new_lines(MATCH_PATH, pos)
    skipped = 0
    for packet in parse_packets(lines):
      if (datetime.now(timezone.utc) - packet_ts(packet)).total_seconds() > MAX_PACKET_AGE_S:
        skipped += 1
        continue
      for take in tracker.observe(packet):
        publish(tracker, take, ask)
    if skipped:
      log(f"Skipped {skipped} old packets")

    take = tracker.full_time_due(datetime.now(timezone.utc))
    if take:
      publish(tracker, take, ask)
    time.sleep(POLL_SECONDS)


def run_replay(path, show_context):
  """Prints the take schedule for a saved match file. No Azure, no writes."""
  if not os.path.isfile(path):
    sys.exit(f"No such match file: {os.path.abspath(path)}")
  tracker = GameStateTracker()
  counts = []

  def report(take):
    # Every match opens with a canned kick-off / joined line, then agent takes
    if take["context"] is None:
      counts.append(0)
    else:
      counts[-1] += 1
    print(
        f"[{take['clock']:>10}] {take['update_type']:<17} {take['label']:<20}"
        f" {take['score']}"
    )
    if show_context and take["context"]:
      print(json.dumps(take["context"], indent=2))

  for packet in parse_packets(read_new_lines(path, 0)[0]):
    for take in tracker.observe(packet):
      report(take)
  take = tracker.finish()
  if take:
    report(take)
  print(f"{len(counts)} match(es); agent takes per match: {counts}")


def main():
  parser = argparse.ArgumentParser(description="State of the Game color commentator.")
  parser.add_argument(
      "--dry-run", action="store_true", help="no Azure: write placeholder takes"
  )
  parser.add_argument(
      "--replay", metavar="PATH", help="print the schedule for a saved match file"
  )
  parser.add_argument(
      "--show-context", action="store_true", help="print each take's context packet"
  )
  args = parser.parse_args()
  sys.stdout.reconfigure(errors="replace")

  try:
    if args.replay:
      run_replay(args.replay, args.show_context)
      return
    # Measured before connecting, so packets written while Azure signs in count
    start_pos = os.path.getsize(MATCH_PATH) if os.path.exists(MATCH_PATH) else 0
    ask = dry_run_take if args.dry_run else connect_agent()
    if args.show_context:
      ask = with_context_log(ask)
    run_live(ask, start_pos)
  except KeyboardInterrupt:
    log("Stopped.")


if __name__ == "__main__":
  main()
