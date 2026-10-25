"""Viewer profiles: who is watching, in which mode and in which language.

A profile (profiles/<name>.json) picks casual or analyst text, a favourite club
and player, the one stat the viewer cares about most, and a language. Korean
viewers read Fan Voice's translated feeds instead of the English ones. Standard
library only, so the GUI and the judge replay can use it without Azure.
"""
import json
import os

import paths

PROFILES_DIR = os.path.join(paths.REPO_ROOT, "profiles")
DEFAULT_PROFILE = "casual_en"  # opened when no --profile is given
DEFAULT = {  # the value of any field a profile file leaves out
    "display_name": "Viewer", "mode": "casual", "language": "en",
    "favourite_club": None, "favourite_player_id": None,
    "focus_metric": "shots_on_target",
}
# The commentary and State of the Game feeds each language reads
FEEDS = {
    "en": ("commentary.jsonl", "state_of_game.jsonl"),
    "ko": ("fan_voice.jsonl", "fan_voice_state.jsonl"),
}
# Created in the feed folder once a window shows a translated language; Fan Voice
# makes no agent calls before it exists
WANTED_FLAG = "fan_voice_wanted"
METRICS = {
    "xg": "xG", "shots": "Shots", "shots_on_target": "Shots on target",
    "passes_completed": "Passes completed", "fouls": "Fouls",
    "possession": "Possession",
}
# The GUI's fixed labels in other languages (English is the key itself)
UI = {
    "ko": {
        "Live AI Commentary": "실시간 AI 해설",
        "State of the Game": "경기 흐름",
        "Live Match Analytics": "경기 분석",
        "Player Card": "선수 카드",
        "Your metric": "나의 지표",
        "Your team": "응원 팀",
        "Your player": "응원 선수",
        "Possession": "점유율",
        "Shots (on target)": "슈팅 (유효 슈팅)",
        "Shots on target": "유효 슈팅",
        "Crowd": "관중 열기",
        "Click a player to see their card.": "선수를 클릭하면 선수 카드가 보입니다.",
        "Waiting for the first State of the Game update...":
            "첫 경기 흐름 업데이트를 기다리는 중...",
        "casual": "캐주얼",
        "analyst": "분석",
        "Starting commentary in this language: catching up on the match so far...":
            "한국어 해설을 시작합니다. 지금까지의 경기를 따라잡는 중...",
        "This recording has no feed in this language.":
            "이 녹화에는 한국어 피드가 없습니다.",
        # Player Card lines: the {names} are filled in from the card
        "Passes {done}/{tried}": "패스 {done}/{tried}",
        "Shots {shots} ({on} on target)": "슈팅 {shots} (유효 {on})",
        "Goals {goals}": "골 {goals}",
        "Top speed {kmh} km/h": "최고 속도 {kmh} km/h",
        "Cards: {yellow} yellow, {red} red": "카드: 옐로 {yellow}, 레드 {red}",
    },
}


def load_profile(name=None):
  """The named profile (DEFAULT_PROFILE if None) from profiles/, with defaults."""
  with open(os.path.join(PROFILES_DIR, f"{name or DEFAULT_PROFILE}.json"),
            encoding="utf-8") as f:
    return {**DEFAULT, **json.load(f)}


def profile_names():
  """The profiles in profiles/, by file name without .json."""
  return sorted(name.removesuffix(".json") for name in os.listdir(PROFILES_DIR)
                if name.endswith(".json"))


def ui(profile, text):
  return UI.get(profile["language"], {}).get(text, text)


def feed_paths(profile, data_dir):
  """(commentary, state of the game) feed paths for the profile's language."""
  names = FEEDS.get(profile["language"], FEEDS["en"])
  return tuple(os.path.join(data_dir, name) for name in names)


