import random
import asyncio
import time
import string 
import pandas as pd
import fakeredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, Cookie
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict
from fastapi.responses import RedirectResponse

app = FastAPI()
r = fakeredis.FakeRedis(decode_responses=True)
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

rooms: Dict[str, ConnectionManager] = {}

@app.post("/join")
async def join(
    name: str = Form(...), 
    room_code: str = Form(None), 
    action: str = Form(...)
):
    
    if room_code and room_code.strip():
        room_code = room_code.upper().strip()
       
        if action != "create": 
            action = "join"

    if action == "create":
        room_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        rooms[room_code] = ConnectionManager()
        
    elif action == "join":
        if not room_code:
            return RedirectResponse(url="/?error=missing_code", status_code=303)
        
        if room_code not in rooms:
            return RedirectResponse(url=f"/?error=not_found&code={room_code}", status_code=303)

    response = RedirectResponse(url="/game", status_code=303)
    
    response.set_cookie(key="username", value=name)
    response.set_cookie(key="room_id", value=room_code) 
    
    print(f"DEBUG: Cookies set for {name} in room {room_code}")
    return response

@app.on_event("shutdown")
def shutdown_event():
    r.delete("round_end_time")
    
def load_movies_by_section():
    try:
        df = pd.read_csv("Movie_Names_Dataset.csv")
        sections = {}
        for section in df['Section'].unique():
            movies = df[df['Section'] == section]['Movie Name'].dropna().tolist()
            sections[section] = [m.strip().upper() for m in movies]
        return sections
    except Exception as e:
        return {"Hollywood": ["INCEPTION"], "Bollywood": ["SHOLAY"]}


MOVIE_POOL_DICT = load_movies_by_section()

def get_random_movie():
    return random.choice(MOVIE_POOL_DICT)

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
        """Calculates remaining seconds based on the end_time stored in Redis"""
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

        if name == self.game_state["drawer_name"]:
            role = "drawer"

        elif not self.game_state["drawer_assigned"]:
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
                print("Drawer disconnected, waiting for reconnect...")
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


rooms: Dict[str, ConnectionManager] = {}

@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("front_page.html", {"request": request})

@app.get("/game")
async def get_game(request: Request, room_id: str = Cookie(None)):
    # If the user tries to go to /game without joining a room first
    if not room_id:
        return RedirectResponse(url="/", status_code=303)
    
    # We pass room_id to the template just so the UI can display "ROOM: ABCD"
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "room_code": room_id
    })

@app.websocket("/ws") # Removed /{room_code} from the path
async def websocket_endpoint(
    websocket: WebSocket, 
    username: str = Cookie(None), 
    room_id: str = Cookie(None) # Get room from cookie
):
    if not username or not room_id or room_id not in rooms:
        print("REJECTED: Missing credentials or invalid room cookie.")
        await websocket.close()
        return

    manager = rooms[room_id]
    role = await manager.connect(websocket, username)
    name = username
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
                    
                    section = data.get("section", "Hollywood")
                    pool = MOVIE_POOL_DICT.get(section, MOVIE_POOL_DICT.get("Hollywood"))
                    
                    options = random.sample(pool, min(3, len(pool)))
                    await websocket.send_json({
                        "type": "movie_options",
                        "options": options
                    })
            elif data["type"] == "select_movie":
                if name == manager.game_state["drawer_name"]:
                    movie = data["movie"]
                    manager.game_state["movie"] = movie
                    manager.game_state["display_name"] = process_movie(movie)
                    manager.game_state["is_round_active"] = True
                    await manager.start_round_timer(480)
                    await manager.broadcast({
                        "type": "game_start",
                        "display": manager.game_state["display_name"],
                        "full_movie": manager.game_state["movie"],
                        "drawer_name": manager.game_state["drawer_name"],
                        "time_left": 480
                    })
    except WebSocketDisconnect:
        if await manager.disconnect(websocket):
            await manager.broadcast({"type": "drawer_disconnected"}) 