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
  """Create table on startup if it doesn't exist."""
  conn = get_db_connection()
  cursor = conn.cursor()
  # Uses PostgreSQL's SERIAL for auto-increment and native JSONB for efficient JSON storage
  cursor.execute(
      "CREATE TABLE IF NOT EXISTS telemetry (id SERIAL PRIMARY KEY, payload"
      " JSONB);"
  )
  conn.commit()
  cursor.close()
  conn.close()


@app.post("/api/telemetry")
async def receive_telemetry(request: Request):
  data = await request.json()

  conn = get_db_connection()
  cursor = conn.cursor()

  # Note: PostgreSQL uses '%s' for parameterized queries instead of SQLite's '?'
  cursor.execute(
      "INSERT INTO telemetry (payload) VALUES (%s);", (json.dumps(data),)
  )
  conn.commit()
  cursor.close()
  conn.close()

  return {"status": "success", "message": "Data saved to PostgreSQL database"}


@app.get("/api/telemetry")
def get_telemetry():
  conn = get_db_connection()
  cursor = conn.cursor()

  cursor.execute("SELECT payload FROM telemetry;")
  rows = cursor.fetchall()

  cursor.close()
  conn.close()

  # PostgreSQL automatically parses JSONB columns into Python dictionaries
  parsed_records = [
      row[0] if isinstance(row[0], (dict, list)) else json.loads(row[0])
      for row in rows
  ]
  return {"records": parsed_records}