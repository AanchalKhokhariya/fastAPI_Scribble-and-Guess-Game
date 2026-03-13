from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.templating import Jinja2Templates
from typing import List

app = FastAPI()
templates = Jinja2Templates(directory="templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.game_state = {"movie": "", "display_name": "", "drawer_assigned": False}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)

manager = ConnectionManager()

def process_movie(movie: str):
    vowels = "AEIOUaeiou"
    return "".join([char if (char in vowels or char == " ") else "_" for char in movie])

@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    role = "guesser"
    if not manager.game_state["drawer_assigned"]:
        role = "drawer"
        manager.game_state["drawer_assigned"] = True
    
    await websocket.send_json({"type": "role", "role": role})

    try:
        while True:
            data = await websocket.receive_json()
            if data["type"] == "set_movie":
                movie_name = data["movie"].upper()
                manager.game_state["movie"] = movie_name
                manager.game_state["display_name"] = process_movie(movie_name)
                await manager.broadcast({
                    "type": "game_start",
                    "display": manager.game_state["display_name"],
                    "full_movie": movie_name
                })
            elif data["type"] == "drawing":
                await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)