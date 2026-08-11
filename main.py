from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import Dict, Any
import json
import psycopg2
import os

app = FastAPI()

# ---------------------------------------------------------
# DATABASE CONNECTION
# ---------------------------------------------------------
def get_db_connection():
    """
    Connects to the PostgreSQL database.
    (Make sure these match your OpenShift database credentials/services)
    """
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME", "telemetry_db"),
        user=os.getenv("DB_USER", "telemetry_user"),
        password=os.getenv("DB_PASSWORD", "telemetry_password"), # Update this to your DB password
        host=os.getenv("DB_HOST", "postgresql"), # Default OpenShift internal service name
        port=os.getenv("DB_PORT", "5432")
    )


# ---------------------------------------------------------
# PYDANTIC MODELS (Defines what FastAPI expects to receive)
# ---------------------------------------------------------
class GameUploadPayload(BaseModel):
    session_index: int
    game_index: int
    game_data: Dict[str, Any]

class IdPayload(BaseModel):
    id: int


# ---------------------------------------------------------
# API ENDPOINTS
# ---------------------------------------------------------

@app.get("/api/id")
def get_current_id():
    """
    Returns the current highest player ID. 
    Automatically syncs with your existing database to prevent resetting to 1!
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS global_settings (key VARCHAR PRIMARY KEY, value INTEGER);")
        cur.execute("SELECT value FROM global_settings WHERE key = 'current_player_id';")
        result = cur.fetchone()
        
        # If we have a saved value and it's greater than 0, use it
        if result and result[0] > 0:
            return result[0]
        else:
            # BIG FIX: Look at the existing players table to find the highest ID!
            cur.execute("CREATE TABLE IF NOT EXISTS players (player_id VARCHAR PRIMARY KEY, payload JSONB);")
            cur.execute("SELECT player_id FROM players;")
            rows = cur.fetchall()
            
            max_id = 0
            for row in rows:
                try:
                    # Convert player_id string (like "8") to integer
                    pid = int(row[0])
                    if pid > max_id:
                        max_id = pid
                except ValueError:
                    pass
                    
            # Save this high-water mark so we don't start at 0 next time
            cur.execute(
                "INSERT INTO global_settings (key, value) VALUES ('current_player_id', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;", 
                (max_id,)
            )
            conn.commit()
            
            return max_id
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@app.post("/api/id")
def set_current_id(payload: IdPayload):
    """
    Unity calls this to increment the global player ID so the next headset gets a new number.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS global_settings (key VARCHAR PRIMARY KEY, value INTEGER);")
        cur.execute(
            "INSERT INTO global_settings (key, value) VALUES ('current_player_id', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;",
            (payload.id,)
        )
        conn.commit()
        return {"status": "success", "new_id": payload.id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@app.get("/api/players")
def get_all_players():
    """
    Retrieves the JSON payloads for ALL players in the database.
    Returns them as a single JSON array. Highly optimized for memory.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS players (player_id VARCHAR PRIMARY KEY, payload JSONB);")
        cur.execute("SELECT payload FROM players;")
        rows = cur.fetchall()
        
        if not rows:
            return Response(content="[]", media_type="application/json")
        
        json_strings = []
        for row in rows:
            if isinstance(row[0], str):
                json_strings.append(row[0])
            else:
                json_strings.append(json.dumps(row[0]))
                
        combined_json = "[" + ",".join(json_strings) + "]"
        return Response(content=combined_json, media_type="application/json")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@app.get("/api/players/{player_id}")
def get_player_data(player_id: str):
    """
    Retrieves the entire JSON payload for a specific player.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS players (player_id VARCHAR PRIMARY KEY, payload JSONB);")
        cur.execute("SELECT payload FROM players WHERE player_id = %s;", (player_id,))
        result = cur.fetchone()
        
        if result:
            content = result[0] if isinstance(result[0], str) else json.dumps(result[0])
            return Response(content=content, media_type="application/json")
        else:
            raise HTTPException(status_code=404, detail="Player not found")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@app.post("/api/players/{player_id}/games")
def upload_game_data(player_id: str, payload: GameUploadPayload):
    """
    Receives an isolated game from Unity. If the player doesn't exist, it creates them.
    Safely finds the correct session, checks for duplicates, and appends the new data.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS players (player_id VARCHAR PRIMARY KEY, payload JSONB);")
        
        # 1. Fetch the current player JSON profile
        cur.execute("SELECT payload FROM players WHERE player_id = %s;", (player_id,))
        result = cur.fetchone()
        
        is_new_player = False
        
        if not result:
            # CREATE THE PLAYER ON THE FLY!
            player_profile = {
                "id": player_id,
                "group": "B" if int(player_id) % 2 == 0 else "A",
                "currentSession": payload.session_index,
                "currentGame": payload.game_index,
                "sessions": []
            }
            is_new_player = True
        else:
            player_profile = result[0]
            
        # 2. Ensure the 'sessions' array exists at the root
        if "sessions" not in player_profile:
            player_profile["sessions"] = []
            
        # 3. Pad sessions array if Unity sends a session_index that doesn't exist yet
        while len(player_profile["sessions"]) <= payload.session_index:
            player_profile["sessions"].append({"games": []})
            
        # Ensure the 'games' array exists for this session
        if "games" not in player_profile["sessions"][payload.session_index]:
            player_profile["sessions"][payload.session_index]["games"] = []
            
        session_games = player_profile["sessions"][payload.session_index]["games"]
        
        # 4. Handle duplicates and appending
        if payload.game_index < len(session_games):
            session_games[payload.game_index] = payload.game_data
            status_msg = "Duplicate game overwritten successfully"
        elif payload.game_index == len(session_games):
            session_games.append(payload.game_data)
            status_msg = "New game appended successfully"
        else:
            while len(session_games) < payload.game_index:
                session_games.append({})
            session_games.append(payload.game_data)
            status_msg = "Game appended with out-of-order padding"

        # 5. Save the fully updated profile back to PostgreSQL
        if is_new_player:
            cur.execute(
                "INSERT INTO players (player_id, payload) VALUES (%s, %s);",
                (player_id, json.dumps(player_profile))
            )
        else:
            cur.execute(
                "UPDATE players SET payload = %s WHERE player_id = %s;",
                (json.dumps(player_profile), player_id)
            )
            
        conn.commit()
        return {"status": "success", "detail": status_msg}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()