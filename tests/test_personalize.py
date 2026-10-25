"""Optional checks for viewer profiles and the profile-aware window. Safe to delete.

Run from the repo root (no network):
  python -B -m pytest -p no:cacheprovider tests/test_personalize.py -q
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
import match_intel  # noqa: E402
import personalize as pz  # noqa: E402

REPLY = json.dumps({
    "headline": "Garrow scores", "casual": "FC Alpha lead.",
    "analyst": "13 m, xG 0.13.", "why_it_matters": "First goal.",
    "eli5_term": "xG", "eli5_definition": "Chance quality.",
})
# A hand-made moment with round numbers, to pin the exact stat formats
STATS = {
    "broadcast_metadata": {"crowd_intensity_index": 71, "scoreboard": {
        "home": {"team": "FC Alpha", "team_id": "FCA"},
        "away": {"team": "Beta United", "team_id": "BTU"}}},
    "live_match_stats": {
        "possession_pct": {"FC Alpha": 58, "Beta United": 42},
        "box_score": {
            "FC Alpha": {"shots": 5, "shots_on_target": 2, "xg": 0.5,
                         "passes_completed": 9, "passes_attempted": 12, "fouls": 3},
            "Beta United": {"shots": 1, "shots_on_target": 0, "xg": 0.125,
                            "passes_completed": 0, "passes_attempted": 0,
                            "fouls": 1},
        }},
}


@pytest.fixture(scope="module")
def match():
  return list(g.simulate(random.Random(1)))


def goal_row(match):
  goal = next(p for p in match
              if p["broadcast_metadata"]["commentary_trigger"] == "GOAL_SCORED")
  return cio.to_row(goal, REPLY)


def test_profiles_load_with_defaults():
  assert pz.load_profile() == pz.load_profile("casual_en")  # no --profile given
  names = {}
  for name in ("casual_en", "analyst_en", "casual_ko"):
    profile = pz.load_profile(name)
    assert set(profile) == set(pz.DEFAULT) and profile["mode"] in ("casual", "analyst")
    names[name] = profile["display_name"]
  assert names == {"casual_en": "FC Alpha casual fan", "analyst_en": "Analyst",
                   "casual_ko": "Beta United casual fan (Korean)"}
  korean = pz.load_profile("casual_ko")
  assert pz.feed_paths(korean, "d") == (os.path.join("d", "fan_voice.jsonl"),
                                        os.path.join("d", "fan_voice_state.jsonl"))
  assert pz.profile_names() == ["analyst_en", "casual_en", "casual_ko"]
  assert pz.ui(korean, "State of the Game") != "State of the Game"
  assert pz.ui(korean, "not a label") == "not a label"


def test_row_text_by_mode_and_tags(match):
  row = goal_row(match)  # FC Alpha's E. Garrow (FCA-110) scores
  casual, analyst = pz.load_profile("casual_en"), pz.load_profile("analyst_en")
  assert pz.row_text(row, casual) == f"[Your player] {row['text']}"
  assert pz.row_text(row, analyst) == (
      "Garrow scores. 13 m, xG 0.13. Why it matters: First goal.")
  fan = dict(casual, favourite_player_id="FCA-109")
  assert pz.row_text(row, fan).startswith("[Your team] ")
  assert pz.row_text({"clock": "1:00", "text": "a v1 row"}, analyst) == "a v1 row"


def test_stat_formats():
  analyst = dict(pz.DEFAULT, mode="analyst")
  assert pz.stat_lines(STATS, analyst) == [
      "Possession: 58% - 42%", "Shots (on target): 5 (2) - 1 (0)", "Crowd: 71%",
      "xG: 0.50 - 0.12", "Pass accuracy: 75% - 0%", "Fouls: 3 - 1"]
  assert pz.metric_text(STATS, pz.DEFAULT) == (
      "Your metric - Shots on target: FC Alpha 2 - 0 Beta United")
  fan = dict(pz.DEFAULT, focus_metric="xg", favourite_club="BTU")
  assert pz.metric_text(STATS, fan) == (
      "Your metric - xG: Beta United 0.12 (FC Alpha 0.50)")
  korean = pz.load_profile("casual_ko")  # possession, for Beta United
  ko = {k: pz.ui(korean, k) for k in ("Your metric", "Possession")}
  assert pz.metric_text(STATS, korean) == (
      f"{ko['Your metric']} - {ko['Possession']}: Beta United 42% (FC Alpha 58%)")
  assert pz.stat_lines(STATS, korean)[0] == f"{ko['Possession']}: 58% - 42%"


def test_stats_metric_and_card(match):
  last = match[-1]
  casual, analyst = pz.load_profile("casual_en"), pz.load_profile("analyst_en")
  assert len(pz.stat_lines(last, casual)) == 3
  assert len(pz.stat_lines(last, analyst)) == 6
  assert pz.metric_text(last, casual).startswith(
      "Your metric - Shots on target: FC Alpha ")
  text = pz.metric_text(last, analyst)
  assert " FC Alpha " in text and "Beta United" in text
  intel = match_intel.MatchIntel()
  for packet in match:
    intel.observe(packet)
  card = intel.player_card("FCA-110")
  assert len(pz.card_lines(card, casual)) == 3
  assert len(pz.card_lines(card, analyst)) == 4
  assert pz.card_lines(None, casual) == ["Click a player to see their card."]


def test_card_in_korean_and_before_any_pass(match):
  intel = match_intel.MatchIntel()
  for packet in match[:40]:
    intel.observe(packet)
  keeper = pz.card_lines(intel.player_card("FCA-101"), pz.load_profile("analyst_en"))
  assert "Avg pass difficulty -" in keeper[-1] and keeper[-1].endswith("Best shot -")
  korean = pz.card_lines(intel.player_card("BTU-110"), pz.load_profile("casual_ko"))
  assert korean[1].startswith("패스 ") and "카드: 옐로" in korean[2]


def test_window_follows_the_profile(tmp_path, match):
  tk = pytest.importorskip("tkinter")
  import match_gui  # noqa: E402
  try:
    root = tk.Tk()
  except tk.TclError:
    pytest.skip("no desktop session")
  root.withdraw()
  try:
    app = match_gui.FootballMatchGUI(root, pz.load_profile("casual_ko"),
                                     str(tmp_path), replay=True)
    assert app.commentary_file_path.endswith("fan_voice.jsonl")
    for packet in match[:300:5]:
      app.render_frame(packet)
    row = {"clock": "1:00", "text": "Korean row", "actor_id": "BTU-110"}
    with open(app.commentary_file_path, "w") as f:
      f.write(json.dumps(row) + "\n")
    app.poll_data_feed()
    assert "[" + pz.ui(app.profile, "Your player") + "] Korean row" in \
        app.comm_text.get("1.0", "end")
    player = app.players[0]
    x, y = app.to_screen_coords(player["x"], player["y"])
    app.focus_player(type("Click", (), {"x": x, "y": y}))
    assert app.focus_id == player["player_id"]

    # The header dropdown (by display name) re-reads the feeds as another viewer
    assert sorted(app.choices.values()) == pz.profile_names()  # no extra default
    cio.append_row(goal_row(match), str(tmp_path / "commentary.jsonl"))
    for name, expected in (("analyst_en", "Garrow scores. 13 m, xG 0.13."),
                           ("casual_ko", "Korean row"),
                           ("casual_en", "Garrow scores. FC Alpha lead.")):
      app.profile_var.set(pz.load_profile(name)["display_name"])
      app.switch_profile()
      app.poll_data_feed()
      assert expected in app.comm_text.get("1.0", "end"), name
      assert (app.tactic_box.winfo_manager() == "pack") == (name == "analyst_en")
      assert app.font == ("Malgun Gothic" if name == "casual_ko" else "Segoe UI")
    assert app.profile == pz.load_profile("casual_en")
    assert not (tmp_path / pz.WANTED_FLAG).exists()  # a replay never wakes Fan Voice
  finally:
    root.destroy()


def test_live_window_wakes_fan_voice_only_for_korean(tmp_path):
  tk = pytest.importorskip("tkinter")
  import match_gui  # noqa: E402
  try:
    root = tk.Tk()
  except tk.TclError:
    pytest.skip("no desktop session")
  root.withdraw()
  try:
    app = match_gui.FootballMatchGUI(root, pz.load_profile("casual_en"), str(tmp_path))
    assert not (tmp_path / pz.WANTED_FLAG).exists()
    app.profile_var.set(pz.load_profile("casual_ko")["display_name"])
    app.switch_profile()
    assert (tmp_path / pz.WANTED_FLAG).exists()
    assert pz.ui(app.profile, "Starting commentary in this language: catching up on"
                 " the match so far...") in app.comm_text.get("1.0", "end")
  finally:
    root.destroy()
