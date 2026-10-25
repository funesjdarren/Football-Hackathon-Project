"""Optional checks for the agent registry. Safe to delete.

Run from the repo root (no network: Foundry is replaced by a fake):
  python -B -m pytest -p no:cacheprovider tests/test_setup_agents.py -q
"""
import json
import os
import random
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("azure.ai.projects")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from azure.core.exceptions import ResourceNotFoundError  # noqa: E402
import commentary_io  # noqa: E402
import foundry_client  # noqa: E402
import generate_events as g  # noqa: E402
import paths  # noqa: E402
import setup_agents as sa  # noqa: E402

DEFAULT = "MS-Football-Hackathon-Play-by-Play"


class FakeAgents:
  """Just enough of project_client.agents for create_or_update."""

  def __init__(self):
    self.latest, self.created = None, []

  def get(self, agent_name):
    if self.latest is None:
      raise ResourceNotFoundError("not found")
    return SimpleNamespace(versions=SimpleNamespace(latest=self.latest))

  def create_version(self, name, definition, description, metadata):
    self.created.append((name, description, metadata))
    self.latest = SimpleNamespace(version=str(len(self.created)), definition=definition)
    return self.latest


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
  """A temp .env and lock file, and none of the real agent names in the environment."""
  monkeypatch.setattr(paths, "ENV_PATH", str(tmp_path / ".env"))
  monkeypatch.setattr(foundry_client, "LOCK_PATH", str(tmp_path / "agents.lock.json"))
  for key in ("PLAY_BY_PLAY_AGENT_NAME", *sa.PROTECTED_KEYS):
    monkeypatch.delenv(key, raising=False)
  return tmp_path


def test_definition_is_strict_and_tool_free():
  definition = sa.build_definition("play_by_play").as_dict()
  with open(os.path.join(ROOT, "agents", "play_by_play.md"), encoding="utf-8") as f:
    assert definition["instructions"] == f.read()
  assert (definition["kind"], definition["model"]) == ("prompt", "gpt-6-luna")
  assert definition["reasoning"] == {"effort": "low"}
  assert "tools" not in definition and "tool_choice" not in definition
  assert definition["text"]["format"] == {
      "type": "json_schema", "name": "play_by_play", "strict": True,
      "schema": commentary_io.SCHEMA,
  }


def test_dry_run_makes_no_azure_calls(sandbox, monkeypatch, capsys):
  def no_azure():
    raise AssertionError("--dry-run must not connect")
  monkeypatch.setattr(foundry_client, "connect", no_azure)
  monkeypatch.setattr(sys, "argv", ["setup_agents.py", "--dry-run"])
  sa.main()
  blocks = capsys.readouterr().out.split("Agent name: ")[1:]
  names = [block.split("\n", 1)[0] for block in blocks]
  assert names == [DEFAULT, "MS-Football-Hackathon-Fan-Voice"]
  for block in blocks:
    definition = json.loads(block.split("\n", 1)[1])
    assert definition["text"]["format"]["strict"] is True and "tools" not in definition


def test_agent_name_guard(sandbox, monkeypatch):
  assert sa.agent_name("play_by_play") == DEFAULT
  for protected in sa.PROTECTED_KEYS:
    monkeypatch.setenv(protected, DEFAULT)
    with pytest.raises(ValueError, match=protected):
      sa.agent_name("play_by_play")
    monkeypatch.delenv(protected)
  monkeypatch.setenv("PLAY_BY_PLAY_AGENT_NAME", "-not-valid")
  with pytest.raises(ValueError, match="not a valid"):
    sa.agent_name("play_by_play")


def test_create_then_up_to_date_then_changed(sandbox, monkeypatch, capsys):
  project = SimpleNamespace(agents=FakeAgents())
  sa.create_or_update(project, "play_by_play", DEFAULT, force=False)
  (name, description, metadata), = project.agents.created
  sha = sa.fingerprint(sa.build_definition("play_by_play"))
  assert name == DEFAULT and description == sa.REGISTRY["play_by_play"]["description"]
  assert metadata == {"role": "play_by_play", "definition_sha256": sha}
  assert foundry_client.read_lock()[DEFAULT] == {
      "version": "1", "model": "gpt-6-luna", "definition_sha256": sha
  }
  assert (sandbox / ".env").read_text() == f"PLAY_BY_PLAY_AGENT_NAME={DEFAULT}\n"

  sa.create_or_update(project, "play_by_play", DEFAULT, force=False)
  assert len(project.agents.created) == 1
  assert "already up to date (version 1)" in capsys.readouterr().out

  edited = dict(sa.MODEL, reasoning={"effort": "medium"})
  monkeypatch.setattr(sa, "MODEL", edited)
  sa.create_or_update(project, "play_by_play", DEFAULT, force=False)
  assert "version 2" in capsys.readouterr().out
  assert foundry_client.read_lock()[DEFAULT]["version"] == "2"


def test_tools_or_edits_are_not_up_to_date():
  wanted = sa.build_definition("play_by_play")
  assert sa.is_up_to_date(sa.build_definition("play_by_play"), wanted)
  for edit in ({"tools": [{"type": "web_search"}]}, {"instructions": "old"}):
    live = sa.PromptAgentDefinition({**wanted.as_dict(), **edit})
    assert not sa.is_up_to_date(live, wanted)


def test_agent_reference_pins_the_locked_version(sandbox):
  assert foundry_client.agent_reference(DEFAULT) == {
      "type": "agent_reference", "name": DEFAULT}
  sa.write_lock(DEFAULT, "3", sa.build_definition("play_by_play"))
  assert foundry_client.agent_reference(DEFAULT)["version"] == "3"
  assert foundry_client.agent_reference(DEFAULT, "999999")["version"] == "999999"


def test_sample_is_build_input_of_a_real_goal():
  """The --smoke sample is exactly what build_input makes for seed 1's first goal."""
  packet = next(
      p for p in g.simulate(random.Random(1))
      if p["broadcast_metadata"]["commentary_trigger"] == "GOAL_SCORED"
  )
  sample_path = os.path.join(ROOT, "agents", "play_by_play.sample.json")
  with open(sample_path, encoding="utf-8") as f:
    assert json.load(f) == commentary_io.build_input(packet, "KEY EVENT: GOAL_SCORED")
