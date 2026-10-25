"""Runs a whole match from one terminal: generator, agents and the match windows.

Every part starts as a child process from the repo root. The agents connect
before kick-off, each part logs to football_data/logs/<time>/, and everything
stops when the windows close, the match is over or you press Ctrl+C. Old feed
files are moved to football_data/archive/<time>/ first, so every run starts clean.
A live match that reaches the final whistle is saved to demo/<match_id>/ for the
no-Azure replay (run_demo.cmd), unless --no-record is given.

  python scripts/run_match.py                       live (needs .env and az login)
  python scripts/run_match.py --offline             no Azure: placeholder agents
  python scripts/run_match.py --profile casual_ko   one window per --profile
  python scripts/run_match.py --replay              the newest demo/ recording
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import paths
import personalize
import record_demo
import replay

LIVE_KEYS = ("PROJECT_ENDPOINT", "AGENT_NAME", "STATE_OF_GAME_AGENT_NAME")
# Each agent runs scripts/<name>.py and is ready once its log shows this text
AGENTS = {"state_of_game": "Watching", "read_stream": "Listening",
          "fan_voice": "Fan Voice watching"}
READY_TIMEOUT_S, DRAIN_TIMEOUT_S = 90, 150
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
# Children get their own process group, so Ctrl+C reaches only this driver
GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
         else {"start_new_session": True})
SCRIPTS_RE = re.compile(r"scripts[\\/](generate_events|read_stream|state_of_game|"
                        r"fan_voice|match_gui|replay|run_match)\.py")
SIGN_IN = "Azure sign-in needed: run az login, or use --offline."
HINTS = {"az login": SIGN_IN, "CredentialUnavailable": SIGN_IN,
         "must be set in .env": "Check .env against .env.example."}


def say(msg):
  print(f"[run_match] {msg}", flush=True)


def running(*procs):
  """True while any of the children is still going (None counts as stopped)."""
  return any(p is not None and p.poll() is None for p in procs)


def wait_until(done, timeout=float("inf"), step=0.5):
  """Polls done() every step seconds until it is true or timeout seconds pass."""
  deadline = time.monotonic() + timeout
  while not done() and time.monotonic() < deadline:
    time.sleep(step)


def env_keys():
  """Names of settings in the environment or .env (values are never read out)."""
  keys = {k for k, v in os.environ.items() if v}
  if os.path.exists(paths.ENV_PATH):
    with open(paths.ENV_PATH, encoding="utf-8") as f:
      keys |= {line.split("=", 1)[0].strip() for line in f
               if "=" in line and not line.lstrip().startswith("#")}
  return keys


def strays():
  """Process ids of match scripts left running by an earlier run (Windows only)."""
  if os.name != "nt":
    return []
  # UTF-8 output, so a command line like C:\Users\Müller\... can't break decoding
  query = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
           " Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" |"
           " ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }")
  out = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                       capture_output=True, encoding="utf-8", errors="replace",
                       timeout=30).stdout
  mine = {str(os.getpid()), str(os.getppid())}
  return [line.split()[0] for line in out.splitlines()
          if SCRIPTS_RE.search(line) and line.split()[0] not in mine]


def preflight(args):
  """Problems that would stop the run, as messages (empty when all is well)."""
  problems = []
  have = set(LIVE_KEYS) if args.offline or args.replay else env_keys()
  missing = [k for k in LIVE_KEYS if k not in have]
  if missing:
    problems.append(f"Missing settings {', '.join(missing)} (environment or"
                    f" {paths.ENV_PATH}): copy .env.example to .env, or use --offline.")
  modules = ["azure", "dotenv"] * (not args.replay) + ["tkinter"] * (not args.no_gui)
  missing = [m for m in modules if importlib.util.find_spec(m) is None]
  if missing:
    problems.append(f"Missing packages {', '.join(missing)}:"
                    " pip install -r requirements.txt")
  languages = set()
  for name in args.profile or []:
    if os.path.exists(os.path.join(personalize.PROFILES_DIR, f"{name}.json")):
      languages.add(personalize.load_profile(name)["language"])
    else:
      problems.append(f"No profile '{name}' in {personalize.PROFILES_DIR}")
  if args.replay and not os.path.exists(os.path.join(args.replay, "manifest.json")):
    problems.append(f"No recording at {args.replay}: make one with record_demo.py")
  elif args.replay and "ko" in languages and not os.path.exists(
      os.path.join(args.replay, "fan_voice_state.jsonl")):
    problems.append(f"{args.replay} has no Korean feeds: record a live run that had a"
                    " --profile casual_ko window")
  pids = strays()
  if pids:
    problems.append("Match scripts from an earlier run are still running; stop"
                    f" them with: Stop-Process -Id {','.join(pids)}")
  return problems


def archive_feeds(stamp):
  dest = os.path.join(paths.DATA_DIR, "archive", stamp)
  moved = []
  for name in replay.FEEDS:
    src = os.path.join(paths.DATA_DIR, name)
    if os.path.exists(src) and os.path.getsize(src):
      os.makedirs(dest, exist_ok=True)
      os.replace(src, os.path.join(dest, name))
      moved.append(name)
  if moved:
    say(f"Moved the last run's {', '.join(moved)} to {dest}")


def launch(name, script, args, log_dir, stdout=None):
  """Starts scripts/<script>; its output goes to <log_dir>/<name>.log."""
  pipe = name == "read_stream"  # it reads the generator's packets on stdin
  with open(os.path.join(log_dir, f"{name}.log"), "ab") as log:
    return subprocess.Popen(
        [sys.executable, os.path.join(paths.REPO_ROOT, "scripts", script), *args],
        cwd=paths.REPO_ROOT, env=CHILD_ENV, stdout=stdout or log, stderr=log,
        stdin=subprocess.PIPE if pipe else subprocess.DEVNULL, **GROUP)


def launch_windows(args, procs, log_dir, *extra):
  """Opens a match window per --profile (or one default window); returns them."""
  if args.no_gui:
    return []
  for profile in args.profile or [None]:
    name = f"gui-{profile}" if profile else "gui"
    opts = ["--profile", profile] if profile else []
    procs[name] = launch(name, "match_gui.py", [*opts, *extra], log_dir)
  return [p for name, p in procs.items() if name.startswith("gui")]


def read_log(log_dir, name):
  with open(os.path.join(log_dir, f"{name}.log"), "rb") as f:
    return f.read().decode("utf-8", "replace")


def wait_ready(proc, log_dir, name):
  """True once the agent's log shows its ready text; False if it exits or stalls."""
  wait_until(lambda: AGENTS[name] in read_log(log_dir, name) or not running(proc),
             READY_TIMEOUT_S, 0.25)
  return AGENTS[name] in read_log(log_dir, name)


