"""Shared Foundry connection for the agent scripts (no network calls on import).

connect() signs in with az login and reads PROJECT_ENDPOINT from .env.
agent_reference() pins an agent to its version in agents.lock.json.
"""
import json
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

import paths

LOCK_PATH = os.path.join(paths.REPO_ROOT, "agents.lock.json")


def connect(timeout=30.0, max_retries=1):
  """(project_client, openai_client) for the Foundry project in .env."""
  load_dotenv(paths.ENV_PATH)
  endpoint = os.getenv("PROJECT_ENDPOINT")
  if not endpoint:
    raise ValueError("PROJECT_ENDPOINT must be set in .env file")
  credential = DefaultAzureCredential(
      exclude_environment_credential=True,
      exclude_managed_identity_credential=True,
  )
  project_client = AIProjectClient(credential=credential, endpoint=endpoint)
  return project_client, project_client.get_openai_client(
      timeout=timeout, max_retries=max_retries
  )


def read_lock():
  """{agent name: {"version", ...}} from agents.lock.json; {} before the first setup."""
  if not os.path.exists(LOCK_PATH):
    return {}
  with open(LOCK_PATH, encoding="utf-8") as f:
    return json.load(f)


def agent_reference(name, version=None):
  """The agent_reference for a call: the given or locked version, else the latest."""
  reference = {"type": "agent_reference", "name": name}
  version = version or read_lock().get(name, {}).get("version")
  if version:
    reference["version"] = version
  return reference


def ask(openai_client, reference, payload):
  """Sends one JSON payload to the agent and returns its reply text."""
  response = openai_client.responses.create(
      input=json.dumps(payload), extra_body={"agent_reference": reference}
  )
  return response.output_text
