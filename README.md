# Football Hackathon Project

Streams a simulated match to three Foundry agents:
- a play-by-play commentator (`scripts/read_stream.py`) that explains each key moment for casual fans and for analysts;
- a "State of the Game" color commentator (`scripts/state_of_game.py`) that steps back for the bigger picture every 15 minutes, at half-time and full-time, and shortly after goals and red cards;
- Fan Voice (`scripts/fan_voice.py`), which retells both in Korean.

## For the demo

### Watch the recorded match (judges: no Azure account needed)

1. Use Windows 10/11 with Python 3.10 or later (tested on 3.13). The python.org installer includes Tk. The replay needs no packages and no keys, and sends nothing anywhere.
2. Double-click `run_demo.cmd`, or run `python scripts/run_match.py --replay` from the repo root. It replays the match saved in `demo/` with its real timing and a REPLAY badge. This is real agent output from a live run.
3. Things to try:
   - **Viewer dropdown** (top right): switch between "FC Alpha casual fan", "Analyst" and "Beta United casual fan (Korean)". The commentary, stats and panels redraw for that viewer.
   - **Click any player** for their Player Card (passes, pass difficulty, shots, top speed). The tile under the pitch shows the viewer's own metric.
   - **Analyst view:** every stat, the tactical card, and a difficulty chip on each pass.
4. Options:
   - `run_demo.cmd --speed 2` plays twice as fast.
   - `run_demo.cmd --start-at 03:00` jumps to a match clock.
   - `run_demo.cmd --profile analyst_en --profile casual_en` opens two windows side by side.

### Record a new demo match (team: needs Azure)

1. Do the setup below once: `.env`, `az login`, then `python scripts/setup_agents.py` and `python scripts/setup_agents.py --smoke`.
2. Run `python scripts/run_match.py --seed 173`.
   - It plays the match live with all three agents, Korean included.
   - When it reaches the final whistle, it saves itself to `demo/syn-000173/`.
   - Seed 173 has an early goal, goals for both sides and a red card.
3. Check it with `run_demo.cmd`, then commit `demo/syn-000173/`. Delete any other `demo/` folders, because the replay plays the newest.
4. To practise for free, use `--offline` (placeholder agents) or `--no-record` (live, but not saved).

## Setup

Copy `.env.example` to `.env` in the repo root and fill in your Foundry project endpoint. `.env` is git-ignored; never commit it.

Then (Python 3.10+):

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
az login
python scripts/setup_state_of_game_agent.py
python scripts/setup_agents.py

