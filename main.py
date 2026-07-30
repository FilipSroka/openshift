from fastapi import FastAPI, Request
import json

app = FastAPI()

@app.post("/api/telemetry")
async def receive_telemetry(request: Request):
    data = await request.json()
    
    # For now, this will just print the JSON payload to the OpenShift pod logs
    print(json.dumps(data, indent=2))
    
    return {"status": "success", "message": "Telemetry received"}