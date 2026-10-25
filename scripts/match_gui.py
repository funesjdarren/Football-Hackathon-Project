import argparse
import json
import math
import os
import sys
import tkinter as tk
from tkinter import ttk
import match_intel
import paths
import personalize

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
FOCUS_COLOR = "#fde047"
FONTS = {"ko": "Malgun Gothic"}  # Segoe UI has no Hangul
BALL_CARRIER_ICON = "⚽"
BALL_CARRIER_ICON_SIZE = 18


class FootballMatchGUI:

  def __init__(self, root, profile=None, data_dir=None, replay=False):
    self.root = root
    self.profile = profile or personalize.load_profile()
    self.replay = replay
    self.font = "Segoe UI"  # apply_profile sets the profile's font
    self.styled = []  # (widget, size, weight...) whose font follows the profile
    self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
    self.root.configure(bg="#0f172a")

    self.data_dir = data_dir or paths.DATA_DIR
    self.match_file_path = os.path.join(self.data_dir, "match_data.jsonl")

    self.file_pos = 0
    self.intel = match_intel.MatchIntel()
    self.focus_id = None
    self.players = []  # the last frame, for clicks
    self.last_packet = None

    self._build_header()
    self._build_pitch_canvas()
    self._build_sidebar()
    self.apply_profile()

    # Start file polling loop (every 100ms)
    self.root.after(100, self.poll_data_feed)

  def tr(self, text):
    return personalize.ui(self.profile, text)

  def style(self, widget, size, *weight):
    self.styled.append((widget, size, *weight))
    return widget

  # --- VIEWER PROFILE ---

  def apply_profile(self):
    """Shows the match as the current profile's viewer: mode, language and feeds.

    The commentary and State of the Game feeds are read again from the top, so a
    switch redraws everything said so far in the new mode and language.
    """
    p = self.profile
    self.font = FONTS.get(p["language"], "Segoe UI")
    for widget, size, *weight in self.styled:
      widget.config(font=(self.font, size, *weight))
    title = f"Live Tactical Commentary - {p['display_name']}"
    self.root.title(title + (" (REPLAY)" if self.replay else ""))
    self.profile_var.set(p["display_name"])
    self.badge_lbl.config(text=f"{self.tr(p['mode'])} | {p['language'].upper()}")
    if p["language"] != "en" and not self.replay:  # wakes Fan Voice for this match
      open(os.path.join(self.data_dir, personalize.WANTED_FLAG), "a").close()
    self.stats_box.config(text=f" {self.tr('Live Match Analytics')} ")
    self.state_title = self.tr("State of the Game")
    self.state_box.config(text=f" {self.state_title} ")
    self.comm_box.config(
        text=f" {self.tr('Live AI Commentary')} ({self.tr(p['mode'])}) ")
    if p["mode"] == "analyst":  # casual viewers get the room for commentary
      self.tactic_box.pack(fill=tk.X, padx=12, pady=10, before=self.stats_box)
    else:
      self.tactic_box.pack_forget()
    self.commentary_file_path, self.state_file_path = personalize.feed_paths(
        p, self.data_dir)
    self.comm_file_pos = self.state_file_pos = 0
    self.comm_text.delete("1.0", tk.END)
    self.state_text.delete("1.0", tk.END)
    self.state_text.insert(
        tk.END, self.tr("Waiting for the first State of the Game update..."))
    self.focus_id = p["favourite_player_id"] or self.focus_id
    if self.last_packet:
      self.show_stats(self.last_packet)
    self.show_card()

  def switch_profile(self, _choice=None):
    """The header dropdown: watch as another viewer from profiles/."""
    self.profile = personalize.load_profile(self.choices[self.profile_var.get()])
    self.apply_profile()
    if self.profile["language"] != "en" and not os.path.exists(self.state_file_path):
      note = ("This recording has no feed in this language." if self.replay else
              "Starting commentary in this language: catching up on the match"
              " so far...")
      self.comm_text.insert(tk.END, self.tr(note) + "\n")

  # --- UI LAYOUT CREATION ---

  def _build_header(self):
    """Broadcast Top Bar: Scoreboard, Match Clock, Tactical Phase and profile."""
    self.header_frame = tk.Frame(self.root, bg="#1e293b", height=60)
    self.header_frame.pack(fill=tk.X, side=tk.TOP)
    # Viewer dropdown, styled like the header's other chips: display name -> profile
    self.choices = {personalize.load_profile(name)["display_name"]: name
                    for name in personalize.profile_names()}
    self.profile_var = tk.StringVar()
    picker = tk.OptionMenu(self.header_frame, self.profile_var, *self.choices,
                           command=self.switch_profile)
    picker.config(font=("Segoe UI", 10, "bold"), fg="#e2e8f0", bg="#0f172a",
                  activeforeground="#38bdf8", activebackground="#0f172a",
                  relief=tk.FLAT, bd=0, highlightthickness=0, indicatoron=False,
                  padx=10, pady=4, cursor="hand2")
    picker["menu"].config(font=("Segoe UI", 10), fg="#e2e8f0", bg="#1e293b",
                          activeforeground="#0f172a", activebackground="#38bdf8", bd=0)
    picker.pack(side=tk.RIGHT, padx=(0, 12))
    tk.Label(self.header_frame, text="Viewer ▾", font=("Segoe UI", 9),
             fg="#94a3b8", bg="#1e293b").pack(side=tk.RIGHT, padx=(0, 4))
    self.badge_lbl = self.style(tk.Label(self.header_frame, fg="#a5b4fc", bg="#0f172a",
                                         padx=10, pady=4), 10, "bold")
    self.badge_lbl.pack(side=tk.RIGHT, padx=(0, 12))
    if self.replay:
      tk.Label(self.header_frame, text="REPLAY", font=("Segoe UI", 10, "bold"),
               fg="#f87171", bg="#0f172a", padx=10, pady=4).pack(side=tk.RIGHT,
                                                                  padx=(0, 12))

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
    self.canvas.bind("<Button-1>", self.focus_player)

    # Below the pitch: the focused player's card and the profile's own metric
    card_box = tk.Frame(self.pitch_frame, bg="#0f172a")
    card_box.pack(fill=tk.X, pady=(8, 0))
    style = {"bg": "#1e293b", "justify": tk.LEFT, "padx": 8, "pady": 4}
    self.card_lbl = self.style(
        tk.Label(card_box, fg="#f8fafc", anchor="nw", **style), 9)
    self.card_lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    self.metric_lbl = self.style(
        tk.Label(card_box, fg="#fde68a", wraplength=230, **style), 10, "bold")
    self.metric_lbl.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))

    self.draw_pitch_markings()

  def _build_sidebar(self):
    """Sidebar containing Live Stats, Tactical Foil Card, State of the Game, and AI Commentary."""
    self.sidebar = tk.Frame(self.root, bg="#1e293b", width=400)
    self.sidebar.pack(
        side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 20), pady=15
    )

    # 1. Tactical Duel Split Card (Intent vs Foil), shown to analysts only
    self.tactic_box = tactic_box = tk.LabelFrame(
        self.sidebar,
        text=" Tactical Duel (Tactic vs. Foil) ",
        font=("Segoe UI", 11, "bold"),
        fg="#38bdf8",
        bg="#1e293b",
        padx=10,
        pady=8,
    )

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
    self.stats_box = self.style(tk.LabelFrame(
        self.sidebar, fg="#38bdf8", bg="#1e293b", padx=10, pady=8), 11, "bold")
    self.stats_box.pack(fill=tk.X, padx=12, pady=5)

    self.stats_lbl = self.style(
        tk.Label(self.stats_box, fg="#f8fafc", bg="#1e293b", justify=tk.LEFT), 10)
    self.stats_lbl.pack(anchor="w")

    # 3. State of the Game (color commentator, latest take only)
    self.state_box = self.style(tk.LabelFrame(
        self.sidebar, fg="#38bdf8", bg="#1e293b", padx=10, pady=8), 11, "bold")
    self.state_box.pack(fill=tk.X, padx=12, pady=5)

    state_body = tk.Frame(self.state_box, bg="#1e293b")
    state_body.pack(fill=tk.X)
    self.state_text = tk.Text(
        state_body,
        width=1,
        height=8,
        bg="#0f172a",
        fg="#fde68a",
        wrap=tk.WORD,
        relief=tk.FLAT,
    )
    self.style(self.state_text, 9)
    state_scroll = ttk.Scrollbar(
        state_body, orient=tk.VERTICAL, command=self.state_text.yview
    )
    self.state_text.configure(yscrollcommand=state_scroll.set)
    state_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    self.state_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

    self.state_next_lbl = tk.Label(
        self.state_box,
        text=(
            "Updates every 15 minutes, at half-time and full-time, and after"
            " goals or red cards."
        ),
        fg="#94a3b8",
        bg="#1e293b",
        wraplength=340,
        justify=tk.LEFT,
    )
    self.style(self.state_next_lbl, 8)
    self.state_next_lbl.pack(anchor="w", pady=(4, 0))

    # 4. AI Commentary Feed, in the profile's mode and language
    self.comm_box = self.style(tk.LabelFrame(
        self.sidebar, fg="#38bdf8", bg="#1e293b", padx=10, pady=8), 11, "bold")
    self.comm_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 12))

    self.comm_text = self.style(tk.Text(
        self.comm_box, bg="#0f172a", fg="#f1f5f9", wrap=tk.WORD, relief=tk.FLAT), 9)
    self.comm_text.pack(fill=tk.BOTH, expand=True)

  # --- PLAYER FOCUS ---

  def focus_player(self, event):
    """A click on the pitch focuses the nearest player within 15 px."""
    def gap(p):
      return math.dist(self.to_screen_coords(p["x"], p["y"]), (event.x, event.y))
    nearest = min(self.players, key=gap, default=None)
    if nearest and gap(nearest) <= 15:
      self.focus_id = nearest["player_id"]
      self.show_card()

  def show_card(self):
    lines = personalize.card_lines(self.intel.player_card(self.focus_id), self.profile)
    self.card_lbl.config(text=f"{self.tr('Player Card')}: " + "\n".join(lines))

  def show_stats(self, packet):
    self.stats_lbl.config(text="\n".join(personalize.stat_lines(packet, self.profile)))
    self.metric_lbl.config(text=personalize.metric_text(packet, self.profile))

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
    clock = packet["broadcast_metadata"]["clock"]
    clock_str = clock["display_time"]
    home = packet["broadcast_metadata"]["scoreboard"]["home"]
    away = packet["broadcast_metadata"]["scoreboard"]["away"]
    self.clock_label.config(text=f"⏱ {clock_str}")
    self.score_label.config(
        text=f"{home['team']}  {home['score']} - {away['score']}  {away['team']}"
    )

    tactics = packet["tactical_context"]
    self.phase_tag.config(text=f"Phase: {tactics['phase_of_play']}")

    # 2. Update Stats, the Player Card and the profile's metric
    self.intel.observe(packet)
    self.last_packet = packet  # for a profile switch between packets
    self.show_stats(packet)
    self.show_card()

    # 3. Update Tactical Card
    self.attack_intent_lbl.config(
        text=f"🔵 Attack Plan ({tactics['possession_team']}): {packet['event']['type']} by {packet['event']['actor']['name']}"
    )
    self.defense_foil_lbl.config(
        text=f"🔴 Defense Foil ({tactics['defending_team']}): {tactics['defensive_structure']} (Line: {tactics['defensive_line_height_meters']}m)"
    )

    # 4. Render 22 Players from tracking data
    self.players = packet.get("player_tracking", {}).get("players", [])
    actor_id = packet["event"]["actor"]["id"]
    for p in self.players:
      px, py = self.to_screen_coords(p["x"], p["y"])
      r = 7
      focused = p["player_id"] == self.focus_id
      has_ball = p["player_id"] == actor_id

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
          outline=FOCUS_COLOR if focused else "#ffffff",
          width=3 if focused else 1,
          tags="dynamic_element",
      )
      # Role tag, or the name of the focused player
      self.canvas.create_text(
          px,
          py - (20 if has_ball else 11),
          text=p["name"] if focused else f'{p["role"]}: {p["player_id"]}',
          fill=FOCUS_COLOR if focused else "#f8fafc",
          font=("Segoe UI", 8 if focused else 7, "bold"),
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
        quality = match_intel.pass_quality(packet)
        if quality and self.profile["mode"] == "analyst":
          self.canvas.create_text(
              ex, ey + 14, text=f"{quality['label']} pass ({quality['difficulty']})",
              fill="#fde68a", font=("Segoe UI", 8, "bold"), tags="dynamic_element")

    ball_carrier = next(
        (p for p in self.players if p["player_id"] == actor_id), None)
    if ball_carrier:
      px, py = self.to_screen_coords(ball_carrier["x"], ball_carrier["y"])
      self.canvas.create_text(
          px,
          py,
          text=BALL_CARRIER_ICON,
          font=("Segoe UI Emoji", BALL_CARRIER_ICON_SIZE),
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
        text=f" {self.state_title}  ⏱ {entry.get('clock', '')}"
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
    # Poll match events (a packet that can't be drawn is skipped, never stops this loop)
    lines, self.file_pos = self.read_complete_lines(self.match_file_path, self.file_pos)
    for line in lines:
      try:
        self.render_frame(json.loads(line))
      except Exception as exc:
        print(f"Skipped a packet the window can't draw: {exc!r}", file=sys.stderr)

    # Poll AI Commentary (a bad row is skipped, never stops this loop)
    lines, self.comm_file_pos = self.read_complete_lines(
        self.commentary_file_path, self.comm_file_pos)
    for line in lines:
      try:
        entry = json.loads(line)
      except json.JSONDecodeError:
        continue
      if isinstance(entry, dict) and isinstance(entry.get("text"), str):
        text = personalize.row_text(entry, self.profile)
        self.comm_text.insert(
            tk.END, f"\n[{entry.get('clock', '')}] {text}\n{'-' * 42}\n")
        self.comm_text.see(tk.END)

    # Poll State of the Game (a bad line must never stop this loop)
    try:
      self.poll_state_feed()
    except Exception as exc:
      print(f"State of the Game panel error: {exc!r}", file=sys.stderr)

    self.root.after(200, self.poll_data_feed)


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Live match window.")
  parser.add_argument("--profile",
                      help="a viewer profile in profiles/ (e.g. casual_ko)")
  parser.add_argument("--data-dir",
                      help="read the feeds from here (default: football_data)")
  parser.add_argument("--replay", action="store_true", help="show the REPLAY badge")
  args = parser.parse_args()
  root = tk.Tk()
  app = FootballMatchGUI(
      root, personalize.load_profile(args.profile), args.data_dir, args.replay)
  root.mainloop()