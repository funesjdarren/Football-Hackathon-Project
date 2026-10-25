"""Optional checks for the match generator. Safe to delete: the app never uses them.

Run from the repo root (writes nothing into the repo):
  python -B -m pytest -p no:cacheprovider tests -q

These tests never import read_stream.py (it connects to Azure on import) and
make no network calls.
"""
import ast
import functools
import json
import math
import os
import random
import re
import sys

import pytest

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
sys.path.insert(0, SCRIPTS)
import generate_events as g  # noqa: E402

PHASES = {"Build-Up (Defensive Third)", "Midfield Progression", "Final Third Attack"}
EVENT_TYPES = {"Pass", "Shot", "Dribble", "Tackle", "Foul", *g.MARKER_TYPES}
TRIGGERS = {None, "GOAL_SCORED", "BOOKING_YELLOW", "SENDING_OFF_RED"}
# Every field a v1 consumer reads must still be there on every packet
V1_PATHS = """
    event_id broadcast_metadata.timestamp_utc
    broadcast_metadata.clock.period broadcast_metadata.clock.display_time
    broadcast_metadata.clock.minute broadcast_metadata.clock.second
    broadcast_metadata.scoreboard.home.team broadcast_metadata.scoreboard.home.score
    broadcast_metadata.scoreboard.away.team broadcast_metadata.scoreboard.away.score
    broadcast_metadata.var_status broadcast_metadata.crowd_intensity_index
    broadcast_metadata.commentary_trigger
    tactical_context.phase_of_play tactical_context.possession_team
    tactical_context.defending_team tactical_context.defensive_structure
    tactical_context.defensive_line_height_meters
    tactical_context.pressure_distance_to_ball_meters
    event.type event.actor.id event.actor.name event.actor.role event.actor.pos_key
    event.actor.shirt_number event.actor.cards event.actor_speed_kmh
    event.details.coordinates.start
    live_match_stats.possession_pct live_match_stats.box_score
    player_tracking.frame_rate_hz player_tracking.pitch_units player_tracking.players
""".split()


@functools.lru_cache(maxsize=8)  # a cached match holds about 20 MB
def match(seed):
  return tuple(g.simulate(random.Random(seed)))


def get(packet, path):
  for key in path.split("."):
    packet = packet[key]
  return packet


def match_seconds(packet):
  clock = packet["broadcast_metadata"]["clock"]
  return clock["period"], clock["minute"] * 60 + clock["second"]


def without_timestamps(packets):
  """Packets with the wall-clock time blanked: it is the only unseeded field."""
  return [
      dict(p, broadcast_metadata=dict(p["broadcast_metadata"], timestamp_utc=None))
      for p in packets
  ]


def markers(packets):
  return [(i, p) for i, p in enumerate(packets) if p["event"]["type"] in g.MARKER_TYPES]


def stoppage(packet):
  return int(packet["broadcast_metadata"]["clock"]["display_time"].split("+")[1])


def test_same_seed_same_match():
  first = without_timestamps(g.simulate(random.Random(3)))
  assert first == without_timestamps(g.simulate(random.Random(3)))
  assert first != without_timestamps(match(4))


def test_no_module_level_random():
  for name in ("generate_events.py", "tracking_engine.py"):
    with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
      tree = ast.parse(f.read())
    used = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "random"
    }
    assert used <= {"Random"}, (name, used)
  state = random.getstate()
  list(g.simulate(random.Random(5)))
  assert random.getstate() == state


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_packet_keeps_v1_shape(seed):
  teams = set(g.SYNTHETIC_TEAMS)
  previous = (1, 0)
  for p in match(seed):
    for path in V1_PATHS:
      get(p, path)  # a KeyError here means a v1 field went missing
    assert p["tactical_context"]["phase_of_play"] in PHASES
    assert p["event"]["type"] in EVENT_TYPES
    assert p["broadcast_metadata"]["commentary_trigger"] in TRIGGERS
    display = p["broadcast_metadata"]["clock"]["display_time"]
    assert re.fullmatch(r"\d{2}:\d{2}( \+\d)?", display), display
    now = match_seconds(p)
    assert now[0] in (1, 2) and now >= previous
    assert now[0] != previous[0] or now[1] - previous[1] <= 59
    previous = now
    assert set(p["live_match_stats"]["box_score"]) == teams
    assert set(p["live_match_stats"]["possession_pct"]) == teams