def row_text(row, profile):
  """The commentary line this viewer sees, tagged when it is about their team."""
  text = row["text"]
  if profile["mode"] == "analyst" and row.get("analyst"):
    text = f"{row['headline'].rstrip('.!?')}. {row['analyst']}"
    if row.get("why_it_matters"):
      text += f" Why it matters: {row['why_it_matters']}"
  for key, field, tag in (("favourite_player_id", "actor_id", "Your player"),
                          ("favourite_club", "team_id", "Your team")):
    if profile[key] and row.get(field) == profile[key]:
      return f"[{ui(profile, tag)}] {text}"
  return text


def team_stats(packet, side):
  """One side's box score, plus the window's formatted stats, by key."""
  stats = packet["live_match_stats"]
  team = packet["broadcast_metadata"]["scoreboard"][side]["team"]
  box = stats["box_score"][team]
  done, tried = box["passes_completed"], box["passes_attempted"]
  return {
      **box,
      "xg": f"{box['xg']:.2f}",
      "possession": f"{stats['possession_pct'][team]}%",
      "shots_and_on_target": f"{box['shots']} ({box['shots_on_target']})",
      "pass_accuracy": f"{round(100 * done / tried) if tried else 0}%",
  }


def stat_lines(packet, profile):
  """Three plain stats for casual viewers; the full set for analysts."""
  home, away = team_stats(packet, "home"), team_stats(packet, "away")

  def line(label, key):
    return f"{ui(profile, label)}: {home[key]} - {away[key]}"

  crowd = packet["broadcast_metadata"]["crowd_intensity_index"]
  lines = [line("Possession", "possession"),
           line("Shots (on target)", "shots_and_on_target"),
           f"{ui(profile, 'Crowd')}: {crowd}%"]
  if profile["mode"] == "analyst":
    lines += [line("xG", "xg"), line("Pass accuracy", "pass_accuracy"),
              line("Fouls", "fouls")]
  return lines


def metric_text(packet, profile):
  """The "Your metric" tile: the profile's stat, for their club if they have one."""
  key = profile["focus_metric"]
  label = f"{ui(profile, 'Your metric')} - {ui(profile, METRICS.get(key, key))}"
  board = packet["broadcast_metadata"]["scoreboard"]
  team = {side: board[side]["team"] for side in ("home", "away")}
  value = {side: team_stats(packet, side)[key] for side in ("home", "away")}
  for mine, other in (("home", "away"), ("away", "home")):
    if board[mine]["team_id"] == profile["favourite_club"]:
      return f"{label}: {team[mine]} {value[mine]} ({team[other]} {value[other]})"
  return f"{label}: {team['home']} {value['home']} - {value['away']} {team['away']}"


def card_lines(card, profile):
  """The Player Card text for one player (match_intel.MatchIntel.player_card)."""
  if card is None:
    return [ui(profile, "Click a player to see their card.")]

  def say(template, **values):
    return ui(profile, template).format(**values)

  passes = say("Passes {done}/{tried}", done=card["passes_completed"],
               tried=card["passes_attempted"])
  if card["completion_pct"] is not None:
    passes += f" ({card['completion_pct']:.0f}%)"
  shots = say("Shots {shots} ({on} on target)", shots=card["shots"],
              on=card["shots_on_target"])
  lines = [
      f"{card['name']} - {card['role']}, {card['team']}",
      f"{passes}   {shots}   {say('Goals {goals}', goals=card['goals'])}",
      f"{say('Top speed {kmh} km/h', kmh=card['top_speed_kmh'])}   "
      + say("Cards: {yellow} yellow, {red} red", yellow=card["yellow"],
            red=card["red"]),
  ]
  if profile["mode"] == "analyst":  # "-" until the player has passed or shot
    difficulty = card["avg_pass_difficulty"]
    best = card["max_shot_speed_kmh"]
    lines.append(
        f"xG {card['xg']:.2f}   Avg pass difficulty "
        f"{'-' if difficulty is None else difficulty}"
        f"   Elite passes {card['elite_passes']}"
        f"   Best shot {f'{best} km/h' if best else '-'}"
    )
  return lines
