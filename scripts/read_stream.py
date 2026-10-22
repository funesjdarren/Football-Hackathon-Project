import json
import math
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
project_endpoint = os.getenv("PROJECT_ENDPOINT")
agent_name = os.getenv("AGENT_NAME")

if not project_endpoint or not agent_name:
  raise ValueError("PROJECT_ENDPOINT and AGENT_NAME must be set in .env file")

# Connect to Azure AI Foundry project and agent
credential = DefaultAzureCredential(
    exclude_environment_credential=True,
    exclude_managed_identity_credential=True,
)
project_client = AIProjectClient(
    credential=credential, endpoint=project_endpoint
)
openai_client = project_client.get_openai_client()
agent = project_client.agents.get(agent_name=agent_name)


def read_events(lines):
  """Reads JSON events from stdin.

  Handles both single-line JSONL streams and multi-line indented blocks.
  """
  buffer = []
  depth = 0
  inside_object = False

  for line in lines:
    stripped = line.strip()
    if not stripped:
      continue

    # Fast path: Single-line JSONL
    if stripped.startswith("{") and stripped.endswith("}"):
      try:
        yield json.loads(stripped)
        continue
      except json.JSONDecodeError:
        pass  # Fall through to multi-line buffering if corrupted or split

    # Multi-line handling with brace depth tracking
    for ch in stripped:
      if ch == "{":
        depth += 1
        inside_object = True
      elif ch == "}":
        depth -= 1

    if inside_object:
      buffer.append(line)

    if inside_object and depth == 0:
      raw_json = "".join(buffer)
      buffer = []
      inside_object = False
      try:
        yield json.loads(raw_json)
      except json.JSONDecodeError as err:
        print(f"Skipping malformed JSON chunk: {err}", file=sys.stderr)


def extract_tactical_spatial_metrics(
    tracking_data, ball_pos, attacking_team, defending_team
):
  """Distills 22 player coordinates into tactical summary metrics for the agent."""
  if not tracking_data or "players" not in tracking_data:
    return {}

  players = tracking_data["players"]
  ball_x, ball_y = ball_pos

  # 1. Local Numerical Overload Check (within 15m radius of the ball)
  attackers_near_ball = 0
  defenders_near_ball = 0

  for p in players:
    dist = math.hypot(p["x"] - ball_x, p["y"] - ball_y)
    if dist <= 15.0:
      if p["team"] == attacking_team:
        attackers_near_ball += 1
      elif p["team"] == defending_team:
        defenders_near_ball += 1

  # 2. Defending Team Compactness (Width & Depth)
  defending_outfield = [
      p
      for p in players
      if p["team"] == defending_team and p.get("role") != "GK"
  ]
  if defending_outfield:
    def_xs = [p["x"] for p in defending_outfield]
    def_ys = [p["y"] for p in defending_outfield]
    def_width = round(max(def_ys) - min(def_ys), 1)  # Horizontal spread
    def_depth = round(max(def_xs) - min(def_xs), 1)  # Vertical compression
  else:
    def_width, def_depth = None, None

  return {
      "local_duel_ratio": f"{attackers_near_ball}v{defenders_near_ball}",
      "overload_status": (
          "Attacking Overload"
          if attackers_near_ball > defenders_near_ball
          else (
              "Defensive Overload"
              if defenders_near_ball > attackers_near_ball
              else "Even Numbers"
          )
      ),
      "defending_compactness_width_m": def_width,
      "defending_depth_compression_m": def_depth,
  }