@pytest.mark.parametrize("seed", range(20))
def test_whistles_and_stoppage(seed):
  packets = match(seed)
  found = markers(packets)
  assert [p["event"]["type"] for _, p in found] == list(g.MARKER_TYPES)
  assert found[0][0] == 0 and found[-1][0] == len(packets) - 1
  half_time, second_half, full_time = (p for _, p in found[1:])
  assert 1 <= stoppage(half_time) <= 4
  assert match_seconds(half_time) == (1, (45 + stoppage(half_time)) * 60)
  assert match_seconds(second_half) == (2, 45 * 60)
  assert second_half["broadcast_metadata"]["clock"]["display_time"] == "45:00"
  assert 2 <= stoppage(full_time) <= 6
  assert match_seconds(full_time) == (2, (90 + stoppage(full_time)) * 60)
  for i, p in found:
    meta = p["broadcast_metadata"]
    assert meta["commentary_trigger"] is None
    assert meta["crowd_intensity_index"] < 80
    assert p["event"]["actor_speed_kmh"] == 0.0
    if i:
      assert p["live_match_stats"] == packets[i - 1]["live_match_stats"]


def test_kick_off_metadata():
  packets = match(1)
  meta = packets[0]["match_metadata"]
  assert all("match_metadata" not in p for p in packets[1:])
  lineup_ids = set()
  for side in ("home", "away"):
    lineup = meta["teams"][side]["lineup"]
    assert len({player["id"] for player in lineup}) == 11
    lineup_ids |= {player["id"] for player in lineup}
  first_frame = packets[0]["player_tracking"]["players"]
  assert lineup_ids == {player["player_id"] for player in first_frame}
  assert {p["match_id"] for p in packets} == {meta["match_id"]}


@pytest.mark.parametrize("seed", range(5))
def test_ids_and_possession_chain(seed):
  previous_id = 1
  for p in match(seed):
    chain = p["possession_change"]
    assert chain["to_team"] == p["tactical_context"]["possession_team"]
    assert chain["changed"] == (
        chain["from_team"] is not None and chain["from_team"] != chain["to_team"]
    )
    assert chain["possession_id"] == previous_id + chain["changed"]
    previous_id = chain["possession_id"]
    ev = p["event"]
    if ev["type"] in g.MARKER_TYPES:
      assert ev["team"] is None
    else:
      assert ev["team"] == chain["from_team"]
      assert ev["team_id"] == g.TEAM_CODES[ev["team"]]
      assert ev["actor"]["id"].startswith(ev["team_id"])
    for side in ("home", "away"):
      board = p["broadcast_metadata"]["scoreboard"][side]
      assert board["team_id"] == g.TEAM_CODES[board["team"]]


@pytest.mark.parametrize("seed", range(50))
def test_each_team_attacks_its_own_goal(seed):
  home = g.SYNTHETIC_TEAMS[0]
  for p in g.simulate(random.Random(seed)):  # streamed, not cached
    ev = p["event"]
    attacking = ev["type"] == "Shot" or (
        p["tactical_context"]["phase_of_play"] == "Final Third Attack"
    )
    if ev["team"] and attacking:
      x = ev["details"]["coordinates"]["start"][0]
      assert x >= 75.0 if ev["team"] == home else x <= 45.0, (ev["type"], ev["team"], x)


@pytest.mark.parametrize("seed", range(3))
def test_frame_is_the_event_instant(seed):
  for p in match(seed):
    ev = p["event"]
    if ev["type"] in g.MARKER_TYPES:
      continue
    actor = next(
        pl for pl in p["player_tracking"]["players"] if pl["player_id"] == ev["actor"]["id"]
    )
    start = ev["details"]["coordinates"]["start"]
    assert math.hypot(actor["x"] - start[0], actor["y"] - start[1]) <= 0.11


@pytest.mark.parametrize("seed", range(50))
def test_discipline(seed):
  sent_off = set()
  for p in g.simulate(random.Random(seed)):  # streamed, not cached
    ev = p["event"]
    ids = {pl["player_id"] for pl in p["player_tracking"]["players"]}
    ids |= {ev["actor"]["id"], ev["recipient_id"], ev["challenger_id"], ev["offender_id"]}
    assert not ids & sent_off  # a sent-off player never comes back
    if ev["type"] != "Foul":
      continue
    details = ev["details"]
    assert not ev["offender_id"].endswith("-101")  # keepers are never carded
    assert ev["offender_id"].startswith(p["tactical_context"]["defending_team_id"])
    if details["second_yellow"]:
      assert details["card_issued"] == "Red"
      assert p["broadcast_metadata"]["commentary_trigger"] == "SENDING_OFF_RED"
    if details["card_issued"] == "Red":
      sent_off.add(ev["offender_id"])


