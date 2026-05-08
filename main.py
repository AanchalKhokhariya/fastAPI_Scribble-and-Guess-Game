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

# Updated Room Model in main.py
class GameRoom:
    def __init__(self, room_id: str, host: str, room_type: str, max_players: int, duration: int = 5):
        self.room_id = room_id
        self.host = host
        self.room_type = room_type
        self.max_players = max_players
        self.players: List[str] = []
        self.status = "LOBBY"  # Status: LOBBY, PLAYING
        self.game_started = False
        # Pass the duration to the manager!
        self.manager = ConnectionManager(duration_mins=duration)

    def is_full(self):
        return len(self.players) >= self.max_players

    def add_player(self, name: str):
        if not self.is_full():
            self.players.append(name)

    def remove_player(self, name: str):
        if name in self.players:
            self.players.remove(name)

    def should_start_game(self):
        return self.room_type == "public" and self.is_full()
    
    def transfer_host(self):
        """Transfers host role to another player. Returns new host name or None if no players left."""
        if not self.players:
            return None
        
        if self.host in self.players:
            self.players.remove(self.host)
        
        if not self.players:
            return None
        
        new_host = self.players[0]
        self.host = new_host
        return new_host



public_rooms: Dict[str, GameRoom] = {} 
lobby_connections: List[WebSocket] = [] 

@app.post("/join")
async def join(
    name: str = Form(...),
    room_code: str = Form(None),
    action: str = Form(...),
    room_type: str = Form("private"),  
    max_players: int = Form(6), # This captures the value from the slider
    duration: int = Form(5)
):
    print(f"[DEBUG] Action: {action} | User: {name} | Type: {room_type}")
    if action == "create":
        max_players = max(2, min(12, max_players)) 
        
        room_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

        room = GameRoom(
            room_id=room_code,
            host=name,
            room_type=room_type,
            max_players=max_players,
            duration=duration  
        )

        room.add_player(name)
        rooms[room_code] = room
        print(f"[DEBUG] Room Created: {room_code} by {name} | Type: {room_type} (Max: {max_players})")

        if room_type == "public":
            print(f"[DEBUG] Public Room added to lobby: {room_code}")
            await broadcast_lobby_update()

    elif action == "join":
        if not room_code:
            return RedirectResponse(url="/?error=missing_code", status_code=303)

        room_code = room_code.upper().strip()
        print(f"[DEBUG] Attempting to join Room: {room_code}")

        if room_code not in rooms:
            print(f"[DEBUG] Join Failed: Room {room_code} not found")
            return RedirectResponse(url=f"/?error=not_found&code={room_code}", status_code=303)

        room = rooms[room_code]

        # For private rooms, check max player limit
        if room.room_type == "private" and room.is_full():
            print(f"[DEBUG] Join Failed: Room {room_code} is full")
            return RedirectResponse(url=f"/?error=full&code={room_code}", status_code=303)

        name = get_unique_name(name, room.players)
        room.add_player(name)
        print(f"[DEBUG] User {name} joined Room {room_code}. Total players: {len(room.players)}")

        await broadcast_lobby_update()

    response = RedirectResponse(url="/game", status_code=303)
    response.set_cookie("username", name)
    response.set_cookie("room_id", room_code)
    return response

