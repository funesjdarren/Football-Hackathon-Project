# Football Hackathon Project

Streams a simulated match to two Foundry agents: a play-by-play "Casual Fan Companion" (`scripts/read_stream.py`) and a "State of the Game" color commentator (`scripts/state_of_game.py`) that steps back for the bigger picture every 15 minutes, at half-time and full-time, and shortly after goals and red cards.

## Setup

Copy `.env.example` to `.env` in the repo root and fill in your Foundry project endpoint. `.env` is git-ignored; never commit it.

Then (Python 3.10+):

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
az login
python scripts/setup_state_of_game_agent.py

If using powershell run .venv/Scripts/Activate.ps1
```

The setup script creates the State of the Game agent once (same model and reasoning as `AGENT_NAME`, no web search) and adds `STATE_OF_GAME_AGENT_NAME` to your `.env`. It is safe to re-run.

## Run
Each command below runs in **its own terminal** (they keep running), with the `.venv` activated. Run them from the repo root: `read_stream.py` still writes `football_data/` relative to where it starts; the other scripts find the repo's `football_data/` from any folder.

Match gui waits for data contained in the .jsonl files so it can be run first. Start `state_of_game.py` before the match and wait for its `Watching ...` line, so it catches kick-off.
```
### For the live production run
Terminal 1:  python scripts/match_gui.py
Terminal 2:  python scripts/state_of_game.py
Terminal 3:  python -u scripts/generate_events.py | python scripts/read_stream.py

### For the test run (no Azure calls)
Terminal 1:  python scripts/match_gui.py
Terminal 2:  python scripts/state_of_game.py --dry-run
Terminal 3:  python scripts/generate_events.py
```

Ctrl-C in terminals 2 and 3 and close the GUI window to stop. Match data in `football_data/` is git-ignored, so there is nothing to clean up.

## State of the Game commentator

- Speaks at kick-off, 15', 30', half-time, 60', 75' and full-time, plus about a match minute after a goal or red card when there is room. Apart from the kick-off line, updates are always at least 6 match minutes apart: about 10 per match.
- Reads `football_data/match_data.jsonl`, so it never touches the play-by-play pipe. Each take is appended to `football_data/state_of_game.jsonl`, and the GUI's State of the Game panel shows the newest one.
- `python scripts/state_of_game.py --replay football_data/match_data.jsonl` prints the update schedule for a saved match without calling Azure. Add `--show-context` to see each packet sent to the agent.
- `python scripts/setup_state_of_game_agent.py --force` adds a new agent version (for example after editing its instructions). `--dry-run` only prints the definition.