If using powershell run .venv/Scripts/Activate.ps1
```

The first setup script creates the State of the Game agent once (same model and reasoning as `AGENT_NAME`, no web search) and adds `STATE_OF_GAME_AGENT_NAME` to your `.env`. The second creates the play-by-play v2 agent (below). Both are safe to re-run.

## Play-by-play v2 agent (agents as code)

- `scripts/setup_agents.py` builds `MS-Football-Hackathon-Play-by-Play` from code: `gpt-6-luna` with low reasoning, the instructions in `agents/play_by_play.md`, a strict JSON reply (`headline`, `casual`, `analyst`, `why_it_matters`, `eli5_term`, `eli5_definition`) and no tools. It adds a new version only when the definition changed, records it in `agents.lock.json` (commit that file) and saves `PLAY_BY_PLAY_AGENT_NAME` to `.env`.
- `--dry-run` prints the definition without calling Azure, `--status` lists every agent's version (read-only), and `--smoke` sends `agents/play_by_play.sample.json` once, then checks that a call pinned to an unknown version is rejected.
- `read_stream.py` calls the v2 agent through `scripts/commentary_io.py`, pinned to the version in `agents.lock.json`, even with no `PLAY_BY_PLAY_AGENT_NAME` in `.env`. An empty `PLAY_BY_PLAY_AGENT_NAME=` line in `.env` switches it back to the v1 agent (`AGENT_NAME`), which is never changed.

## Viewer profiles, Player Cards and Fan Voice

- `profiles/*.json` set who is watching: "FC Alpha casual fan" (`casual_en`, the default when no `--profile` is given), "Analyst" (`analyst_en`) and "Beta United casual fan (Korean)" (`casual_ko`). Each has a mode (casual or analyst), a language, a favourite club and player, and one metric for the tile under the pitch.
- The Viewer dropdown at the top right of the window switches profile live and redraws everything said so far in the new mode and language. Record demos with `--profile casual_ko` so the Korean viewer works in the replay too.
- `python scripts/run_match.py --profile casual_en --profile analyst_en` opens one window per profile on the same match, for side-by-side shots. Casual windows show three plain stats and the casual commentary; analyst windows show every stat, the tactical card, the analyst commentary and a difficulty chip on each pass.
- Click a player for their Player Card (`scripts/match_intel.py`: passes, pass difficulty, shots, top speed, cards). Pass difficulty adds up length, pressure on the passer, defenders in the lane and forward progress; its weights are in `match_intel.pass_quality`.
- Fan Voice (`scripts/fan_voice.py`, the `MS-Football-Hackathon-Fan-Voice` agent from `setup_agents.py`) retells key moments and every State of the Game take in Korean for a Korean profile. Each retelling is checked against the English source (same numbers, names unchanged, really Korean) and replaced by the English row, marked (EN), if a check fails. A normal live run (`python scripts/run_match.py`) translates from kick-off, so the saved replay has Korean all the way through. In a practice run (`--no-record`) or an offline run, it makes no agent calls until a window shows Korean. It then catches up on the match so far and keeps going, and older shots get English (EN) lines instead of calls. Fan Voice is text only, there is no audio. Have a fluent Korean speaker review its output before the demo.

## Demo recording and replay (no Azure needed)

- Every live run that reaches the final whistle is saved automatically to `demo/<match_id>/`, with the feeds and the agent versions used. Offline runs and matches stopped early are not saved, and `--no-record` skips it. Commit the folder of the match you want judges to see, and delete any others. `python scripts/record_demo.py` saves the last run by hand.
- `run_demo.cmd`, or `python scripts/run_match.py --replay [DIR]`, plays the newest recording back with its real timing and a REPLAY badge. It needs only Python and Tk, and writes nothing into the repo. Add `--profile casual_ko`, `--speed 2` or `--start-at 60:00` as needed.

## Run

One command runs the whole match from the repo root: the generator, both agents and the match window.

```
python scripts/run_match.py              # live (needs .env and az login)
python scripts/run_match.py --offline    # no Azure: placeholder State of the Game, no play-by-play
```

- Options: `--seed N` replays an exact match, `--speed P` sets packets per second (0 = no delay), `--no-gui` runs without the window, `--keep-history` leaves the last run's feed files in place.
- The agents connect before kick-off, so a missing setting or Azure sign-in shows up straight away. Close the window or press Ctrl+C to stop everything.
- Each run first moves the previous feed files to `football_data/archive/<time>/` (nothing is deleted) and logs every part to `football_data/logs/<time>/`, with a `run.json` summary.

### Manual run (debugging)

Each command in its own terminal, from the repo root. Start `state_of_game.py` first and wait for its `Watching ...` line.
```
Terminal 1:  python scripts/match_gui.py
Terminal 2:  python scripts/state_of_game.py            (add --dry-run for no Azure)
Terminal 3:  python scripts/generate_events.py | python scripts/read_stream.py
```

## State of the Game commentator

- Speaks at kick-off, 15', 30', half-time, 60', 75' and full-time, plus about a match minute after a goal or red card when there is room. Apart from the kick-off line, updates are always at least 6 match minutes apart: about 10 per match.
- Reads `football_data/match_data.jsonl`, so it never touches the play-by-play pipe. Each take is appended to `football_data/state_of_game.jsonl`, and the GUI's State of the Game panel shows the newest one.
- `python scripts/state_of_game.py --replay football_data/match_data.jsonl` prints the update schedule for a saved match without calling Azure. Add `--show-context` to see each packet sent to the agent.
- `python scripts/setup_state_of_game_agent.py --force` adds a new agent version (for example after editing its instructions). `--dry-run` only prints the definition.