@app.get("/leave")
async def leave(username: str = Cookie(None), room_id: str = Cookie(None)):
    if room_id in rooms and username:
        room = rooms[room_id]
        manager = room.manager
        await manager.handle_voluntary_leave(username)
        
        if not manager.active_connections:
            del rooms[room_id]
            if room_id in public_rooms:
                del public_rooms[room_id]

    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("room_id")
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
    def __init__(self, duration_mins=5): 
        self.active_connections: Dict[str, WebSocket] = {}
        self.ws_to_name: Dict[int, str] = {}
        self.draw_history: List[dict] = []
        self.round_duration = duration_mins * 60
        self.movie_history: List[str] = []
        
        self.round_timer_task = None
        self.selection_timer_task = None

        self.game_state = {
            "movie": "",
            "display_name": "",
            "drawer_assigned": False,
            "drawer_name": None,
            "is_round_active": False,
            "is_selecting": False,
            "selection_active": False,
            "selection_end_time": None,
            "winner_announcement": None,
            "revealed_movie": None,
            "show_vowels": True   
        }

    def get_player_score(self, name: str):
        score = r.get(f"score:{name}")
        return int(score) if score else 0
    def get_round(self):
        round_no = r.get(f"round:{id(self)}")
        return int(round_no) if round_no is not None else 0

    def increment_round(self):
        current = self.get_round()
        r.set(f"round:{id(self)}", current + 1)

    def reset_round(self):
        r.set(f"round:{id(self)}", 0)

    def set_player_score(self, name: str, points: int):
        current_score = self.get_player_score(name)
        new_score = current_score + points
        r.set(f"score:{name}", new_score)

    def get_remaining_time(self):
        """Calculates remaining seconds based on the end_time stored in Redis"""
        end_time = r.get(f"round_end_time:{id(self)}") or r.get("round_end_time")
        if end_time:
            remaining = int(float(end_time) - time.time())
            return max(0, remaining)
        return 0

    def get_selection_time_left(self):
        selection_end = r.get(f"selection_end_time:{id(self)}")
        if selection_end:
            remaining = int(float(selection_end) - time.time())
            return max(0, remaining)
        return 0

    def cancel_selection_timer(self):
        if self.selection_timer_task:
            self.selection_timer_task.cancel()
            self.selection_timer_task = None
        self.game_state["selection_active"] = False
        self.game_state["selection_end_time"] = None
        r.delete(f"selection_end_time:{id(self)}")
        r.delete(f"selection_drawer:{id(self)}")

    async def handle_selection_expiry(self):
        
        if self.game_state.get("movie"):
            return

        old_drawer = self.game_state.get("drawer_name")

        player_names = list(self.active_connections.keys())
        if not player_names:
            return

        
        if len(player_names) > 1 and old_drawer in player_names:
            idx = player_names.index(old_drawer)
            new_drawer = player_names[(idx + 1) % len(player_names)]
        else:
            new_drawer = random.choice(player_names)

        
        self.game_state.update({
            "drawer_name": new_drawer,
            "drawer_assigned": True,
            "movie": "",
            "display_name": "",
            "is_round_active": False
        })

        
        await self.broadcast({
            "type": "new_drawer",
            "drawer_name": new_drawer,
            "message": f"⏱️ Time's up for {old_drawer}. New drawer: {new_drawer}."
        })

        await self.broadcast({
            "type": "player_list",
            "players": self.get_player_data()
        })

        
        await self.start_selection_timer()

    async def start_selection_timer(self):
        if self.selection_timer_task:
            self.selection_timer_task.cancel()

        self.game_state["selection_active"] = True
        end_timestamp = time.time() + 60
        self.game_state["selection_end_time"] = end_timestamp
        r.set(f"selection_end_time:{id(self)}", end_timestamp)
        r.set(f"selection_drawer:{id(self)}", self.game_state.get("drawer_name", ""))

        
        async def selection_timer():
            try:
                while True:
                    remaining = self.get_selection_time_left()
                    
                    await self.broadcast({
                        "type": "timer_update",
                        "timer_type": "selection",
                        "time_left": remaining,
                        "drawer_name": self.game_state.get("drawer_name")
                    })

                    if remaining <= 0:
                        break
                    await asyncio.sleep(1)

                
                self.game_state["selection_end_time"] = None
                r.delete(f"selection_end_time:{id(self)}")
                r.delete(f"selection_drawer:{id(self)}")

                await self.handle_selection_expiry()
            except asyncio.CancelledError:
                
                pass

        self.selection_timer_task = asyncio.create_task(selection_timer())

    def get_player_data(self):
        players = [{"name": name, "score": self.get_player_score(name)} 
                   for name in self.active_connections.keys()]
        return sorted(players, key=lambda x: x['score'], reverse=True)

    async def connect(self, websocket: WebSocket, name: str):
        original_name = name
        name = get_unique_name(name, self.active_connections.keys())

        await websocket.accept()
        ws_id = id(websocket)
        self.active_connections[name] = websocket
        self.ws_to_name[ws_id] = name
        print(f"[DEBUG-BACKEND] Player {name} connected. Total players: {len(self.active_connections)}")

        if name != original_name:
            await websocket.send_json({
                "type": "name_updated",
                "new_name": name
            })
        
        if r.get(f"score:{name}") is None:
            r.set(f"score:{name}", 0)
        
        # Only assign drawer if game has already started
        role = "guesser"
        if self.game_state["drawer_assigned"] and name == self.game_state["drawer_name"]:
            role = "drawer"
            print(f"[DEBUG-BACKEND] {name} assigned drawer role (already assigned)")
        else:
            print(f"[DEBUG-BACKEND] {name} assigned guesser role (game in lobby or not their turn)")

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
                
                return False

    async def start_round_timer(self, duration=None):
        if duration is None:
            duration = self.round_duration

        
        self.cancel_selection_timer()
        
        if self.round_timer_task:
            self.round_timer_task.cancel()
        
        end_timestamp = time.time() + duration

        r.set(f"round_end_time:{id(self)}", end_timestamp)
        r.set("round_end_time", end_timestamp)
        
        self.game_state["is_round_active"] = True

        

        async def timer():
            try:
                while True:
                    remaining = self.get_remaining_time()
                    await self.broadcast({
                        "type": "timer_update",
                        "timer_type": "round",
                        "time_left": remaining,
                        "drawer_name": self.game_state.get("drawer_name")
                    })

                    if remaining <= 0:
                        break
                    await asyncio.sleep(1)

                if self.game_state["is_round_active"]:
                    self.game_state["is_round_active"] = False
                    self.game_state["winner_announcement"] = "⏰ Time's up!"
                    self.game_state["revealed_movie"] = self.game_state["movie"]
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
        """Start a new round. Handles both initial game start (from lobby) and between-round transitions."""
        print(f"[DEBUG-BACKEND] restart_game() called. drawer_assigned={self.game_state['drawer_assigned']}, active_connections={len(self.active_connections)}")
        
        if self.game_state.get("movie"):
            self.movie_history.append(self.game_state["movie"])
        await self.broadcast({
            "type": "history_update",
            "history": self.movie_history
        })

        self.increment_round() 
        new_round = self.get_round()
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
            print(f"[DEBUG-BACKEND] No active connections, returning")
            return
        
        # Pick drawer: first game or normal rotation
        old_drawer_name = self.game_state.get("drawer_name")
        names = list(self.active_connections.keys())
        
        if not old_drawer_name or not self.game_state["drawer_assigned"]:
            # Initial game - pick random from all players
            new_drawer_name = random.choice(names)
            print(f"[DEBUG-BACKEND] Initial game start - selecting drawer {new_drawer_name} from {names}")
        else:
            # Rotate drawer - exclude current drawer if possible
            if len(names) > 1 and old_drawer_name in names:
                names.remove(old_drawer_name)
            new_drawer_name = random.choice(names)
            print(f"[DEBUG-BACKEND] Rotating drawer from {old_drawer_name} to {new_drawer_name}")
        
        self.game_state["drawer_name"] = new_drawer_name
        self.game_state["drawer_assigned"] = True
        self.game_state["is_selecting"] = True

        await self.start_selection_timer()

        for name, ws in self.active_connections.items():
            role = "drawer" if name == new_drawer_name else "guesser"
            print(f"[DEBUG-BACKEND] Sending init to {name} with role={role}")
            await ws.send_json({
                "type": "init",
                "role": role,
                "round_number": new_round, 
                "movie_set": False,
                "drawer_name": new_drawer_name,
                "selection_active": True,
                "selection_time_left": self.get_selection_time_left()
            })

    async def broadcast(self, message: dict):
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_json(message)
            except:
                continue

    async def handle_voluntary_leave(self, name: str):
        if name in self.active_connections:
            ws = self.active_connections.pop(name)
            if id(ws) in self.ws_to_name:
                del self.ws_to_name[id(ws)]
            
            if name == self.game_state["drawer_name"]:
                self.cancel_selection_timer()

                await self.broadcast({
                    "type": "drawer_disconnected", 
                    "name": name
                })
                
                if self.active_connections:
                    await self.restart_game()
                else:
                    self.game_state["drawer_assigned"] = False
                    self.game_state["drawer_name"] = None
            
            await self.broadcast({"type": "player_list", "players": self.get_player_data()})
