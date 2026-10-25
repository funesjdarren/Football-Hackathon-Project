"""Optional checks for Fan Voice, the Korean retelling sidecar. Safe to delete.

Run from the repo root (no network; the sidecar run uses --dry-run in a temp copy):
  python -B -m pytest -p no:cacheprovider tests/test_fan_voice.py -q
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import fan_voice as fv  # noqa: E402

ROW = {
    "clock": "16:42", "event_id": "syn-ev-000259", "actor_id": "FCA-110",
    "team_id": "FCA", "reason": "KEY EVENT: GOAL_SCORED",
    "text": "Garrow scores for FC Alpha. E. Garrow finishes from about 13 m.",
    "headline": "Garrow scores for FC Alpha",
    "casual": "E. Garrow finishes from about 13 m to put FC Alpha 1-0 up against"
              " Beta United.",
    "analyst": "Not retold.",
    "why_it_matters": "The first goal of the match, at 16:42.",
    "eli5_term": "xG",
    "eli5_definition": "How likely a chance is to become a goal, from 0 to 1.",
}
KOREAN = {
    "headline": "E. Garrow 골, FC Alpha 1-0 리드",
    "line": "E. Garrow가 약 13 m 거리에서 마무리해 FC Alpha가 Beta United에 1-0으로"
            " 앞섭니다.",
    "tip": "xG: 0에서 1 사이로 득점 가능성을 나타냅니다.",
}
STATE = {"clock": "45:00", "label": "Half-time",
         "next": "Next update: 60' (or sooner after a goal or red card)",
         "text": "A close first half.\nFan tip: xG means..."}


def test_build_input_keeps_names_and_leaves_out_analyst():
  payload = fv.build_input("play", ROW)
  assert payload["keep_exactly"] == ["FC Alpha", "Beta United", "E. Garrow"]
  assert "analyst" not in payload["source"] and payload["clock"] == "16:42"
  state = fv.build_input("state", STATE)["source"]
  assert state == {"label": "Half-time", "body": "A close first half.",
                   "fan_tip": "xG means..."}


def test_check_accepts_a_faithful_retelling():
  assert fv.check(fv.build_input("play", ROW), KOREAN) is None


@pytest.mark.parametrize("change, reason", [
    ({"line": KOREAN["line"] + " 2골."}, "numbers"),
    ({k: v.replace("E. Garrow", "가로우") for k, v in KOREAN.items()}, "names"),
    ({k: "Garrow scores for FC Alpha. E. Garrow 1-0 Beta United" for k in fv.FIELDS},
     "not Korean"),
    ({"tip": "**xG**: 득점 가능성 0에서 1"}, "markdown"),
    ({"line": KOREAN["line"] * 6}, "too long"),
])
def test_check_rejects(change, reason):
  assert reason in fv.check(fv.build_input("play", ROW), {**KOREAN, **change})


@pytest.mark.parametrize("line", [
    "전반 6분, Beta United는 10명으로 싸웁니다.",  # a padded clock, a number word
    "Beta United는 열 명으로 싸웁니다.",
])
def test_check_accepts_natural_korean_numbers(line):
  row = {"clock": "06:05", "label": "After the red card", "next": "",
         "text": "Beta United are down to ten men.\nFan tip: a red means sent off."}
  fields = {"headline": "레드카드 이후", "line": line, "tip": "레드카드: 퇴장입니다."}
  assert fv.check(fv.build_input("state", row), fields) is None


def test_headline_punctuation_is_not_doubled():
  text = fv.to_row("play", ROW, dict(KOREAN, headline="E. Garrow 선제골!"))["text"]
  assert text.startswith("E. Garrow 선제골! E. Garrow가")


def test_rows_korean_or_english_fallback():
  korean = fv.to_row("play", ROW, KOREAN, "FV", "1")
  assert korean["lang"] == "ko" and not korean["fallback"]
  assert korean["text"].startswith(KOREAN["headline"])
  assert korean["actor_id"] == "FCA-110"
  english = fv.to_row("play", ROW, None)
  assert english["text"] == ROW["text"] + " (EN)" and english["fallback"]
  state = fv.to_row("state", STATE, {"headline": "하프타임", "line": "팽팽한 전반.",
                                     "tip": "xG: 기대 득점"})
  assert state["label"] == "하프타임"
  assert state["next"].startswith("다음 업데이트: 60'")
  assert fv.localized_next(fv.FINAL_NEXT).startswith("풀타임:")
  assert fv.localized_next("Not a known line") == "Not a known line"


def test_wanted_rows():
  assert fv.wanted("state", STATE, set())
  assert fv.wanted("play", ROW, set())
  sprint = dict(ROW, reason="HIGH TEMPO SPRINT")
  assert not fv.wanted("play", sprint, set())
  assert fv.wanted("play", sprint, {"FCA-110"})
  v1 = {"clock": "1:00", "text": "v1 row", "reason": "SHOT"}
  assert not fv.wanted("play", v1, set())
  old = (datetime.now(timezone.utc) - timedelta(seconds=fv.STALE_S + 5)).isoformat()
  assert not fv.stale("play", dict(ROW, written_at=old))  # goals and cards: never
  assert fv.stale("play", dict(sprint, written_at=old))  # shown in English, no call
  assert not fv.stale("state", dict(STATE, written_at=old))


def test_sample_is_build_input():
  path = os.path.join(ROOT, "agents", "fan_voice.sample.json")
  with open(path, encoding="utf-8") as f:
    sample = json.load(f)
  assert sample == fv.build_input("play", dict(ROW, clock=sample["clock"]))


def test_dry_run_sidecar(tmp_path):
  for folder in ("scripts", "profiles"):
    shutil.copytree(os.path.join(ROOT, folder), tmp_path / folder,
                    ignore=shutil.ignore_patterns("__pycache__"))
  data = tmp_path / "football_data"
  data.mkdir()
  rows = [ROW, dict(ROW, reason="HIGH TEMPO SPRINT", actor_id="FCA-102"),
          {"clock": "17:00", "text": "a v1 row", "reason": "SHOT: Saved"}]
  (data / "commentary.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
  (data / "state_of_game.jsonl").write_text(json.dumps(STATE) + "\n")
  play_out, state_out = data / "fan_voice.jsonl", data / "fan_voice_state.jsonl"
  proc = subprocess.Popen([sys.executable, "-B", "scripts/fan_voice.py", "--dry-run",
                           "--from-start"], cwd=tmp_path)
  try:
    time.sleep(3)  # nobody watches Korean yet: no calls, nothing written
    assert not play_out.exists() and not state_out.exists()
    (data / "fan_voice_wanted").touch()  # a window switches to Korean
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (
        play_out.exists() and state_out.exists()):
      time.sleep(0.2)
    time.sleep(1)
  finally:
    proc.terminate()
    proc.wait(10)
  play = [json.loads(line) for line in play_out.read_text().splitlines()]
  state = state_out.read_text().splitlines()
  assert len(play) == 1 and play[0]["lang"] == "ko" and len(state) == 1
