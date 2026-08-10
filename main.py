import json
import os
from fastapi import FastAPI, Request
import psycopg2

app = FastAPI()

# 1. Fetch the DATABASE_URL environment variable set in OpenShift
DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    """Establish a connection to PostgreSQL."""
    return psycopg2.connect(DATABASE_URL)


@app.on_event("startup")
def startup_db():
    """Create tables on startup if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # TABLE 1: Player profiles. 
    # Notice we use player_id as the PRIMARY KEY now.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            player_id VARCHAR PRIMARY KEY,
            payload JSONB
        );
        """
    )
    
    # TABLE 2: A simple table to keep track of the current max ID, 
    # replacing Firebase's "id" node.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS global_config (
            key VARCHAR PRIMARY KEY,
            value INTEGER
        );
        """
    )
    
    # Initialize the ID counter to 0 if the table is brand new
    cursor.execute(
        "INSERT INTO global_config (key, value) VALUES ('current_id', 0) ON CONFLICT DO NOTHING;"
    )
    
    conn.commit()
    cursor.close()
    conn.close()


# ---------------------------------------------------------
# ROUTE 1: Save or Override Player Profile
# ---------------------------------------------------------
@app.post("/api/players/{player_id}")
async def save_player(player_id: str, request: Request):
    data = await request.json()
    conn = get_db_connection()
    cursor = conn.cursor()

    # The magic happens here: ON CONFLICT DO UPDATE acts exactly like Firebase's Set/Override
    cursor.execute(
        """
        INSERT INTO players (player_id, payload) 
        VALUES (%s, %s)
        ON CONFLICT (player_id) 
        DO UPDATE SET payload = EXCLUDED.payload;
        """,
        (player_id, json.dumps(data))
    )
    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "success", "message": f"Data for player {player_id} saved/overridden"}


# ---------------------------------------------------------
# ROUTE 2: Get the current ID (For new players)
# ---------------------------------------------------------
@app.get("/api/id")
def get_current_id():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT value FROM global_config WHERE key = 'current_id';")
    current_id = cursor.fetchone()[0]

    cursor.close()
    conn.close()
    
    return current_id


# ---------------------------------------------------------
# ROUTE 3: Update the current ID (When a new player joins)
# ---------------------------------------------------------
@app.post("/api/id")
async def update_current_id(request: Request):
    data = await request.json()
    new_id = data.get("id")
    
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE global_config SET value = %s WHERE key = 'current_id';", 
        (new_id,)
    )
    
    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "success", "new_id": new_id}