manager = ConnectionManager()

def process_movie(movie: str, show_vowels: bool = True):
    if show_vowels:
        vowels = "AEIOUaeiou "
        return "".join([char if (char in vowels or not char.isalnum()) else "_" for char in movie])
    else:
        return "".join(["_" if char.isalnum() else char for char in movie])

rooms: Dict[str, GameRoom] = {}

def get_unique_name(name, existing_names):
    if name not in existing_names:
        return name
    
    count = 1
    while f"{name}({count})" in existing_names:
        count += 1
    
    return f"{name}({count})"

@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("front_page.html", {"request": request})

@app.get("/game")
async def get_game(request: Request, room_id: str = Cookie(None), username: str = Cookie(None)):
    
    if not room_id:
        return RedirectResponse(url="/", status_code=303)
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "room_code": room_id,
        "username": username or "Guest"
    })


async def broadcast_lobby_update():
    """Sends current public rooms to all users in the lobby. Only shows rooms in WAITING state."""
    public_list = [
        {
            "room_id": r.room_id,
            "host": r.host,
            "count": len(r.players),
            "max": r.max_players,
            "status": r.status,
        }
        for r in rooms.values() if r.room_type == "public" and r.status == "LOBBY"
    ]
    for ws in lobby_connections:
        try:
            await ws.send_json({"type": "lobby_update", "rooms": public_list})
        except Exception as e:
            print(f"[DEBUG] Error sending lobby update: {e}")
            continue

