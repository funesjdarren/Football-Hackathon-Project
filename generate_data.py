import json
import time
import random
from datetime import datetime
import os

# Lists for completely synthetic names
SYNTHETIC_TEAMS = ["FC Alpha", "Beta United", "Gamma City", "Delta Rovers", "Omega Athletic"]
FIRST_NAMES = ["Liam", "Noah", "Oliver", "Elijah", "James", "William", "Benjamin", "Lucas", "Henry", "Alexander"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]
POSITIONS = ["Goalkeeper", "Defender", "Midfielder", "Forward"]

def generate_rosters():
    rosters = {}
    for team in SYNTHETIC_TEAMS:
        rosters[team] = [
            {
                "id": random.randint(1000, 9999),
                "name": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
                "position": random.choice(POSITIONS)
            } for _ in range(11)
        ]
    return rosters

def generate_live_soccer_stream():
    rosters = generate_rosters()
    
    # Pick two random synthetic teams for this match simulation
    team_a, team_b = random.sample(SYNTHETIC_TEAMS, 2)
    match_id = random.randint(10000, 99999)
    
    current_team = team_a
    minute = 0
    second = 0
    event_id_counter = 100000
    
    print(f"--- SIMULATION STARTED: {team_a} vs {team_b} ---")
    
    while minute < 90:
        event_id_counter += 1
        
        # Determine event type based on basic weights
        event_pool = ["Pass", "Pass", "Pass", "Dribble", "Foul", "Tackle", "Shot"]
        event_type = random.choice(event_pool)
        
        # Pick actor and recipient from the team currently in possession
        actor = random.choice(rosters[current_team])
        recipient = random.choice([p for p in rosters[current_team] if p["id"] != actor["id"]])
        
        # Generate random soccer grid coordinates (standard 120 x 80 pitch metric)
        start_x = round(random.uniform(0, 120), 1)
        start_y = round(random.uniform(0, 80), 1)
        end_x = round(min(120, max(0, start_x + random.uniform(-15, 20))), 1)
        end_y = round(min(80, max(0, start_y + random.uniform(-20, 20))), 1)
        
        # Build base event structure
        payload = {
            "event_id": f"syn-ev-{event_id_counter}",
            "match_id": match_id,
            "stream_timestamp": datetime.utcnow().isoformat() + "Z",
            "match_time": {
                "period": 1 if minute < 45 else 2,
                "minute": minute,
                "second": second
            },
            "type": event_type,
            "possession_team": current_team,
            "actor": actor
        }
        
        # Inject event-specific metadata logic
        if event_type == "Pass":
            outcome = "Complete" if random.random() > 0.2 else "Incomplete"
            payload["details"] = {
                "coordinates": {"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y},
                "outcome": outcome,
                "recipient": recipient if outcome == "Complete" else None
            }
            if outcome == "Incomplete":
                current_team = team_b if current_team == team_a else team_a
                
        elif event_type == "Shot":
            outcome = random.choice(["Goal", "Saved", "Missed", "Blocked"])
            payload["details"] = {
                "coordinates": {"start_x": start_x, "start_y": start_y},
                "outcome": outcome
            }
            # Shifting possession back to defending team after a shot
            current_team = team_b if current_team == team_a else team_a
            
        elif event_type in ["Tackle", "Foul"]:
            defending_team = team_b if current_team == team_a else team_a
            target = random.choice(rosters[defending_team])
            payload["details"] = {
                "coordinates": {"x": start_x, "y": start_y},
                "opponent_involved": target
            }
            if event_type == "Tackle" and random.random() > 0.4:
                current_team = defending_team  # Turnover!

        else: # Dribble
            payload["details"] = {
                "coordinates": {"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y}
            }

        # Output the live simulated JSON payload string

        save_path = os.path.join('football_data', 'match_data.json')
        if os.path.exists(save_path):
            with open(save_path, 'r') as f:
                football_json = json.load(f)
            football_json[event_id_counter] = payload
            with open(save_path, 'w') as f:
                json.dump(football_json, f, indent=4)

        else:
            with open(save_path, 'w') as f:
                json.dump(football_json, f, indent=4)
        
        print(json.dumps(payload, indent=2))
        
        # Progress match time incrementally (Simulating an event every 2 to 6 seconds)
        time_increment = random.randint(2, 6)
        second += time_increment
        if second >= 60:
            minute += second // 60
            second = second % 60
            
        # Real-time sleep interval to replicate streaming engine ingestion 
        # (Change to 0.1 for high-speed stress-testing)
        time.sleep(2)

if __name__ == "__main__":
    generate_live_soccer_stream()