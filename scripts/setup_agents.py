"""Creates this repo's Foundry agents from code, with instructions in agents/*.md.

A definition (model, instructions, JSON output format, no tools) becomes a new
version only when it differs from the latest one. The version goes into
agents.lock.json and the name into .env. The v1 play-by-play agent (AGENT_NAME)
and the State of the Game agent are never changed here. Safe to re-run.
"""

import argparse
import hashlib
import json
import os
import re
import sys

from azure.ai.projects.models import PromptAgentDefinition
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from dotenv import dotenv_values, load_dotenv, set_key
from openai import APIError, APIStatusError

import commentary_io
import fan_voice
import foundry_client
import paths

AGENTS_DIR = os.path.join(paths.REPO_ROOT, "agents")
MODEL = {"model": "gpt-6-luna", "reasoning": {"effort": "low"}}
# Foundry rule: alphanumeric ends, hyphens inside, at most 63 characters
NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
PROTECTED_KEYS = ("AGENT_NAME", "STATE_OF_GAME_AGENT_NAME")  # never given versions here
UNKNOWN_VERSION = "999999"  # --smoke checks that Foundry rejects it

# agents/<key>.md holds the instructions, agents/<key>.sample.json the --smoke input
REGISTRY = {
    "play_by_play": {
        "env_key": "PLAY_BY_PLAY_AGENT_NAME",
        "default_name": "MS-Football-Hackathon-Play-by-Play",
        "description": (
            "Play-by-play v2: one moment explained for casual fans and analysts,"
            " as strict JSON."
        ),
        "schema": commentary_io.SCHEMA,
        "parse": commentary_io.parse_reply,
    },
    "fan_voice": {
        "env_key": fan_voice.ENV_KEY,
        "default_name": fan_voice.DEFAULT_NAME,
        "description": (
            "Fan Voice: retells key moments in Korean for casual fans, checked"
            " against the English source."
        ),
        "schema": fan_voice.SCHEMA,
        "parse": fan_voice.parse_reply,
        "check": fan_voice.check,
    },
}


def agent_name(key):
  """The agent's name from .env (or its default), checked against the rules."""
  env_key = REGISTRY[key]["env_key"]
  name = os.getenv(env_key) or REGISTRY[key]["default_name"]
  if not NAME_PATTERN.match(name):
    raise ValueError(f"{env_key} '{name}' is not a valid Foundry agent name")
  for protected in PROTECTED_KEYS:
    if name == os.getenv(protected):
      raise ValueError(f"{env_key} must differ from {protected} ('{name}')")
  return name


def build_definition(key):
  with open(os.path.join(AGENTS_DIR, f"{key}.md"), encoding="utf-8") as f:
    instructions = f.read()
  schema = REGISTRY[key]["schema"]
  output_format = {"type": "json_schema", "name": key, "strict": True, "schema": schema}
  return PromptAgentDefinition(
      {**MODEL, "instructions": instructions, "text": {"format": output_format}}
  )


def fingerprint(definition):
  canonical = json.dumps(definition.as_dict(), sort_keys=True)
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_up_to_date(current, wanted):
  current = current.as_dict()
  return not current.get("tools") and all(
      current.get(k) == v for k, v in wanted.as_dict().items()
  )


def latest(project_client, name):
  try:
    return project_client.agents.get(agent_name=name).versions.latest
  except ResourceNotFoundError:
    return None


def write_lock(name, version, definition):
  lock = foundry_client.read_lock()
  entry = {
      "version": version,
      "model": definition["model"],
      "definition_sha256": fingerprint(definition),
  }
  if lock.get(name) == entry:
    return
  lock[name] = entry
  with open(foundry_client.LOCK_PATH, "w", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(lock, indent=2, sort_keys=True) + "\n")
  print(f"Recorded '{name}' version {version} in agents.lock.json")


def save_env(env_key, name):
  if dotenv_values(paths.ENV_PATH).get(env_key) == name:
    return
  try:
    set_key(paths.ENV_PATH, env_key, name, quote_mode="never")
    print(f"Saved {env_key}={name} to .env")
  except OSError:
    print(f"Could not write .env - add this line yourself: {env_key}={name}")


