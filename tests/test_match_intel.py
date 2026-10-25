"""Optional checks for match_intel.py. Safe to delete: the app never uses them.

Run from the repo root (writes nothing, no network):
  python -B -m pytest -p no:cacheprovider tests/test_match_intel.py -q
"""
import functools
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import generate_events as g  # noqa: E402
import match_intel as mi  # noqa: E402

SEEDS = [1, 3, 9]  # 3 has a second-yellow red, 9 a straight red


@functools.cache
def match(seed):
  """One match's packets, a MatchIntel fed all of them, and what observe returned."""
  packets = list(g.simulate(random.Random(seed), match_id=f"syn-{seed:06d}"))
  intel = mi.MatchIntel()
  returned = [m for p in packets for m in intel.observe(p)]
  return packets, intel, returned


def lineup_ids(packets):
  teams = packets[0]["match_metadata"]["teams"].values()
  return [p["id"] for team in teams for p in team["lineup"]]


def milestones(intel, kind, key="clock"):
  return [(m[key], m["player_id"]) for m in intel.milestones if m["kind"] == kind]


@pytest.mark.parametrize("seed", SEEDS)
def test_cards_and_milestones_match_the_packets(seed):
  packets, intel, returned = match(seed)
  last = packets[-1]
  cards = [intel.player_card(pid) for pid in lineup_ids(packets)]
  for team, box in last["live_match_stats"]["box_score"].items():
    mine = [c for c in cards if c["team"] == team]
    assert len(mine) == 11
    for key in box.keys() - {"xg"}:  # the box score sums unrounded xG
      assert sum(c[key] for c in mine) == box[key]
  for side in last["broadcast_metadata"]["scoreboard"].values():
    assert sum(c["goals"] for c in cards if c["team"] == side["team"]) == side["score"]
  assert returned == intel.milestones

  goals, firsts, shown, second = [], {}, [], 0
  for p in packets:
    ev, meta = p["event"], p["broadcast_metadata"]
    if meta["commentary_trigger"] == "GOAL_SCORED":
      goals.append((meta["clock"]["display_time"], ev["actor"]["id"]))
    if ev["type"] == "Shot" and ev["details"]["outcome"] in ("Goal", "Saved"):
      firsts.setdefault(ev["team"], ev["actor"]["id"])
    if ev["type"] == "Foul":
      shown.append(ev["details"]["card_issued"])
      second += ev["details"]["second_yellow"]
  assert milestones(intel, "goal") == goals
  assert sorted(milestones(intel, "first_on_target", "team")) == sorted(firsts.items())
  kinds = [m["kind"] for m in intel.milestones]
  assert kinds.count("yellow") == shown.count("Yellow")
  assert kinds.count("red") == shown.count("Red") == sum(c["red"] for c in cards)
  assert sum(c["yellow"] for c in cards) == shown.count("Yellow") + second
  card = intel.player_card(milestones(intel, "fastest_shot")[-1][1])
  assert card["share_of_match_max"]["shot_speed"] == 1.0
  assert card["max_shot_speed_kmh"] >= mi.FAST_SHOT_KMH


@pytest.mark.parametrize("seed", SEEDS[:2])
def test_pass_quality(seed):
  packets = match(seed)[0]
  teams = packets[0]["match_metadata"]["teams"].values()
  direction = {t["name"]: t["attacking_direction"] for t in teams}
  labels = set()
  for p in packets:
    ev, q = p["event"], mi.pass_quality(p)
    if ev["type"] != "Pass":
      assert q is None
      continue
    d = q["difficulty"]
    assert isinstance(d, int) and 0 <= d <= 100
    assert q["label"] == ("Elite" if d >= 65 else "Tricky" if d >= 45 else "Routine")
    labels.add(q["label"])
    coords, tactics = ev["details"]["coordinates"], p["tactical_context"]
    forward = (coords["end"][0] - coords["start"][0]) * direction[ev["team"]]
    assert q["progress_m"] == round(forward, 1)
    if ev["details"]["outcome"] == "Complete":  # the same defenders as the packet's
      assert q["pressure_m"] == tactics["pressure_distance_to_ball_meters"]
  assert labels == {"Routine", "Tricky", "Elite"}


def test_unknown_player_and_empty_card():
  packets, intel, _ = match(1)
  assert intel.player_card(None) is None and intel.player_card("XYZ-999") is None
  fresh = mi.MatchIntel()
  assert fresh.player_card("FCA-101") is None
  assert fresh.observe(packets[0]) == []
  card = fresh.player_card("FCA-101")
  assert card["completion_pct"] is None and card["avg_pass_difficulty"] is None
  assert card["share_of_match_max"] == {"shot_speed": 0.0, "pass_speed": 0.0}


def test_second_match_starts_fresh():
  second, fresh, _ = match(3)
  for skip in (0, 1):  # reset by the KICK_OFF, then by the match_id alone
    intel = mi.MatchIntel()
    for p in match(1)[0] + second[skip:]:
      intel.observe(p)
    assert intel.milestones == fresh.milestones
    for pid in lineup_ids(second):
      assert intel.player_card(pid) == fresh.player_card(pid)


def test_top_speed_is_the_players_fastest_moment():
  """Tracking frames all hit the sprint cap, so cards use the actor's own speed."""
  packets, intel, _ = match(1)
  best = {}
  for p in packets:
    pid = p["event"]["actor"]["id"]
    if pid in intel.players:
      best[pid] = max(best.get(pid, 0), p["event"]["actor_speed_kmh"])
  assert all(intel.player_card(pid)["top_speed_kmh"] == best.get(pid, 0)
             for pid in intel.players)
  assert len({card["top_speed_kmh"] for card in intel.players.values()}) > 5
