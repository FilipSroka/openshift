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
# PYDANTIC MODELS
# ---------------------------------------------------------
class GameUploadPayload(BaseModel):
    session_index: int
    game_index: int
    game_data: Dict[str, Any]


# ---------------------------------------------------------
# API ENDPOINTS
# ---------------------------------------------------------

@app.get("/api/players")
def get_all_players():
    """
    Retrieves the JSON payloads for ALL players in the database.
    Returns them as a single JSON array. Highly optimized for memory.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT payload FROM players;")
        rows = cur.fetchall()
        
        if not rows:
            # Return an empty JSON array if the table is completely empty
            return Response(content="[]", media_type="application/json")
        
        # OPTIMIZATION: Stitch the raw JSON strings together manually.
        # This prevents Python from having to parse and re-serialize hundreds 
        # of megabytes of tracking data, saving massive amounts of RAM.
        json_strings = []
        for row in rows:
            # psycopg2 might return a dict for JSONB, or a string depending on extras.
            if isinstance(row[0], str):
                json_strings.append(row[0])
            else:
                json_strings.append(json.dumps(row[0]))
                
        # Wrap the joined strings in brackets to make a valid JSON array
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
    Perfect for downloading straight from the browser or via curl.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT payload FROM players WHERE player_id = %s;", (player_id,))
        result = cur.fetchone()
        
        if result:
            # result[0] contains your JSONB string/dict directly from PostgreSQL
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
    Receives an isolated game from Unity, safely finds the correct session, 
    checks for duplicates to handle network drops, and appends the new data.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. Fetch the current player JSON profile
        cur.execute("SELECT payload FROM players WHERE player_id = %s;", (player_id,))
        result = cur.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Player not found")
            
        player_profile = result[0]
        
        # 2. Ensure the 'sessions' array exists at the root
        if "sessions" not in player_profile:
            player_profile["sessions"] = []
            
        # 3. Pad sessions array if Unity sends a session_index that doesn't exist yet
        # (e.g., creating Session 1 when only Session 0 exists)
        while len(player_profile["sessions"]) <= payload.session_index:
            player_profile["sessions"].append({"games": []})
            
        # Target the specific session's games array
        # Ensure the 'games' array exists for this session just in case
        if "games" not in player_profile["sessions"][payload.session_index]:
            player_profile["sessions"][payload.session_index]["games"] = []
            
        session_games = player_profile["sessions"][payload.session_index]["games"]
        
        # 4. Handle duplicates and appending based on the game_index
        if payload.game_index < len(session_games):
            # DUPLICATE FOUND: Unity is resending a game we already have!
            # We overwrite it to ensure we have the most up-to-date data.
            session_games[payload.game_index] = payload.game_data
            status_msg = "Duplicate game overwritten successfully"
            
        elif payload.game_index == len(session_games):
            # NORMAL BEHAVIOR: It's the exact next game in the sequence. Append it.
            session_games.append(payload.game_data)
            status_msg = "New game appended successfully"
            
        else:
            # EDGE CASE: A game was skipped (e.g., game 1 failed, but game 2 uploaded).
            # Pad the array with empty objects to maintain the correct index order.
            while len(session_games) < payload.game_index:
                session_games.append({})
            session_games.append(payload.game_data)
            status_msg = "Game appended with out-of-order padding"

        # 5. Save the fully updated profile back to PostgreSQL
        cur.execute(
            "UPDATE players SET payload = %s WHERE player_id = %s;",
            (json.dumps(player_profile), player_id)
        )
        conn.commit()
        
        # Return 200 OK so Unity knows it is safe to DELETE the local file
        return {"status": "success", "detail": status_msg}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()