def explain(log_dir, name):
  """Prints why a child stopped: the end of its log and a hint if one fits."""
  tail = read_log(log_dir, name).strip().splitlines()[-8:]
  say(f"{name} stopped; the end of {name}.log:")
  print("\n".join(f"    {line}" for line in tail), flush=True)
  for clue, hint in HINTS.items():
    if any(clue in line for line in tail):
      return say(hint)


def feed_size(name):
  path = os.path.join(paths.DATA_DIR, name)
  return os.path.getsize(path) if os.path.exists(path) else 0


def new_lines(name, start=0):
  """Lines added to football_data/<name> after byte offset start."""
  path = os.path.join(paths.DATA_DIR, name)
  if not os.path.exists(path):
    return []
  with open(path, "rb") as f:
    f.seek(start)
    return f.read().decode("utf-8", "replace").splitlines()


def full_time_seen(start=0):
  """True once a Full-time take was written after start (this match's kick-off)."""
  return any('"label": "Full-time' in line
             for line in new_lines("state_of_game.jsonl", start))


def stop_all(procs, stopped):
  """Stops every child still running and adds its name to the stopped set."""
  signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl+C can't cut this short
  live = {name: p for name, p in procs.items() if running(p)}
  for proc in live.values():
    proc.terminate()
  for proc in live.values():
    try:
      proc.wait(5)
    except subprocess.TimeoutExpired:
      proc.kill()
  stopped.update(live)


def supervise(procs, guis, log_dir, stopped, start):
  """Waits for the match to end, reporting any agent that fails early.

  Returns True if the match reached the final whistle. start holds the State of
  the Game feed sizes at kick-off, so takes left from an earlier match
  (--keep-history) don't count as this match's full time.
  """
  gen = procs["generator"]
  agents = {name: p for name, p in procs.items() if name in AGENTS}
  while running(gen):
    time.sleep(0.5)
    if guis and not running(*guis):
      say("Match windows closed.")
      return False
    for name in [n for n, p in agents.items() if not running(p)]:
      del agents[name]
      if procs[name].returncode:  # read_stream ends cleanly when the match does
        explain(log_dir, name)
  if gen.returncode:
    explain(log_dir, "generator")
  say("Final whistle: waiting for the last commentary...")
  wait_until(lambda: not running(procs.get("read_stream")) and full_time_seen(
      start["state_of_game.jsonl"]), DRAIN_TIMEOUT_S)
  watched = os.path.exists(os.path.join(paths.DATA_DIR, personalize.WANTED_FLAG))
  if "fan_voice" in procs and watched:  # until every take has its Korean row
    wait_until(lambda: len(new_lines("fan_voice_state.jsonl",
                                     start["fan_voice_state.jsonl"]))
               >= len(new_lines("state_of_game.jsonl", start["state_of_game.jsonl"])),
               45)
  stop_all({name: p for name, p in procs.items() if p not in guis}, stopped)
  return gen.returncode == 0


