import json
import math
import os
import sys
import tkinter as tk
from tkinter import ttk
import paths

# Window configuration
WINDOW_WIDTH = 1260
WINDOW_HEIGHT = 740
PITCH_PIXEL_W = 800
PITCH_PIXEL_H = 540
PITCH_X_OFFSET = 20
PITCH_Y_OFFSET = 80

# Field Metric System: 120m length x 80m width
PITCH_METRIC_L = 120.0
PITCH_METRIC_W = 80.0

TEAM_A_COLOR = "#38bdf8"  # Light Blue
TEAM_B_COLOR = "#f87171"  # Light Red
GK_COLOR = "#facc15"      # Amber/Yellow
BALL_COLOR = "#ffffff"


class FootballMatchGUI:

  def __init__(self, root):
    self.root = root
    self.root.title("Live Tactical Commentary & Visualizer")
    self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
    self.root.configure(bg="#0f172a")

    self.match_file_path = os.path.join(paths.DATA_DIR, "match_data.jsonl")
    self.commentary_file_path = os.path.join(paths.DATA_DIR, "commentary.jsonl")
    self.state_file_path = os.path.join(paths.DATA_DIR, "state_of_game.jsonl")

    self.file_pos = 0
    self.comm_file_pos = 0
    self.state_file_pos = 0
    self.current_payload = None

    self._build_header()
    self._build_pitch_canvas()
    self._build_sidebar()

    # Start file polling loop (every 100ms)
    self.root.after(100, self.poll_data_feed)

  # --- UI LAYOUT CREATION ---

  def _build_header(self):
    """Broadcast Top Bar: Scoreboard, Match Clock, and Tactical Phase."""
    self.header_frame = tk.Frame(self.root, bg="#1e293b", height=60)
    self.header_frame.pack(fill=tk.X, side=tk.TOP)

    self.clock_label = tk.Label(
        self.header_frame,
        text="00:00",
        font=("Segoe UI", 16, "bold"),
        fg="#38bdf8",
        bg="#1e293b",
    )
    self.clock_label.pack(side=tk.LEFT, padx=25, pady=12)

    self.score_label = tk.Label(
        self.header_frame,
        text="FC Alpha 0 - 0 Beta United",
        font=("Segoe UI", 16, "bold"),
        fg="#f8fafc",
        bg="#1e293b",
    )
    self.score_label.pack(side=tk.LEFT, padx=30, pady=12)

    self.phase_tag = tk.Label(
        self.header_frame,
        text="Phase: Midfield Progression",
        font=("Segoe UI", 11, "bold"),
        fg="#a855f7",
        bg="#0f172a",
        padx=12,
        pady=4,
    )
    self.phase_tag.pack(side=tk.RIGHT, padx=25, pady=12)

  def _build_pitch_canvas(self):
    """Draws the 2D soccer field on an HTML5-style Dark Grass Canvas."""
    self.pitch_frame = tk.Frame(self.root, bg="#0f172a")
    self.pitch_frame.pack(side=tk.LEFT, padx=20, pady=15)

    self.canvas = tk.Canvas(
      self.pitch_frame,
      width=PITCH_PIXEL_W,
      height=PITCH_PIXEL_H,
      bg="#1e3a2f",
      highlightthickness=2,
      highlightbackground="#334155",
    )
    self.canvas.pack()

    self.draw_pitch_markings()

  def _build_sidebar(self):
    """Sidebar containing Live Stats, Tactical Foil Card, State of the Game, and AI Commentary."""
    self.sidebar = tk.Frame(self.root, bg="#1e293b", width=400)
    self.sidebar.pack(
        side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 20), pady=15
    )

    # 1. Tactical Duel Split Card (Intent vs Foil)
    tactic_box = tk.LabelFrame(
        self.sidebar,
        text=" Tactical Duel (Tactic vs. Foil) ",
        font=("Segoe UI", 11, "bold"),
        fg="#38bdf8",
        bg="#1e293b",
        padx=10,
        pady=8,
    )
    tactic_box.pack(fill=tk.X, padx=12, pady=10)

    self.attack_intent_lbl = tk.Label(
        tactic_box,
        text="Attack Plan: Waiting for match event...",
        font=("Segoe UI", 10),
        fg="#93c5fd",
        bg="#1e293b",
        wraplength=340,
        justify=tk.LEFT,
    )
    self.attack_intent_lbl.pack(anchor="w", pady=2)

    self.defense_foil_lbl = tk.Label(
        tactic_box,
        text="Defense Foil: Waiting for match event...",
        font=("Segoe UI", 10),
        fg="#fca5a5",
        bg="#1e293b",
        wraplength=340,
        justify=tk.LEFT,
    )
    self.defense_foil_lbl.pack(anchor="w", pady=2)

    # 2. Cumulative Match Stats
    stats_box = tk.LabelFrame(
        self.sidebar,
        text=" Live Match Analytics ",
        font=("Segoe UI", 11, "bold"),
        fg="#38bdf8",
        bg="#1e293b",
        padx=10,
        pady=8,
    )
    stats_box.pack(fill=tk.X, padx=12, pady=5)

    self.poss_lbl = tk.Label(
        stats_box,
        text="Possession: 50% - 50%",
        font=("Segoe UI", 10),
        fg="#f8fafc",
        bg="#1e293b",
    )
    self.poss_lbl.pack(anchor="w")

    self.xg_lbl = tk.Label(
        stats_box,
        text="Expected Goals (xG): 0.00 - 0.00",
        font=("Segoe UI", 10),
        fg="#f8fafc",
        bg="#1e293b",
    )
    self.xg_lbl.pack(anchor="w")

    self.crowd_lbl = tk.Label(
        stats_box,
        text="Crowd Intensity Index: 40%",
        font=("Segoe UI", 10),
        fg="#f8fafc",
        bg="#1e293b",
    )
    self.crowd_lbl.pack(anchor="w")

    # 3. State of the Game (color commentator, latest take only)
    self.state_box = tk.LabelFrame(
        self.sidebar,
        text=" State of the Game ",
        font=("Segoe UI", 11, "bold"),
        fg="#38bdf8",
        bg="#1e293b",
        padx=10,
        pady=8,
    )
    self.state_box.pack(fill=tk.X, padx=12, pady=5)

    state_body = tk.Frame(self.state_box, bg="#1e293b")
    state_body.pack(fill=tk.X)
    self.state_text = tk.Text(
        state_body,
        width=1,
        height=8,
        bg="#0f172a",
        fg="#fde68a",
        font=("Segoe UI", 9),
        wrap=tk.WORD,
        relief=tk.FLAT,
    )
    state_scroll = ttk.Scrollbar(
        state_body, orient=tk.VERTICAL, command=self.state_text.yview
    )
    self.state_text.configure(yscrollcommand=state_scroll.set)
    state_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    self.state_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
    self.state_text.insert(
        tk.END, "Waiting for the first State of the Game update..."
    )

    self.state_next_lbl = tk.Label(
        self.state_box,
        text=(
            "Updates every 15 minutes, at half-time and full-time, and after"
            " goals or red cards."
        ),
        font=("Segoe UI", 8),
        fg="#94a3b8",
        bg="#1e293b",
        wraplength=340,
        justify=tk.LEFT,
    )
    self.state_next_lbl.pack(anchor="w", pady=(4, 0))

    # 4. Newcomer AI Commentary Feed
    comm_box = tk.LabelFrame(
        self.sidebar,
        text=" Live AI Commentary (Casual Fan Companion) ",
        font=("Segoe UI", 11, "bold"),
        fg="#38bdf8",
        bg="#1e293b",
        padx=10,
        pady=8,
    )
    comm_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 12))

    self.comm_text = tk.Text(
        comm_box,
        bg="#0f172a",
        fg="#f1f5f9",
        font=("Segoe UI", 9),
        wrap=tk.WORD,
        relief=tk.FLAT,
    )
    self.comm_text.pack(fill=tk.BOTH, expand=True)

  # --- PITCH RENDERING LOGIC ---

  def to_screen_coords(self, x_metric, y_metric):
    """Converts 120x80 football meters to canvas pixel coordinates."""
    px = (x_metric / PITCH_METRIC_L) * PITCH_PIXEL_W
    py = (y_metric / PITCH_METRIC_W) * PITCH_PIXEL_H
    return px, py

  def draw_pitch_markings(self):
    """Draws regulation lines, penalty boxes, center circles."""
    c = self.canvas
    w, h = PITCH_PIXEL_W, PITCH_PIXEL_H
    line_col = "#4ade80"

    # Outer border line
    c.create_rectangle(8, 8, w - 8, h - 8, outline=line_col, width=2)
    # Halfway line
    c.create_line(w / 2, 8, w / 2, h - 8, fill=line_col, width=2)
    # Center circle (radius 9.15m)
    r_px = (9.15 / PITCH_METRIC_L) * w
    c.create_oval(
        w / 2 - r_px,
        h / 2 - r_px,
        w / 2 + r_px,
        h / 2 + r_px,
        outline=line_col,
        width=2,
    )
    c.create_oval(
        w / 2 - 3, h / 2 - 3, w / 2 + 3, h / 2 + 3, fill=line_col, width=0
    )

    # Left Penalty Box (16.5m deep, 40.3m wide)
    box_w_px = (16.5 / PITCH_METRIC_L) * w
    box_top_py = ((40.0 - 20.15) / PITCH_METRIC_W) * h
    box_bot_py = ((40.0 + 20.15) / PITCH_METRIC_W) * h
    c.create_rectangle(
        8, box_top_py, 8 + box_w_px, box_bot_py, outline=line_col, width=2
    )

    # Right Penalty Box
    c.create_rectangle(
        w - 8 - box_w_px,
        box_top_py,
        w - 8,
        box_bot_py,
        outline=line_col,
        width=2,
    )

  def render_frame(self, packet):
    """Renders active players, moving ball, and action trails on the pitch."""
    self.canvas.delete("dynamic_element")

    # 1. Update Match Header
    clock_str = packet["broadcast_metadata"]["clock"]["display_time"]
    home = packet["broadcast_metadata"]["scoreboard"]["home"]
    away = packet["broadcast_metadata"]["scoreboard"]["away"]
    self.clock_label.config(text=f"⏱ {clock_str}")
    self.score_label.config(
        text=f"{home['team']}  {home['score']} - {away['score']}  {away['team']}"
    )

    tactics = packet["tactical_context"]
    self.phase_tag.config(text=f"Phase: {tactics['phase_of_play']}")

    # 2. Update Stats
    stats = packet["live_match_stats"]
    poss = stats["possession_pct"]
    box = stats["box_score"]
    self.poss_lbl.config(
        text=f"Possession: {home['team']} {poss[home['team']]}% | {away['team']} {poss[away['team']]}%"
    )
    self.xg_lbl.config(
        text=f"xG: {home['team']} {box[home['team']]['xg']} | {box[away['team']]['xg']} {away['team']}"
    )
    self.crowd_lbl.config(
        text=f"Crowd Intensity: {packet['broadcast_metadata']['crowd_intensity_index']}%"
    )

    # 3. Update Tactical Card
    self.attack_intent_lbl.config(
        text=f"🔵 Attack Plan ({tactics['possession_team']}): {packet['event']['type']} by {packet['event']['actor']['name']}"
    )
    self.defense_foil_lbl.config(
        text=f"🔴 Defense Foil ({tactics['defending_team']}): {tactics['defensive_structure']} (Line: {tactics['defensive_line_height_meters']}m)"
    )

    # 4. Render 22 Players from tracking data
    tracking = packet.get("player_tracking", {})
    for p in tracking.get("players", []):
      px, py = self.to_screen_coords(p["x"], p["y"])
      r = 7

      color = (
          GK_COLOR
          if p["role"] == "GK"
          else (
              TEAM_A_COLOR
              if p["team"] == home["team"]
              else TEAM_B_COLOR
          )
      )

      # Player circle
      self.canvas.create_oval(
          px - r,
          py - r,
          px + r,
          py + r,
          fill=color,
          outline="#ffffff",
          width=1,
          tags="dynamic_element",
      )
      # Role / Name tag
      self.canvas.create_text(
          px,
          py - 11,
          text=p["role"],
          fill="#f8fafc",
          font=("Segoe UI", 7, "bold"),
          tags="dynamic_element",
      )

    # 5. Render Ball and Pass/Shot Vector
    details = packet["event"]["details"]
    coords = details.get("coordinates", {})
    if "start" in coords:
      bx, by = self.to_screen_coords(coords["start"][0], coords["start"][1])
      # Draw Action vector arrow if ball moved
      if "end" in coords:
        ex, ey = self.to_screen_coords(coords["end"][0], coords["end"][1])
        arrow_color = (
            "#fbbf24"
            if details.get("outcome") in ["Complete", "Goal"]
            else "#ef4444"
        )
        self.canvas.create_line(
            bx,
            by,
            ex,
            ey,
            fill=arrow_color,
            width=2,
            arrow=tk.LAST,
            tags="dynamic_element",
        )

      # Draw Ball with glow
      self.canvas.create_oval(
          bx - 5,
          by - 5,
          bx + 5,
          by + 5,
          fill="#ffffff",
          outline="#000000",
          width=1,
          tags="dynamic_element",
      )

  # --- STATE OF THE GAME PANEL ---

  def read_complete_lines(self, path, pos):
    """Returns complete new lines after byte offset pos, and the new offset.

    A half-written last line is left for the next poll, so a take is never lost.
    """
    try:
      size = os.path.getsize(path)
    except OSError:
      return [], pos
    if size < pos:
      pos = 0
    if size == pos:
      return [], pos
    with open(path, "rb") as f:
      f.seek(pos)
      data = f.read(size - pos)
    cut = data.rfind(b"\n")
    if cut < 0:
      return [], pos
    text = data[: cut + 1].decode("utf-8", "replace")
    return [line.strip() for line in text.splitlines() if line.strip()], pos + cut + 1

  def show_state_take(self, entry):
    """Replaces the panel with the newest State of the Game take."""
    label = entry.get("label")
    self.state_box.config(
        text=f" State of the Game  ⏱ {entry.get('clock', '')}"
        + (f" · {label} " if label else " ")
    )
    self.state_text.delete("1.0", tk.END)
    self.state_text.insert(tk.END, entry["text"])
    self.state_text.see("1.0")
    self.state_next_lbl.config(text=entry.get("next") or "")

  def poll_state_feed(self):
    """Tails state_of_game.jsonl and shows only its newest valid entry."""
    lines, self.state_file_pos = self.read_complete_lines(
        self.state_file_path, self.state_file_pos
    )
    for line in reversed(lines):
      try:
        entry = json.loads(line)
      except json.JSONDecodeError:
        continue
      if isinstance(entry, dict) and isinstance(entry.get("text"), str):
        self.show_state_take(entry)
        break

  # --- REAL-TIME POLLING ---

  def poll_data_feed(self):
    """Tails match_data.jsonl and commentary.jsonl."""
    # Poll match events
    if os.path.exists(self.match_file_path):
      with open(self.match_file_path, "r") as f:
        f.seek(self.file_pos)
        lines = f.readlines()
        self.file_pos = f.tell()

        for line in lines:
          line = line.strip()
          if line:
            try:
              packet = json.loads(line)
              self.render_frame(packet)
            except json.JSONDecodeError:
              pass

    # Poll AI Commentary
    if os.path.exists(self.commentary_file_path):
      with open(self.commentary_file_path, "r") as f:
        f.seek(self.comm_file_pos)
        lines = f.readlines()
        self.comm_file_pos = f.tell()

        for line in lines:
          line = line.strip()
          if line:
            try:
              comm_entry = json.loads(line)
              self.comm_text.insert(
                  tk.END,
                  f"\n[{comm_entry['clock']}] {comm_entry['text']}\n"
                  + ("-" * 42)
                  + "\n",
              )
              self.comm_text.see(tk.END)
            except json.JSONDecodeError:
              pass

    # Poll State of the Game (a bad line must never stop this loop)
    try:
      self.poll_state_feed()
    except Exception as exc:
      print(f"State of the Game panel error: {exc!r}", file=sys.stderr)

    self.root.after(200, self.poll_data_feed)


if __name__ == "__main__":
  root = tk.Tk()
  app = FootballMatchGUI(root)
  root.mainloop()