@pytest.mark.parametrize("seed", range(5))
def test_restarts(seed):
  packets = match(seed)
  for p, nxt in zip(packets, packets[1:]):
    if nxt["event"]["type"] in g.MARKER_TYPES:
      continue  # the half ended before the restart
    ev, restart = p["event"], nxt["event"]["restart"]
    outcome = ev["details"].get("outcome")
    if p["broadcast_metadata"]["commentary_trigger"] == "GOAL_SCORED":
      assert restart == "Kick-off" and nxt["event"]["type"] == "Pass"
      assert nxt["event"]["details"]["coordinates"]["start"] == [60.0, 40.0]
      conceding = p["tactical_context"]["possession_team"]
      assert nxt["event"]["team"] == conceding
      for pl in nxt["player_tracking"]["players"]:
        if pl["player_id"] == nxt["event"]["actor"]["id"]:
          continue
        assert pl["x"] <= 59.0 if pl["team"] == g.SYNTHETIC_TEAMS[0] else pl["x"] >= 61.0
        if pl["team"] != conceding:
          assert math.hypot(pl["x"] - 60.0, pl["y"] - 40.0) >= 9.15
    elif ev["type"] == "Shot" and outcome == "Missed":
      assert restart == "Goal Kick" and nxt["event"]["actor"]["pos_key"] == "GK"
      assert nxt["event"]["details"]["coordinates"]["start"][0] in (5.5, 114.5)
    elif ev["type"] == "Shot" and outcome == "Saved":
      assert restart == "Keeper Ball" and nxt["event"]["actor"]["pos_key"] == "GK"
    elif ev["type"] == "Foul":
      assert restart == "Free Kick"
      assert nxt["event"]["actor"]["id"] == ev["actor"]["id"]


def test_match_rates():
  """50-match means stay football-realistic, and goals follow xG."""
  totals = dict.fromkeys(("goals", "xg", "shots", "fouls", "yellows"), 0.0)
  seeds = range(50)
  for seed in seeds:
    for p in g.simulate(random.Random(seed)):  # streamed, not cached
      ev = p["event"]
      if ev["type"] == "Shot":
        totals["shots"] += 1
        totals["xg"] += ev["details"]["xG"]
        totals["goals"] += ev["details"]["outcome"] == "Goal"
      elif ev["type"] == "Foul":
        totals["fouls"] += 1
      totals["yellows"] += p["broadcast_metadata"]["commentary_trigger"] == "BOOKING_YELLOW"
  mean = {k: v / len(seeds) for k, v in totals.items()}
  assert 2 <= mean["goals"] <= 4, mean
  assert 20 <= mean["shots"] <= 32, mean
  assert 15 <= mean["fouls"] <= 30, mean
  assert 2 <= mean["yellows"] <= 5, mean
  assert abs(mean["goals"] - mean["xg"]) <= 1.0, mean


@pytest.mark.parametrize("seed", range(3))
def test_players_never_outrun_a_sprint(seed):
  packets = match(seed)
  for before, after in zip(packets, packets[1:]):
    (period_0, s_0), (period_1, s_1) = match_seconds(before), match_seconds(after)
    if period_0 != period_1:
      continue  # half-time
    dt = s_1 - s_0
    old = {pl["player_id"]: pl for pl in before["player_tracking"]["players"]}
    for pl in after["player_tracking"]["players"]:
      moved = math.hypot(pl["x"] - old[pl["player_id"]]["x"], pl["y"] - old[pl["player_id"]]["y"])
      # 0.15 m allows for positions being rounded to 0.1 m
      assert moved <= g.SPRINT_MPS * dt + 0.15, (after["event_id"], pl["name"], moved, dt)


@pytest.mark.parametrize("seed", range(3))
def test_pass_recipient_is_nearest_teammate(seed):
  for p in match(seed):
    ev = p["event"]
    if ev["type"] != "Pass" or ev["details"]["outcome"] != "Complete":
      continue
    end = ev["details"]["coordinates"]["end"]
    mates = [
        pl
        for pl in p["player_tracking"]["players"]
        if pl["team"] == ev["team"] and pl["player_id"] != ev["actor"]["id"]
    ]
    distance = {pl["player_id"]: math.hypot(pl["x"] - end[0], pl["y"] - end[1]) for pl in mates}
    assert distance[ev["recipient_id"]] <= min(distance.values()) + 0.15


