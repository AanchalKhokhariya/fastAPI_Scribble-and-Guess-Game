import random
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.templating import Jinja2Templates
from typing import List

app = FastAPI()
templates = Jinja2Templates(directory="templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.game_state = {
            "movie": "",
            "display_name": "",
            "drawer_assigned": False,
            "drawer_id": None
        }

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        
        if not self.game_state["drawer_assigned"]:
            self.game_state["drawer_assigned"] = True
            self.game_state["drawer_id"] = id(websocket)
            return "drawer"
        return "guesser"

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if id(websocket) == self.game_state["drawer_id"]:
            self.game_state["drawer_assigned"] = False
            self.game_state["drawer_id"] = None

    async def restart_game(self):
        self.game_state["movie"] = ""
        self.game_state["display_name"] = ""
        
        if not self.active_connections:
            return

        old_drawer_id = self.game_state["drawer_id"]
        potential_drawers = self.active_connections
        
        if len(potential_drawers) > 1:
            potential_drawers = [ws for ws in potential_drawers if id(ws) != old_drawer_id]
        
        new_drawer_ws = random.choice(potential_drawers)
        self.game_state["drawer_id"] = id(new_drawer_ws)
        self.game_state["drawer_assigned"] = True

        for ws in self.active_connections:
            new_role = "drawer" if id(ws) == self.game_state["drawer_id"] else "guesser"
            await ws.send_json({"type": "init", "role": new_role, "movie_set": False})

    async def broadcast(self, message: dict):
        for connection in self.active_connections[:]:
            try:
                await connection.send_json(message)
            except:
                self.active_connections.remove(connection)

manager = ConnectionManager()

def process_movie(movie: str):
    vowels = "AEIOUaeiou"
    return "".join([char if (char in vowels or char == " ") else "_" for char in movie])

@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("front_page.html", {"request": request})

@app.get("/game")
async def get_game(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    role = await manager.connect(websocket)
    
    await websocket.send_json({
        "type": "init", 
        "role": role,
        "movie_set": bool(manager.game_state["movie"]),
        "display": manager.game_state["display_name"],
        "full_movie": manager.game_state["movie"]
    })

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
            elif data["type"] == "restart":
                await manager.restart_game()
            elif data["type"] in ["drawing", "clear"]:
                await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)