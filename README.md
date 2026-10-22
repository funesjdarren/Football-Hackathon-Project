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

If using powershell run .venv/Scripts/Activate.ps1
```

## Run
Match gui waits for data contained in the .jsonl file so it can be run first.
```
### For the live production run  
python3 scripts/match_gui.py
python3 -u scripts/generate_data.py | scripts/python3 read_stream.py  

### For the test run  
python3 scripts/match_gui.py
python3 scripts/generate_data.py
```

Ctrl-C to stop. Afterwards, `git restore football_data/match_data.json`.
