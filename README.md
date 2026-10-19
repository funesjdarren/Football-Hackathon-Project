# Football Hackathon Project

Streams a simulated match and sends each goal to our Foundry agent.

## Setup

Create a `.env` file in the repo root:

```
PROJECT_ENDPOINT="your_project_endpoint"
AGENT_NAME="your_agent_name"
```

Then (Python 3.10+):

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
az login
```

## Run

```
python3 -u generate_data.py | python3 read_stream.py
```

Ctrl-C to stop. Afterwards, `git restore football_data/match_data.json`.