def test_tracking_engine_rules():
  """Sprint cap, the presser's spot and line heights, on the engine directly."""
  import tracking_engine as te  # noqa: E402

  rng = random.Random(1)
  home, away = g.SYNTHETIC_TEAMS
  squads = [g.generate_squad(t, g.LAST_NAMES[i * 11:(i + 1) * 11], rng) for i, t in enumerate((home, away))]
  engine = te.TacticalTrackingEngine(home, away, squads[0], squads[1], rng=rng)

  # 1. With dt, nobody moves further than a sprint
  before = {pid: (p["x"], p["y"]) for pid, p in engine.players.items()}
  for pl in engine.update_frame(20.0, 70.0, home, dt=1.0):
    old = before[pl["player_id"]]
    assert math.hypot(pl["x"] - old[0], pl["y"] - old[1]) <= te.SPRINT_MPS + 0.15

  # 2. The defender nearest the ball stands goal-side of it, within the block's gap
  for block, (low, high) in te.PRESS_GAP_M.items():
    # The engine picks the outfield defender nearest the ball before moving
    defenders = [p for p in engine.players.values() if p["team"] == away and p["role"] != "GK"]
    presser_id = min(defenders, key=lambda p: math.hypot(p["x"] - 60.0, p["y"] - 40.0))["id"]
    frame = engine.update_frame(60.0, 40.0, home, defensive_block=block)
    presser = next(pl for pl in frame if pl["player_id"] == presser_id)
    assert presser["x"] > 60.0  # the away team defends the goal at x=120
    assert low - 0.15 <= math.hypot(presser["x"] - 60.0, presser["y"] - 40.0) <= high + 0.15

  # 3. A High Press holds a higher line than a Mid Block, and a Low Block a deeper one
  def line(block):
    heights = []
    for _ in range(10):
      engine.update_frame(60.0, 40.0, home, defensive_block=block)
      heights.append(engine.line_height(away))
    return sum(heights) / len(heights)

  assert line("High Press") > line("Mid Block") > line("Low Block (Deep Defense)")


@pytest.mark.parametrize("seed", range(5))
def test_state_of_game_schedule(seed):
  sog = pytest.importorskip("state_of_game")  # needs the azure SDK; no network call
  tracker = sog.GameStateTracker()
  packets = match(seed)
  takes = [t for p in packets for t in tracker.observe(p)] + [tracker.finish()]
  kinds = [t["update_type"] for t in takes]
  half_time, full_time = packets[markers(packets)[1][0]], packets[-1]
  assert kinds[0] == "KICK_OFF" and kinds.count("CHECK_IN") == 4
  assert [t["clock"] for t in takes if t["update_type"] == "HALF_TIME"] == [
      half_time["broadcast_metadata"]["clock"]["display_time"]
  ]
  assert kinds[-1] == "FULL_TIME"
  assert takes[-1]["clock"] == full_time["broadcast_metadata"]["clock"]["display_time"]


def read_stream_functions():
  """read_stream's filter and spatial metrics, taken from its source text."""
  with open(os.path.join(SCRIPTS, "read_stream.py"), encoding="utf-8") as f:
    tree = ast.parse(f.read())
  wanted = {"should_generate_commentary", "extract_tactical_spatial_metrics"}
  defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
  if len(defs) != len(wanted):
    pytest.skip("read_stream.py functions were renamed")
  namespace = {"math": math}
  exec(compile(ast.Module(body=defs, type_ignores=[]), "read_stream.py", "exec"), namespace)
  return namespace["should_generate_commentary"], namespace["extract_tactical_spatial_metrics"]


def commentary_input(packet, extract):
  """Mirror of the agent input read_stream.main builds, to catch missing fields."""
  meta, tactics, ev = packet["broadcast_metadata"], packet["tactical_context"], packet["event"]
  ball_pos = ev.get("details", {}).get("coordinates", {}).get("start", [60.0, 40.0])
  return {
      "match_clock": meta["clock"]["display_time"],
      "home": meta["scoreboard"]["home"]["team"],
      "away": meta["scoreboard"]["away"]["team"],
      "phase_of_play": tactics["phase_of_play"],
      "defensive_structure": tactics["defensive_structure"],
      "defensive_line_height_m": tactics["defensive_line_height_meters"],
      "pressure_distance_m": tactics["pressure_distance_to_ball_meters"],
      "event_type": ev["type"],
      "actor": ev["actor"]["name"],
      "actor_role": ev["actor"]["role"],
      "actor_speed_kmh": ev["actor_speed_kmh"],
      "event_details": ev["details"],
      "spatial_tactics": extract(
          tracking_data=packet.get("player_tracking"),
          ball_pos=ball_pos,
          attacking_team=tactics["possession_team"],
          defending_team=tactics["defending_team"],
      ),
      "possession_share": packet["live_match_stats"]["possession_pct"],
  }


def test_markers_never_trigger_read_stream():
  should, extract = read_stream_functions()
  for seed in (1, 2):
    for p in match(seed):
      triggered, _ = should(p)
      if p["event"]["type"] in g.MARKER_TYPES:
        assert not triggered
      elif triggered:
        json.dumps(commentary_input(p, extract))


def test_gui_renders_markers():
  tk = pytest.importorskip("tkinter")
  import match_gui  # noqa: E402 (only builds widgets; its file poll never runs here)

  try:
    root = tk.Tk()
  except tk.TclError:
    pytest.skip("no desktop session")
  root.withdraw()
  try:
    app = match_gui.FootballMatchGUI(root)
    packets = match(1)
    for _, p in markers(packets):
      app.render_frame(p)
    for p in packets[1:400:25]:
      app.render_frame(p)
  finally:
    root.destroy()