@app.websocket("/ws/lobby")
async def lobby_endpoint(websocket: WebSocket):
    await websocket.accept()
    lobby_connections.append(websocket)
    await broadcast_lobby_update() # Send initial list
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except:
        lobby_connections.remove(websocket)

async def send_lobby_data(ws):
    rooms_data = []

    for room in public_rooms.values():
        rooms_data.append({
            "room_id": room.room_id,
            "players": len(room.players),
            "max_players": room.max_players
        })

    await ws.send_json({
        "type": "lobby_list",
        "rooms": rooms_data
    })
async def broadcast_lobby():
    for ws in lobby_connections:
        try:
            await send_lobby_data(ws)
        except:
            continue

@app.websocket("/ws") 
async def websocket_endpoint(websocket: WebSocket, username: str = Cookie(None), room_id: str = Cookie(None)):
    room = rooms.get(room_id)

    if not username or not room_id or room_id not in rooms:
        print(f"[DEBUG] WS Connection Denied: Missing credentials or room {room_id} exists: {room_id in rooms}")
        await websocket.close()
        return

    room = rooms[room_id]
    manager = room.manager
    print(f"[DEBUG] WS Connecting: {username} to Room {room_id}")

    role = await manager.connect(websocket, username)
    print(f"[DEBUG-BACKEND] {username} connected to room {room_id}, role={role}, room_type={room.room_type}")
    
    if room.room_type == "public" and room.is_full() and not room.game_started:
        print(f"[DEBUG-BACKEND] Room {room_id} is now full ({len(room.players)}/{room.max_players}). Auto-starting public game")
        room.game_started = True
        room.status = "PLAYING"
        await room.manager.restart_game()
        await broadcast_lobby_update()

    if room.should_start_game() and not room.game_started:
        print(f"[DEBUG-BACKEND] Room {room_id} conditions met for auto-start")
        room.game_started = True
        room.status = "PLAYING"
        await manager.restart_game()
    
    print(f"[DEBUG-BACKEND] Broadcasting lobby update")
    await broadcast_lobby_update()

    if role is None:
        return  
    name = username
    current_time_left = manager.get_remaining_time()
    current_round = manager.get_round()

    await websocket.send_json({
        "type": "init", 
        "role": role, 
        "round_number": current_round,
        "room_status": room.status,
        "host_name": room.host,
        "room_type": room.room_type,
        "player_count": len(room.players),
        "max_players": room.max_players,
        "movie_set": bool(manager.game_state["movie"]),
        "display": manager.game_state["display_name"], 
        "full_movie": manager.game_state["movie"],
        "drawer_name": manager.game_state["drawer_name"], 
        "selection_active": manager.game_state.get("selection_active", False),
        "selection_time_left": manager.get_selection_time_left(),
        "history": manager.draw_history,
        "winner_msg": manager.game_state["winner_announcement"], 
        "revealed": manager.game_state["revealed_movie"],
        "time_left": current_time_left, 
        "history_movies": manager.movie_history
    })
    
    try:
        while True:
            data = await websocket.receive_json()
            if data["type"] == "start_game":
                print(f"[DEBUG-BACKEND] start_game event from {username} in room {room_id}. Is host? {username == room.host}")
                if username == room.host:
                    if len(room.players) >= 2:
                        print(f"[DEBUG-BACKEND] Host {username} starting room {room_id} with {len(room.players)} players")
                        room.status = "PLAYING"
                        room.game_started = True
                        await manager.restart_game() 
                        await broadcast_lobby_update()
                    else:
                        print(f"[DEBUG-BACKEND] Host tried to start with only {len(room.players)} players (need 2+)")
                        await websocket.send_json({
                            "type": "error",
                            "message": "At least 2 players are required to start the game."
                        })
                else:
                    print(f"[DEBUG-BACKEND] Non-host {username} tried to start game (host is {room.host})")
            if data["type"] not in ["drawing"]: 
                print(f"[DEBUG] WS Message from {username} in {room_id}: {data['type']}")
            if data["type"] == "set_movie":
                manager.cancel_selection_timer()
                manager.game_state["movie"] = data["movie"].upper()
                manager.game_state["show_vowels"] = data.get("show_vowels", True)

                manager.game_state["display_name"] = process_movie(
                    manager.game_state["movie"],
                    manager.game_state["show_vowels"]
                )

                await manager.start_round_timer(duration=manager.round_duration)

                await manager.broadcast({
                    "type": "movie_selected",
                    "drawer_name": manager.game_state["drawer_name"],
                    "full_movie": manager.game_state["movie"]
                })

                await manager.broadcast({
                    "type": "game_start", 
                    "display": manager.game_state["display_name"],
                    "full_movie": manager.game_state["movie"], 
                    "drawer_name": manager.game_state["drawer_name"],
                    "time_left": manager.round_duration 
                })
            elif data["type"] == "won" and manager.game_state["is_round_active"]:
                manager.game_state["is_round_active"] = False

                if manager.round_timer_task:
                    manager.round_timer_task.cancel()
                    manager.round_timer_task = None

                manager.set_player_score(username, 50) 
                if manager.game_state["drawer_name"]:
                    manager.set_player_score(manager.game_state["drawer_name"], 25)

                manager.game_state["winner_announcement"] = f"🎉 {username} guessed it first!"
                manager.game_state["revealed_movie"] = manager.game_state["movie"]

                await manager.broadcast({"type": "player_list", "players": manager.get_player_data()})
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
                    manager.cancel_selection_timer()
                    movie = data["movie"]
                    manager.game_state["movie"] = movie
                    manager.game_state["show_vowels"] = data.get("show_vowels", True)

                    manager.game_state["display_name"] = process_movie(
                        movie,
                        manager.game_state["show_vowels"]
                    )

                    await manager.start_round_timer(duration=manager.round_duration)

                    await manager.broadcast({
                        "type": "movie_selected",
                        "drawer_name": manager.game_state["drawer_name"],
                        "full_movie": manager.game_state["movie"]
                    })

                    await manager.broadcast({
                        "type": "game_start",
                        "display": manager.game_state["display_name"],
                        "full_movie": manager.game_state["movie"],
                        "drawer_name": manager.game_state["drawer_name"],
                        "time_left": manager.round_duration 
                    })
    except WebSocketDisconnect:
        print(f"[DEBUG-HOST] {username} disconnected from room {room_id}")

        # Remove websocket connection
        await manager.disconnect(websocket)

        # Remove player from room player list
        if username in room.players:
            room.players.remove(username)
            print(f"[DEBUG-HOST] Removed {username} from room.players")

        print(f"[DEBUG-HOST] Remaining players: {room.players}")
        print(f"[DEBUG-HOST] Current host before check: {room.host}")
        print(f"[DEBUG-HOST] Game started: {room.game_started}")

        # ==========================================
        # HOST TRANSFER LOGIC
        # ==========================================
        if username == room.host and not room.game_started:
            print(f"[DEBUG-HOST] Host left before game started")

            # Transfer host if players remain
            if room.players:
                new_host = room.players[0]
                room.host = new_host

                print(f"[DEBUG-HOST] New host assigned: {new_host}")

                # Broadcast new host to everyone
                await manager.broadcast({
                    "type": "host_transferred",
                    "new_host": new_host
                })

            else:
                print(f"[DEBUG-HOST] No players left. Deleting room {room_id}")

                # Delete room completely
                if room_id in rooms:
                    del rooms[room_id]

                if room_id in public_rooms:
                    del public_rooms[room_id]

        # ==========================================
        # DELETE EMPTY ROOM
        # ==========================================
        if len(room.players) == 0:
            print(f"[DEBUG-HOST] Room empty. Cleaning room {room_id}")

            if room_id in rooms:
                del rooms[room_id]

            if room_id in public_rooms:
                del public_rooms[room_id]

        # Update public lobby instantly
        await broadcast_lobby_update()

        # Send updated player list
        await manager.broadcast({
            "type": "player_list",
            "players": manager.get_player_data()
        })

        print(f"[DEBUG-HOST] Disconnect handling completed")
        