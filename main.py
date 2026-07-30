from fastapi import FastAPI, Request
import sqlite3
import json

app = FastAPI()

# 1. Initialize a lightweight database inside the pod
conn = sqlite3.connect("telemetry.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT)")
conn.commit()

@app.post("/api/telemetry")
async def receive_telemetry(request: Request):
    data = await request.json()
    
    # 2. Save the incoming Unity JSON into the database
    cursor.execute("INSERT INTO telemetry (payload) VALUES (?)", (json.dumps(data),))
    conn.commit()
    
    return {"status": "success", "message": "Data saved to SQLite database"}

@app.get("/api/telemetry")
def get_telemetry():
    # 3. Retrieve all saved records so your terminal script can read them
    cursor.execute("SELECT payload FROM telemetry")
    rows = cursor.fetchall()
    
    # Convert the text back into JSON objects
    parsed_records = [json.loads(row[0]) for row in rows]
    return {"records": parsed_records}