def run_live(args, procs, log_dir, stopped):
  names = ["state_of_game"] + ["read_stream"] * (not args.offline)
  korean = any(personalize.load_profile(p)["language"] == "ko"
               for p in args.profile or [])
  # Fan Voice retells from kick-off in a live match that will be saved (so the
  # replay has Korean all the way through) or that opens a Korean window. In a
  # practice (--no-record) or offline run it waits until a window switches to
  # Korean, which creates the flag, so it makes no calls nobody watches
  flag = os.path.join(paths.DATA_DIR, personalize.WANTED_FLAG)
  if os.path.exists(flag):
    os.remove(flag)
  from_kick_off = korean or not (args.offline or args.no_record)
  if from_kick_off:
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    open(flag, "w").close()
  if from_kick_off or not args.no_gui:
    names.append("fan_voice")
  for name in names:
    procs[name] = launch(name, f"{name}.py", ["--dry-run"] * args.offline, log_dir)
  for name in names:
    if not wait_ready(procs[name], log_dir, name):
      explain(log_dir, name)
      sys.exit(1)
  guis = launch_windows(args, procs, log_dir)
  start = {name: feed_size(name)
           for name in ("state_of_game.jsonl", "fan_voice_state.jsonl")}
  rs = procs.get("read_stream")
  gen_args = ["--seed", str(args.seed), "--speed", str(args.speed)]
  procs["generator"] = launch("generator", "generate_events.py", gen_args, log_dir,
                              stdout=rs.stdin if rs else subprocess.DEVNULL)
  if rs:
    rs.stdin.close()  # the generator now holds the only write end of the pipe
  say(f"Kick-off: seed {args.seed} (match syn-{args.seed:06d}). Logs: {log_dir}")
  finished = supervise(procs, guis, log_dir, stopped, start)
  if finished and not (args.offline or args.no_record):  # saved for run_demo.cmd
    say(f"Saved this match for the replay: {record_demo.record()}")
  if finished and guis:
    say("Match over. Close the match windows to exit.")
    wait_until(lambda: not running(*guis))


def run_replay(args, procs, log_dir):
  feed_dir = tempfile.mkdtemp(prefix="replay_")  # never written into the repo
  extra = ["--start-at", args.start_at] if args.start_at else []
  procs["replay"] = launch("replay", "replay.py", [args.replay, feed_dir, "--speed",
                           str(args.speed), *extra], log_dir)
  guis = launch_windows(args, procs, log_dir, "--data-dir", feed_dir, "--replay")
  say(f"Replaying {args.replay} into {feed_dir}. Close the match windows to exit.")
  watch = guis or [procs["replay"]]
  wait_until(lambda: not running(*watch))


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--seed", type=int, help="match seed (default: random)")
  parser.add_argument("--speed", type=float, default=1.0,
                      help="live: packets per second, 0 = no delay; replay: 2 = twice"
                      " as fast (default: 1)")
  parser.add_argument("--offline", action="store_true",
                      help="no Azure: skip play-by-play, placeholder agents")
  parser.add_argument("--profile", action="append",
                      help="open a window for this profile in profiles/ (repeatable)")
  parser.add_argument("--replay", nargs="?", const="latest", metavar="DIR",
                      help="replay a recording (default: the newest in demo/)")
  parser.add_argument("--start-at", metavar="MM:SS", help="replay: jump to this clock")
  parser.add_argument("--keep-history", action="store_true",
                      help="leave the last run's feed files in place")
  parser.add_argument("--no-gui", action="store_true", help="run without a window")
  parser.add_argument("--no-record", action="store_true",
                      help="don't save a finished live match to demo/")
  args = parser.parse_args()
  sys.stdout.reconfigure(errors="replace")
  if args.replay == "latest":  # by recording time: a fresh clone's file dates are not
    found = {}
    for path in glob.glob(os.path.join(paths.REPO_ROOT, "demo", "*", "manifest.json")):
      with open(path, encoding="utf-8") as f:
        found[json.load(f)["recorded_at"]] = os.path.dirname(path)
    args.replay = found[max(found)] if found else "demo/"
  problems = preflight(args)
  for problem in problems:
    say(problem)
  if problems:
    sys.exit(2)
  stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
  log_dir = os.path.join(paths.DATA_DIR, "logs", stamp)
  os.makedirs(log_dir)
  if args.seed is None:
    args.seed = secrets.randbelow(1_000_000)
  procs, stopped = {}, set()
  try:
    if args.replay:
      run_replay(args, procs, log_dir)
    else:
      if not args.keep_history:
        archive_feeds(stamp)
      run_live(args, procs, log_dir, stopped)
  except KeyboardInterrupt:
    say("Stopping.")
  finally:
    stop_all(procs, stopped)
    run = {"seed": None if args.replay else args.seed, "replay": args.replay,
           "offline": args.offline, "speed": args.speed, "profiles": args.profile,
           "exit_codes": {name: p.returncode for name, p in procs.items()},
           "stopped_by_driver": sorted(stopped)}
    with open(os.path.join(log_dir, "run.json"), "w", encoding="utf-8") as f:
      f.write(json.dumps(run, indent=2) + "\n")


if __name__ == "__main__":
  main()
