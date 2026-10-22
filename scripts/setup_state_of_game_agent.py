"""Creates the State of the Game color commentator agent in Azure AI Foundry.

Copies the model and settings of the play-by-play agent (AGENT_NAME), swaps in
the color commentator instructions, leaves out web search, and saves the new
agent's name to .env as STATE_OF_GAME_AGENT_NAME. Safe to re-run.
"""

import argparse
import json
import os
import re
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from dotenv import dotenv_values, load_dotenv, set_key

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")
ENV_KEY = "STATE_OF_GAME_AGENT_NAME"
DEFAULT_AGENT_NAME = "MS-Football-Hackathon-State-of-Game"

# Used only if the play-by-play agent can't be read
FALLBACK_DEFINITION = {
    "kind": "prompt",
    "model": "gpt-6-luna",
    "reasoning": {"effort": "low"},
}
# The teams are fictional and share surnames with real players, so no web search
NOT_COPIED = ("instructions", "tools", "tool_choice")

# Foundry rule: alphanumeric ends, hyphens inside, at most 63 characters
NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

AGENT_DESCRIPTION = (
    "State of the Game color commentator: short big-picture match updates for"
    " casual fans."
)
AGENT_INSTRUCTIONS = """\
You are the "State of the Game" color commentator for Inside the Game, a live \
football (soccer) companion for casual fans and complete beginners. A separate \
play-by-play commentator already explains every individual moment. Your job is \
the bigger picture: the score and how it happened, who is on top right now, and \
what to watch for next.

Every message is a JSON object of live match data plus an "instruction" field. \
Always follow the instruction, including its numbered points and its word limit.

Ground rules:
1. Use only facts and numbers from the JSON. Never invent players, injuries, \
substitutions, formations, managers, quotes or history.
2. The teams and players in these matches (such as FC Alpha and Beta United) are \
fictional. Some surnames match real footballers, but they are not those people. \
Never bring in real-world knowledge about any club or player.
3. Talk about the flow of the match, not the single latest play.
4. "last_10_minutes" is the recent spell and "box_score" is the whole match. The \
best story is often where they disagree, or where the score disagrees with \
expected goals ("finishing_vs_xg").
5. Use at most three numbers, rounded and said in everyday words ("about one and \
a half expected goals", "six in every ten passes").
6. Explain any jargon in plain words someone at their first match would \
understand: for example, xG (expected goals) is how many goals a team's chances \
would normally produce, and a red card means a player is sent off and their team \
plays on with one player fewer.
7. Early in a match the numbers are small. Say it is too early to judge rather \
than over-reading them.
8. Do not repeat "previous_take"; move the story on.
9. Plain text only: no markdown, asterisks, headings, bullet points, numbered \
lists, emojis or hashtags. Never mention JSON, fields, data feeds or that you are \
an AI. If some data looks odd, leave it out.
10. Sound like a warm, upbeat TV pundit chatting to a friend watching their \
first match, and stay neutral between the teams.
"""


def source_definition(project_client, source_name):
  """Settings to copy from the play-by-play agent's latest version."""
  reason = "AGENT_NAME is not set"
  if source_name:
    try:
      latest = project_client.agents.get(agent_name=source_name).versions.latest
      source = latest.definition.as_dict()
      copied = {k: v for k, v in source.items() if k not in NOT_COPIED}
      tools = [t.get("type") for t in source.get("tools") or []]
      print(
          f"Copying {sorted(copied)} from '{source_name}' version {latest.version}"
          f" (model {copied.get('model')}, reasoning {copied.get('reasoning')});"
          f" not copying its tools {tools}."
      )
      return copied
    except HttpResponseError as exc:
      reason = f"Could not read '{source_name}' ({exc!r})"
  print(f"{reason}; using gpt-6-luna with low reasoning.")
  return dict(FALLBACK_DEFINITION)


def build_definition(base):
  return PromptAgentDefinition({**base, "instructions": AGENT_INSTRUCTIONS})


def is_up_to_date(current, wanted):
  current = current.as_dict()
  return not current.get("tools") and all(
      current.get(k) == v for k, v in wanted.as_dict().items()
  )


def save_agent_name(name):
  if dotenv_values(ENV_PATH).get(ENV_KEY) == name:
    return
  try:
    set_key(ENV_PATH, ENV_KEY, name, quote_mode="never")
    print(f"Saved {ENV_KEY}={name} to .env")
  except OSError:
    print(f"Could not write .env - add this line yourself: {ENV_KEY}={name}")


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument(
      "--force", action="store_true", help="add a new version even if unchanged"
  )
  parser.add_argument(
      "--dry-run", action="store_true", help="print the definition; no Azure calls"
  )
  args = parser.parse_args()

  load_dotenv(ENV_PATH)
  name = os.getenv(ENV_KEY) or DEFAULT_AGENT_NAME
  source_name = os.getenv("AGENT_NAME")
  if not NAME_PATTERN.match(name):
    raise ValueError(f"{ENV_KEY} '{name}' is not a valid Foundry agent name")
  if name == source_name:
    raise ValueError(
        f"{ENV_KEY} must differ from AGENT_NAME ('{source_name}'), or the"
        " play-by-play agent would be overwritten"
    )

  if args.dry_run:
    print(f"Agent name: {name}")
    print(json.dumps(build_definition(FALLBACK_DEFINITION).as_dict(), indent=2))
    return

  project_endpoint = os.getenv("PROJECT_ENDPOINT")
  if not project_endpoint:
    raise ValueError("PROJECT_ENDPOINT must be set in .env file")

  # Connect to Azure AI Foundry project
  credential = DefaultAzureCredential(
      exclude_environment_credential=True,
      exclude_managed_identity_credential=True,
  )
  project_client = AIProjectClient(
      credential=credential, endpoint=project_endpoint
  )

  try:
    wanted = build_definition(source_definition(project_client, source_name))
    try:
      current = project_client.agents.get(agent_name=name).versions.latest
    except ResourceNotFoundError:
      current = None

    if current is not None and not args.force and is_up_to_date(
        current.definition, wanted
    ):
      print(f"'{name}' is already up to date (version {current.version}).")
    else:
      created = project_client.agents.create_version(
          name,
          definition=wanted,
          description=AGENT_DESCRIPTION,
          metadata={"role": "state-of-the-game", "paired_with": source_name or ""},
      )
      print(
          f"Created '{created.name}' version {created.version}"
          f" (model {wanted.get('model')}, no tools)."
      )
  except HttpResponseError as exc:
    print(
        f"Foundry call failed: {exc.message}. Check az login and that you can"
        " create agents in this project.",
        file=sys.stderr,
    )
    sys.exit(1)

  save_agent_name(name)


if __name__ == "__main__":
  main()