def should_generate_commentary(packet):
  """Filters the event stream for tactically significant and high-leverage moments."""
  meta = packet.get("broadcast_metadata", {})
  ev = packet.get("event", {})
  details = ev.get("details", {})
  ev_type = ev.get("type")

  # 1. Critical Broadcast Triggers
  if meta.get("commentary_trigger") in [
      "GOAL_SCORED",
      "BOOKING_YELLOW",
      "SENDING_OFF_RED",
  ]:
    return True, f"KEY EVENT: {meta['commentary_trigger']}"

  # 2. Shots
  if ev_type == "Shot":
    return True, f"SHOT: {details.get('outcome')}"

  # 3. Tactical Foil / Turnovers
  if ev_type == "Tackle" and details.get("outcome") == "Ball Won":
    return True, "TACTICAL FOIL: Ball Won"

  if ev_type == "Foul":
    return True, "TACTICAL FOIL: Foul Committed"

  # 4. Turnovers from incomplete passes in final third
  if (
      ev_type == "Pass"
      and details.get("outcome") == "Incomplete"
      and packet.get("tactical_context", {}).get("phase_of_play")
      == "Final Third Attack"
  ):
    return True, "TACTICAL FOIL: Attack Broken Down"

  # 5. Pace Bursts / High Crowd Momentum
  if ev.get("actor_speed_kmh", 0) > 28.0:
    return True, "HIGH TEMPO SPRINT"

  if meta.get("crowd_intensity_index", 0) >= 80:
    return True, "HIGH INTENSITY / CHANCE CREATION"

  return False, None


def send_to_agent(context_packet):
  """Sends structured tactical and spatial telemetry to the Azure agent."""
  response = openai_client.responses.create(
      input=json.dumps(context_packet),
      extra_body={
          "agent_reference": {"name": agent.name, "type": "agent_reference"}
      },
  )
  return response.output_text


def main():
  print("Listening to broadcast event stream on stdin...", file=sys.stderr)

  for packet in read_events(sys.stdin):
    if "broadcast_metadata" not in packet or "event" not in packet:
      continue

    should_call, reason = should_generate_commentary(packet)
    if not should_call:
      continue

    clock = packet["broadcast_metadata"]["clock"]["display_time"]
    home = packet["broadcast_metadata"]["scoreboard"]["home"]
    away = packet["broadcast_metadata"]["scoreboard"]["away"]
    tactics = packet["tactical_context"]
    ev = packet["event"]

    # Resolve ball position for spatial analysis
    coords = ev.get("details", {}).get("coordinates", {})
    ball_pos = coords.get("start", [60.0, 40.0])

    spatial_metrics = extract_tactical_spatial_metrics(
        tracking_data=packet.get("player_tracking"),
        ball_pos=ball_pos,
        attacking_team=tactics["possession_team"],
        defending_team=tactics["defending_team"],
    )

    print(
        f"\n--- [{clock}] {home['team']} {home['score']} - {away['score']}"
        f" {away['team']} ({reason}) ---",
        flush=True,
    )

    commentary_input = {
        "match_clock": clock,
        "score": (
            f"{home['team']} {home['score']} - {away['score']} {away['team']}"
        ),
        "attacking_team": tactics["possession_team"],
        "defending_team": tactics["defending_team"],
        "phase_of_play": tactics["phase_of_play"],
        "defensive_structure": tactics["defensive_structure"],
        "defensive_line_height_m": tactics["defensive_line_height_meters"],
        "pressure_distance_m": tactics["pressure_distance_to_ball_meters"],
        "event_type": ev["type"],
        "actor": ev["actor"]["name"],
        "actor_role": ev["actor"]["role"],
        "actor_speed_kmh": ev["actor_speed_kmh"],
        "event_details": ev["details"],
        "spatial_tactics": spatial_metrics,
        "possession_share": packet["live_match_stats"]["possession_pct"],
        "instruction": (
            "Explain: 1. What the attacking team was attempting. "
            "2. How the defending team attempted to foil it. "
            "3. An ELI5 (beginner-friendly) definition of any tactic mentioned."
        ),
    }

    try:
      commentary = send_to_agent(commentary_input)
      print(f"COMMENTARY:\n{commentary}\n", flush=True)
      # --- WRITE TO GUI FEED ---
      comm_path = os.path.join("football_data", "commentary.jsonl")
      with open(comm_path, "a") as cf:
        cf.write(
            json.dumps({"clock": clock, "text": commentary.strip()}) + "\n"
        )
    except Exception as exc:
      print(f"Agent call failed: {exc!r}", file=sys.stderr)


if __name__ == "__main__":
  main()