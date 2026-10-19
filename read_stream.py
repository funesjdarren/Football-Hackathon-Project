import json
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

# Connect to the project and the existing agent
credential = DefaultAzureCredential(
    exclude_environment_credential=True,
    exclude_managed_identity_credential=True
)
project_client = AIProjectClient(credential=credential, endpoint=project_endpoint)
openai_client = project_client.get_openai_client()
agent = project_client.agents.get(agent_name=agent_name)


def read_events(lines):
    block = None
    for line in lines:
        s = line.rstrip()
        if s == "{":
            block = [line]
        elif block is not None:
            block.append(line)
            if s == "}":
                yield json.loads("".join(block))
                block = None


def send_to_agent(team, event):
    response = openai_client.responses.create(
        input=json.dumps({"scoring_team": team, "event": event}),
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
    )
    return response.output_text


def main():
    for event in read_events(sys.stdin):
        if event["type"] == "Shot" and event["details"]["outcome"] == "Goal":
            team = event["possession_team"]  # the shooting team
            print(f"GOAL: {team}", flush=True)
            try:
                print(send_to_agent(team, event), flush=True)
            except Exception as exc:  # a crash here would also kill the generator
                print(f"agent call failed: {exc!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
