"""Optional checks for the one-command driver. Safe to delete.

Run from the repo root (no network; the end-to-end run uses a temp copy):
  python -B -m pytest -p no:cacheprovider tests/test_run_match.py -q
Close any running match first: the driver refuses to start next to one.
"""
import ast
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import paths  # noqa: E402
import run_match as rm  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
  monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path / "football_data"))
  monkeypatch.setattr(paths, "ENV_PATH", str(tmp_path / ".env"))
  monkeypatch.setattr(rm, "strays", lambda: [])
  for key in rm.LIVE_KEYS:
    monkeypatch.delenv(key, raising=False)
  os.makedirs(paths.DATA_DIR)
  return tmp_path


def test_imports_need_no_azure():
  """The driver (and so the judge replay) imports only stdlib-only modules."""
  local = set()
  for script in ("run_match.py", "paths.py", "personalize.py", "replay.py",
                 "record_demo.py"):
    with open(os.path.join(SCRIPTS, script), encoding="utf-8") as f:
      tree = ast.parse(f.read())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree)
              if isinstance(n, ast.ImportFrom)}
    local |= names - set(sys.stdlib_module_names)
  assert local == {"paths", "personalize", "record_demo", "replay"}


def options(**kw):
  return rm.argparse.Namespace(**{"offline": False, "no_gui": True, "replay": None,
                                  "profile": None, **kw})


def test_preflight_names_missing_settings_but_never_values(sandbox):
  live = options()
  assert all(k in " ".join(rm.preflight(live)) for k in rm.LIVE_KEYS)
  (sandbox / ".env").write_text(
      "".join(f'{k}="https://secret.example"\n' for k in rm.LIVE_KEYS))
  assert not any("secret" in p or "Missing settings" in p for p in rm.preflight(live))
  (sandbox / ".env").unlink()
  assert not any("Missing settings" in p for p in rm.preflight(options(offline=True)))
  nobody = rm.preflight(options(profile=["nobody"]))
  assert any("No profile 'nobody'" in p for p in nobody)
  replay_problems = rm.preflight(options(replay=str(sandbox / "none")))
  assert any("No recording" in p for p in replay_problems)
  assert not any("Missing settings" in p for p in replay_problems)
  recording = sandbox / "rec"  # a recording made without a Korean window
  recording.mkdir()
  (recording / "manifest.json").write_text("{}")
  korean = rm.preflight(options(replay=str(recording), profile=["casual_ko"]))
  assert any("no Korean feeds" in p for p in korean)


def test_archive_moves_only_non_empty_feeds(sandbox):
  data = sandbox / "football_data"
  (data / "match_data.jsonl").write_text("{}\n")
  (data / "commentary.jsonl").write_text("")
  rm.archive_feeds("stamp")
  assert (data / "archive" / "stamp" / "match_data.jsonl").read_text() == "{}\n"
  assert not (data / "match_data.jsonl").exists()
  assert (data / "commentary.jsonl").exists()


def test_full_time_seen(sandbox):
  state = sandbox / "football_data" / "state_of_game.jsonl"
  assert not rm.full_time_seen()
  state.write_text('{"label": "Kick-off"}\nnot json\n')
  assert not rm.full_time_seen()
  state.write_text('{"label": "Full-time (updated)"}\n')
  assert rm.full_time_seen()
  # --keep-history: the last match's Full-time row is before this kick-off
  assert not rm.full_time_seen(state.stat().st_size)


def test_stop_all_stops_children(monkeypatch):
  monkeypatch.setattr(rm.signal, "signal", lambda *args: None)
  sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
  procs = {name: subprocess.Popen(sleep, **rm.GROUP) for name in ("a", "b")}
  stopped = set()
  rm.stop_all(procs, stopped)
  assert stopped == {"a", "b"} and all(p.poll() is not None for p in procs.values())


def test_offline_match_end_to_end(tmp_path):
  pytest.importorskip("azure.ai.projects")
  pytest.importorskip("dotenv")
  shutil.copytree(SCRIPTS, tmp_path / "scripts",
                  ignore=shutil.ignore_patterns("__pycache__"))
  result = subprocess.run(
      [sys.executable, "-B", "scripts/run_match.py", "--offline", "--no-gui",
       "--speed", "0", "--seed", "7"],
      cwd=tmp_path, capture_output=True, text=True, timeout=300)
  assert result.returncode == 0, result.stdout + result.stderr
  data = tmp_path / "football_data"
  packets = (data / "match_data.jsonl").read_text().splitlines()
  assert json.loads(packets[-1])["event"]["type"] == "FULL_TIME"
  labels = [json.loads(line)["label"]
            for line in (data / "state_of_game.jsonl").read_text().splitlines()]
  assert labels.count("Kick-off") == 1 and labels[-1].startswith("Full-time")
  (run_json,) = data.glob("logs/*/run.json")
  run = json.loads(run_json.read_text())
  assert run["seed"] == 7 and run["exit_codes"]["generator"] == 0
  assert run["stopped_by_driver"] == ["state_of_game"]
  assert not (tmp_path / "demo").exists()  # placeholder output is never saved
