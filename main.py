import random
import asyncio
import time
import pandas as pd
import fakeredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, Cookie, Form
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from typing import List, Dict

app = FastAPI()

r = fakeredis.FakeRedis(decode_responses=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

@app.on_event("shutdown")
def shutdown_event():
    """Clean up Redis when the server stops"""
    print("Server shutting down... clearing session data.")
    r.delete("round_end_time")
    
def load_movies_after_2000():
    try:
        df = pd.read_csv("IMDB_5000_Movie_Dataset_1547_45.csv")
        df = df[df['title_year'] >= 2000]
        movies = df['movie_title'].dropna().tolist()
        movies = [m.strip().upper() for m in movies]
        return movies if movies else ["INCEPTION"]
    except Exception as e:
        return ["INCEPTION"]

MOVIE_POOL = load_movies_after_2000()

def get_random_movie():
    return random.choice(MOVIE_POOL)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.ws_to_name: Dict[int, str] = {}
        self.draw_history: List[dict] = []
        self.round_timer_task = None  
        
        self.game_state = {
            "movie": "",
            "display_name": "",
            "drawer_assigned": False,
            "drawer_name": None,
            "is_round_active": False,
            "winner_announcement": None,
            "revealed_movie": None
        }

    def get_player_score(self, name: str):
        score = r.get(f"score:{name}")
        return int(score) if score else 0

    def set_player_score(self, name: str, points: int):
        current_score = self.get_player_score(name)
        new_score = current_score + points
        r.set(f"score:{name}", new_score)

    def get_remaining_time(self):
        end_time = r.get("round_end_time")
        if end_time:
            remaining = int(float(end_time) - time.time())
            return max(0, remaining)
        return 0

    def get_player_data(self):
        players = [{"name": name, "score": self.get_player_score(name)} 
                   for name in self.active_connections.keys()]
        return sorted(players, key=lambda x: x['score'], reverse=True)

    async def connect(self, websocket: WebSocket, name: str):
        await websocket.accept()
        ws_id = id(websocket)
        self.active_connections[name] = websocket
        self.ws_to_name[ws_id] = name
        
        if r.get(f"score:{name}") is None:
            r.set(f"score:{name}", 0)
        
        role = "guesser"
        if not self.game_state["drawer_assigned"]:
            self.game_state["drawer_assigned"] = True
            self.game_state["drawer_name"] = name
            role = "drawer"
        
        await self.broadcast({"type": "player_list", "players": self.get_player_data()})
        return role

    async def disconnect(self, websocket: WebSocket):
        ws_id = id(websocket)
        name = self.ws_to_name.get(ws_id)
        if name:
            if name in self.active_connections:
                del self.active_connections[name]
            del self.ws_to_name[ws_id]
            is_drawer = (name == self.game_state["drawer_name"])
            await self.broadcast({"type": "player_list", "players": self.get_player_data()})
            if is_drawer:
                if self.round_timer_task:
                    self.round_timer_task.cancel()
                self.game_state["drawer_assigned"] = False
                self.game_state["drawer_name"] = None
                r.delete("round_end_time")
                return True
        return False

    async def start_round_timer(self, duration=480):
        if self.round_timer_task:
            self.round_timer_task.cancel()
        
        end_timestamp = time.time() + duration
        r.set("round_end_time", end_timestamp)

        async def timer():
            try:
                await asyncio.sleep(duration)  
                if self.game_state["is_round_active"]:
                    self.game_state["is_round_active"] = False
                    self.game_state["winner_announcement"] = "⏰ Time's up! No one guessed."
                    self.game_state["revealed_movie"] = self.game_state["movie"]
                    r.delete("round_end_time")
                    await self.broadcast({
                        "type": "announcement",
                        "message": self.game_state["winner_announcement"],
                        "reveal": self.game_state["revealed_movie"]
                    })
                    await asyncio.sleep(5)
                    await self.restart_game()
            except asyncio.CancelledError:
                pass
        self.round_timer_task = asyncio.create_task(timer())

    async def restart_game(self):
        if self.round_timer_task:
            self.round_timer_task.cancel()
            self.round_timer_task = None
        
        r.delete("round_end_time")
        self.game_state.update({
            "movie": "", "display_name": "", "is_round_active": False,
            "winner_announcement": None, "revealed_movie": None
        })
        self.draw_history = []
        if not self.active_connections:
            return
        
        old_drawer_name = self.game_state["drawer_name"]
        names = list(self.active_connections.keys())
        if len(names) > 1 and old_drawer_name in names:
            names.remove(old_drawer_name)
        
        new_drawer_name = random.choice(names)
        self.game_state["drawer_name"] = new_drawer_name
        self.game_state["drawer_assigned"] = True

        for name, ws in self.active_connections.items():
            role = "drawer" if name == new_drawer_name else "guesser"
            await ws.send_json({
                "type": "init",
                "role": role,
                "movie_set": False,
                "drawer_name": new_drawer_name
            })

    async def broadcast(self, message: dict):
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_json(message)
            except:
                continue

manager = ConnectionManager()

def process_movie(movie: str):
    vowels = "AEIOUaeiou "
    return "".join([char if (char in vowels or not char.isalnum()) else "_" for char in movie])

@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("front_page.html", {"request": request})

@app.post("/join")
async def join_game(name: str = Form(...)):
    response = RedirectResponse(url="/game", status_code=303)
    response.set_cookie(key="player_name", value=name, httponly=True)
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("player_name")
    return response

@app.get("/game")
async def get_game(request: Request, player_name: str = Cookie(None)):
    if not player_name:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "player_name": player_name})

