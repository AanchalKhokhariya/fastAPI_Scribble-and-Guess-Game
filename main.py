import random
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        self.player_names: Dict[int, str] = {}
        self.draw_history: List[dict] = [] 
        
        self.game_state = {
            "movie": "",
            "display_name": "",
            "drawer_assigned": False,
            "drawer_id": None,
            "is_round_active": False,
            "winner_announcement": None,
            "revealed_movie": None
        }

    async def connect(self, websocket: WebSocket, name: str):
        await websocket.accept()
        ws_id = id(websocket)
        self.active_connections[ws_id] = websocket
        self.player_names[ws_id] = name
        
        if not self.game_state["drawer_assigned"]:
            self.game_state["drawer_assigned"] = True
            self.game_state["drawer_id"] = ws_id
            return "drawer"
        return "guesser"

    def disconnect(self, websocket: WebSocket):
        ws_id = id(websocket)
        is_drawer = (ws_id == self.game_state["drawer_id"])
        
        if ws_id in self.active_connections:
            del self.active_connections[ws_id]
        if ws_id in self.player_names:
            del self.player_names[ws_id]
        
        if is_drawer:
            self.game_state["drawer_assigned"] = False
            self.game_state["drawer_id"] = None
            return True 
        return False

    async def restart_game(self):
        self.game_state.update({
            "movie": "", 
            "display_name": "", 
            "is_round_active": False,
            "winner_announcement": None, 
            "revealed_movie": None
        })
        self.draw_history = [] 
        
        if not self.active_connections:
            return

        old_drawer_id = self.game_state["drawer_id"]
        conn_ids = list(self.active_connections.keys())
        if len(conn_ids) > 1:
            conn_ids = [cid for cid in conn_ids if cid != old_drawer_id]
        
        new_drawer_id = random.choice(conn_ids)
        self.game_state["drawer_id"] = new_drawer_id
        self.game_state["drawer_assigned"] = True

        for ws_id, ws in self.active_connections.items():
            role = "drawer" if ws_id == new_drawer_id else "guesser"
            await ws.send_json({
                "type": "init", 
                "role": role, 
                "movie_set": False, 
                "drawer_name": self.player_names[new_drawer_id]
            })

    async def broadcast(self, message: dict):
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_json(message)
            except:
                continue

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

@app.websocket("/ws/{name}")
async def websocket_endpoint(websocket: WebSocket, name: str):
    role = await manager.connect(websocket, name)
    drawer_name = manager.player_names.get(manager.game_state["drawer_id"], "Unknown")
    
    await websocket.send_json({
        "type": "init", 
        "role": role,
        "movie_set": bool(manager.game_state["movie"]),
        "display": manager.game_state["display_name"],
        "full_movie": manager.game_state["movie"],
        "drawer_name": drawer_name,
        "history": manager.draw_history, 
        "winner_msg": manager.game_state["winner_announcement"],
        "revealed": manager.game_state["revealed_movie"]
    })

    try:
        while True:
            data = await websocket.receive_json()
            
            if data["type"] == "set_movie":
                manager.game_state["movie"] = data["movie"].upper()
                manager.game_state["display_name"] = process_movie(manager.game_state["movie"])
                manager.game_state["is_round_active"] = True
                await manager.broadcast({
                    "type": "game_start",
                    "display": manager.game_state["display_name"],
                    "full_movie": manager.game_state["movie"],
                    "drawer_name": manager.player_names[manager.game_state["drawer_id"]]
                })
            elif data["type"] == "won" and manager.game_state["is_round_active"]:
                manager.game_state["is_round_active"] = False 
                manager.game_state["winner_announcement"] = f"🎉 {data['name']} guessed it first!"
                manager.game_state["revealed_movie"] = manager.game_state["movie"]
                await manager.broadcast({
                    "type": "announcement",
                    "message": manager.game_state["winner_announcement"],
                    "reveal": manager.game_state["revealed_movie"]
                })
            
            elif data["type"] == "restart":
                await manager.restart_game()
            
            elif data["type"] == "drawing":
                manager.draw_history.append(data) 
                await manager.broadcast(data)
            
            elif data["type"] == "clear":
                manager.draw_history = []
                await manager.broadcast(data)
                
    except WebSocketDisconnect:
        if manager.disconnect(websocket):
            await manager.broadcast({"type": "drawer_disconnected"})