def create_or_update(project_client, key, name, force):
  wanted = build_definition(key)
  current = latest(project_client, name)
  if current is not None and not force and is_up_to_date(current.definition, wanted):
    version = current.version
    print(f"'{name}' is already up to date (version {version}).")
  else:
    version = project_client.agents.create_version(
        name,
        definition=wanted,
        description=REGISTRY[key]["description"],
        metadata={"role": key, "definition_sha256": fingerprint(wanted)},
    ).version
    print(f"Created '{name}' version {version} ({wanted['model']}, no tools).")
  write_lock(name, version, wanted)
  save_env(REGISTRY[key]["env_key"], name)


def status(project_client, names):
  """One line per agent: latest version, model, tools and the locked version."""
  lock = foundry_client.read_lock()
  protected = [os.getenv(k) for k in PROTECTED_KEYS]
  for name in dict.fromkeys(filter(None, protected + names)):
    current = latest(project_client, name)
    if current is None:
      print(f"{name}: not created yet")
      continue
    definition = current.definition.as_dict()
    tools = [t.get("type") for t in definition.get("tools") or []] or "none"
    locked = lock.get(name, {}).get("version", "-")
    print(
        f"{name}: version {current.version}, model {definition.get('model')},"
        f" tools {tools}, locked {locked}"
    )


def smoke(openai_client, key, name):
  """One call with the sample input, then a check that pinning is enforced."""
  with open(os.path.join(AGENTS_DIR, f"{key}.sample.json"), encoding="utf-8") as f:
    sample = json.load(f)
  reference = foundry_client.agent_reference(name)
  reply = foundry_client.ask(openai_client, reference, sample)
  fields = REGISTRY[key]["parse"](reply)
  if fields is None:
    print(f"'{name}' did not return a valid reply:\n{reply}")
    return False
  print(f"'{name}' version {reference.get('version', '(latest)')} replied:")
  for field, value in fields.items():
    print(f"  {field} ({len(value.split())} words): {value}")
  if "check" in REGISTRY[key]:
    problem = REGISTRY[key]["check"](sample, fields)
    print(f"Checks: {'failed - ' + problem if problem else 'passed'}")
  pinned = foundry_client.agent_reference(name, UNKNOWN_VERSION)
  try:
    foundry_client.ask(openai_client, pinned, sample)
    print(f"Warning: version {UNKNOWN_VERSION} was accepted, so pinning is ignored.")
  except APIStatusError as exc:
    print(f"Pinning works: version {UNKNOWN_VERSION} was rejected ({exc.status_code}).")
  return True


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  mode = parser.add_mutually_exclusive_group()
  for flag, text in (
      ("--dry-run", "print the definitions; no Azure calls"),
      ("--status", "list the agents' versions (read-only)"),
      ("--smoke", "one test call per agent, plus a pin check"),
  ):
    mode.add_argument(flag, action="store_true", help=text)
  parser.add_argument(
      "--force", action="store_true", help="add a new version even if unchanged"
  )
  args = parser.parse_args()
  sys.stdout.reconfigure(errors="replace")

  load_dotenv(paths.ENV_PATH)
  names = {key: agent_name(key) for key in REGISTRY}
  if args.dry_run:
    for key, name in names.items():
      print(f"Agent name: {name}")
      print(json.dumps(build_definition(key).as_dict(), indent=2))
    return

  try:
    project_client, openai_client = foundry_client.connect()
    if args.status:
      status(project_client, list(names.values()))
    elif args.smoke:
      # A list, not a generator, so every agent is smoked even after a failure
      if not all([smoke(openai_client, key, name) for key, name in names.items()]):
        sys.exit(1)
    else:
      for key, name in names.items():
        create_or_update(project_client, key, name, args.force)
  except (HttpResponseError, APIError) as exc:
    sys.exit(
        f"Foundry call failed: {exc}. Check az login and that you can create"
        " agents in this project."
    )


if __name__ == "__main__":
  main()