@app.websocket("/ws/{name}")
async def websocket_endpoint(websocket: WebSocket, name: str):
    role = await manager.connect(websocket, name)
    current_time_left = manager.get_remaining_time()

    await websocket.send_json({
        "type": "init", "role": role, "movie_set": bool(manager.game_state["movie"]),
        "display": manager.game_state["display_name"], "full_movie": manager.game_state["movie"],
        "drawer_name": manager.game_state["drawer_name"], "history": manager.draw_history,
        "winner_msg": manager.game_state["winner_announcement"], "revealed": manager.game_state["revealed_movie"],
        "time_left": current_time_left 
    })
    
    try:
        while True:
            data = await websocket.receive_json()
            if data["type"] == "set_movie":
                manager.game_state["movie"] = data["movie"].upper()
                manager.game_state["display_name"] = process_movie(manager.game_state["movie"])
                manager.game_state["is_round_active"] = True
                await manager.start_round_timer(480)
                await manager.broadcast({
                    "type": "game_start", "display": manager.game_state["display_name"],
                    "full_movie": manager.game_state["movie"], "drawer_name": manager.game_state["drawer_name"],
                    "time_left": 480
                })
            elif data["type"] == "won" and manager.game_state["is_round_active"]:
                manager.game_state["is_round_active"] = False
                if manager.round_timer_task:
                    manager.round_timer_task.cancel()
                r.delete("round_end_time")
                manager.set_player_score(name, 50)
                if manager.game_state["drawer_name"]:
                    manager.set_player_score(manager.game_state["drawer_name"], 25)
                manager.game_state["winner_announcement"] = f"🎉 {name} guessed it first!"
                manager.game_state["revealed_movie"] = manager.game_state["movie"]
                await manager.broadcast({"type": "player_list", "players": manager.get_player_data()})
                await manager.broadcast({
                    "type": "announcement", "message": manager.game_state["winner_announcement"], "reveal": manager.game_state["revealed_movie"]
                })
            elif data["type"] == "restart":
                await manager.restart_game()
            elif data["type"] == "drawing":
                manager.draw_history.append(data)
                await manager.broadcast(data)
            elif data["type"] == "clear":
                manager.draw_history = []
                await manager.broadcast(data)
            elif data["type"] == "random_movie":
                if name == manager.game_state["drawer_name"]:
                    movie = get_random_movie()
                    manager.game_state["movie"] = movie
                    manager.game_state["display_name"] = process_movie(movie)
                    manager.game_state["is_round_active"] = True
                    await manager.start_round_timer(480)
                    await manager.broadcast({
                        "type": "game_start", "display": manager.game_state["display_name"],
                        "full_movie": manager.game_state["movie"], "drawer_name": manager.game_state["drawer_name"],
                        "time_left": 480
                    })
    except WebSocketDisconnect:
        if await manager.disconnect(websocket):
            await manager.broadcast({"type": "drawer_disconnected"})