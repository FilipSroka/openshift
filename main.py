import json
import os
from fastapi import FastAPI, Request, Response, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
import psycopg2

app = FastAPI()

# 1. Fetch the DATABASE_URL environment variable set in OpenShift
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Establish a connection to PostgreSQL using the OpenShift env variable."""
    return psycopg2.connect(DATABASE_URL)


@app.on_event("startup")
def startup_db():
    """Create tables on startup and recover the player ID if necessary."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # TABLE 1: Player profiles. 
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            player_id VARCHAR PRIMARY KEY,
            payload JSONB
        );
        """
    )
    
    # TABLE 2: Global config for IDs
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS global_config (
            key VARCHAR PRIMARY KEY,
            value INTEGER
        );
        """
    )
    
    # ID RECOVERY LOGIC: Scan the players table to find the highest ID.
    # This ensures that even if global_config is reset, we never start back at 0 
    # if a player already exists in the database.
    cursor.execute("SELECT player_id FROM players;")
    rows = cursor.fetchall()
    max_id = 0
    for row in rows:
        try:
            pid = int(row[0])
            if pid > max_id:
                max_id = pid
        except ValueError:
            pass

    # Insert or update the current_id to be at least the max_id found
    cursor.execute("SELECT value FROM global_config WHERE key = 'current_id';")
    result = cursor.fetchone()
    
    if not result:
        cursor.execute("INSERT INTO global_config (key, value) VALUES ('current_id', %s);", (max_id,))
    elif result[0] < max_id:
        cursor.execute("UPDATE global_config SET value = %s WHERE key = 'current_id';", (max_id,))
    
    conn.commit()
    cursor.close()
    conn.close()


# ---------------------------------------------------------
# PYDANTIC MODELS
# ---------------------------------------------------------
class GameUploadPayload(BaseModel):
    session_index: int
    game_index: int
    game_data: Dict[str, Any]


# ---------------------------------------------------------
# ROUTE 1 & 2: ID Management 
# ---------------------------------------------------------
@app.get("/api/id")
def get_current_id():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM global_config WHERE key = 'current_id';")
        result = cursor.fetchone()
        current_id = result[0] if result else 0
        return current_id
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/id")
async def update_current_id(request: Request):
    data = await request.json()
    new_id = data.get("id")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE global_config SET value = %s WHERE key = 'current_id';", 
            (new_id,)
        )
        conn.commit()
        return {"status": "success", "new_id": new_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------
# ROUTE 3: Download Entire Database
# ---------------------------------------------------------
@app.get("/api/players")
def get_all_players():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
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


# ---------------------------------------------------------
# ROUTE 4: Download Specific Player
# ---------------------------------------------------------
@app.get("/api/players/{player_id}")
def get_player_data(player_id: str):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
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


# ---------------------------------------------------------
# ROUTE 5: Store-and-Forward Game Upload
# ---------------------------------------------------------
@app.post("/api/players/{player_id}/games")
def upload_game_data(player_id: str, payload: GameUploadPayload):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT payload FROM players WHERE player_id = %s;", (player_id,))
        result = cur.fetchone()
        
        is_new_player = False
        
        if not result:
            player_profile = {
                "id": player_id,
                "group": "B" if int(player_id) % 2 == 0 else "A",
                "currentSession": payload.session_index,
                "currentGame": payload.game_index + 1,
                "sessions": []
            }
            is_new_player = True
        else:
            player_profile = result[0]
            
            # --- THE FIX ---
            # Updates the root metadata in the database whenever a game finishes!
            player_profile["currentSession"] = payload.session_index
            player_profile["currentGame"] = payload.game_index + 1
            
        if "sessions" not in player_profile:
            player_profile["sessions"] = []
            
        while len(player_profile["sessions"]) <= payload.session_index:
            player_profile["sessions"].append({"games": []})
            
        if "games" not in player_profile["sessions"][payload.session_index]:
            player_profile["sessions"][payload.session_index]["games"] = []
            
        session_games = player_profile["sessions"][payload.session_index]["games"]
        
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