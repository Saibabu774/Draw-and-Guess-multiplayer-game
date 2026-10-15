from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from pydantic_settings import BaseSettings
from datetime import datetime, timezone
from typing import Optional
import random, secrets, string, json, asyncio, os, re

class Settings(BaseSettings):
    app_name: str = "Drawing Guessing Game"
    database_url: str = "sqlite:///./drawing_game.db"
    cors_origins: str = "http://localhost:5173"
    round_seconds: int = 60
    min_players: int = 2
    total_rounds: int = 3
    class Config:
        env_file = ".env"
settings = Settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase): pass
class GameRoom(Base):
    __tablename__ = "game_rooms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="lobby")
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    total_rounds: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("game_rooms.id"))
    display_name: Mapped[str] = mapped_column(String(40))
    score: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

class GameRound(Base):
    __tablename__ = "game_rounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("game_rooms.id"))
    round_number: Mapped[int] = mapped_column(Integer)
    drawer_player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    word: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

class Guess(Base):
    __tablename__ = "guesses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("game_rounds.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    guess_text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(default=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Draw & Guess API", version="1.0.0", description="Real-time multiplayer drawing and guessing game API")
origins = [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

WORDS = ["apple", "bicycle", "castle", "elephant", "guitar", "rainbow", "rocket", "sunflower", "turtle", "watermelon"]
rooms = {}  # In-memory active game state; persistence is in SQL for room/player/round/guess records.
connections = {} # room_code -> {player_id: WebSocket}
locks = {}
def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()

def public_state(code: str):
    state = rooms.get(code)
    if not state: return None
    return {
        "room_code": code, "status": state["status"], "current_round": state["current_round"],
        "total_rounds": state["total_rounds"], "drawer_id": state.get("drawer_id"),
        "round_seconds": settings.round_seconds,
        "players": [{"id": p["id"], "display_name": p["display_name"], "score": p["score"],
                     "ready": p["ready"], "connected": p["id"] in connections.get(code, {})}
                    for p in state["players"].values()],
        "time_left": max(0, int(state.get("deadline", 0) - asyncio.get_event_loop().time())) if state.get("deadline") else 0,
        "word_hint": (" ".join("_" for _ in state["word"]) if state.get("word") else None)
    }

async def broadcast(code: str, event: str, data: dict):
    payload = {"event": event, "data": data}
    dead = []
    for pid, ws in list(connections.get(code, {}).items()):
        try: await ws.send_json(payload)
        except Exception: dead.append(pid)
    for pid in dead:
        connections.get(code, {}).pop(pid, None)

async def send_player(code: str, pid: int, event: str, data: dict):
    ws = connections.get(code, {}).get(pid)
    if ws:
        await ws.send_json({"event": event, "data": data})

def room_summary(code: str):
    state = rooms.get(code)
    if not state: return None
    return public_state(code)

def make_code():
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))

class CreateRoom(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    total_rounds: int = Field(default=3, ge=1, le=10)
class JoinRoom(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)

@app.get("/")
def root():
    return {"message": "Drawing Guessing Game API is running", "docs": "/docs", "websocket": "/ws/{room_code}/{player_id}"}

@app.post("/api/rooms", tags=["rooms"])
def create_room(body: CreateRoom):
    dbs = SessionLocal()
    try:
        code = make_code()
        while dbs.query(GameRoom).filter_by(room_code=code).first(): code = make_code()
        room = GameRoom(room_code=code, total_rounds=body.total_rounds)
        dbs.add(room); dbs.commit(); dbs.refresh(room)
        player = Player(room_id=room.id, display_name=body.display_name.strip())
        dbs.add(player); dbs.commit(); dbs.refresh(player)
        rooms[code] = {"db_room_id": room.id, "status":"lobby", "current_round":0, "total_rounds":body.total_rounds,
                       "players": {player.id: {"id":player.id,"display_name":player.display_name,"score":0,"ready":False}},
                       "word":None,"drawer_id":None,"guessed":set(),"round_db_id":None,"deadline":0,"drawer_order":[]}
        connections[code] = {}; locks[code] = asyncio.Lock()
        return {"room_code": code, "player_id": player.id, "room": public_state(code)}
    finally: dbs.close()

@app.post("/api/rooms/{room_code}/join", tags=["rooms"])
def join_room(room_code: str, body: JoinRoom):
    code = room_code.upper()
    state = rooms.get(code)
    if not state or state["status"] != "lobby":
        raise HTTPException(404, "Room not found or game has already started")
    name = body.display_name.strip()
    if not name: raise HTTPException(422, "Display name is required")
    if any(p["display_name"].casefold() == name.casefold() for p in state["players"].values()):
        raise HTTPException(409, "Display name is already taken in this room")
    dbs = SessionLocal()
    try:
        player = Player(room_id=state["db_room_id"], display_name=name)
        dbs.add(player); dbs.commit(); dbs.refresh(player)
        state["players"][player.id] = {"id":player.id,"display_name":name,"score":0,"ready":False}
        return {"room_code": code, "player_id": player.id, "room": public_state(code)}
    finally: dbs.close()

@app.get("/api/rooms/{room_code}", tags=["rooms"])
def get_room(room_code: str):
    state = rooms.get(room_code.upper())
    if not state: raise HTTPException(404, "Room not found (active rooms are held in memory)")
    return public_state(room_code.upper())

@app.get("/api/leaderboard/{room_code}", tags=["history & stats"])
def leaderboard(room_code: str):
    state = rooms.get(room_code.upper())
    if not state: raise HTTPException(404, "Room not found")
    return sorted([{"player_id":p["id"],"display_name":p["display_name"],"score":p["score"]} for p in state["players"].values()],
                  key=lambda x: x["score"], reverse=True)

async def start_round(code: str):
    state = rooms[code]
    active = list(state["players"].values())
    if len(active) < settings.min_players:
        state["status"] = "lobby"
        await broadcast(code, "error", {"message": f"At least {settings.min_players} players are required"})
        return
    state["current_round"] += 1
    if state["current_round"] > state["total_rounds"]:
        state["status"] = "completed"
        dbs = SessionLocal()
        try:
            room = dbs.query(GameRoom).filter_by(id=state["db_room_id"]).first()
            room.status="completed"; room.ended_at=datetime.now(timezone.utc); dbs.commit()
        finally: dbs.close()
        await broadcast(code, "game_completed", {"leaderboard": leaderboard(code)})
        return
    state["status"] = "playing"
    state["drawer_order"] = sorted(state["players"].keys())
    drawer_id = state["drawer_order"][(state["current_round"]-1) % len(state["drawer_order"])]
    state["drawer_id"] = drawer_id
    state["word"] = random.choice(WORDS)
    state["guessed"] = set()
    state["deadline"] = asyncio.get_event_loop().time() + settings.round_seconds
    dbs = SessionLocal()
    try:
        room = dbs.query(GameRoom).filter_by(id=state["db_room_id"]).first()
        room.status="playing"; room.current_round=state["current_round"]
        if not room.started_at: room.started_at=datetime.now(timezone.utc)
        rnd = GameRound(room_id=state["db_room_id"], round_number=state["current_round"], drawer_player_id=drawer_id, word=state["word"])
        dbs.add(rnd); dbs.commit(); dbs.refresh(rnd); state["round_db_id"] = rnd.id
    finally: dbs.close()
    await broadcast(code, "round_started", {"round_number":state["current_round"],"total_rounds":state["total_rounds"],
                         "drawer_id":drawer_id,"drawer_name":state["players"][drawer_id]["display_name"],
                         "word_hint":" ".join("_" for _ in state["word"]), "seconds":settings.round_seconds})
    await send_player(code, drawer_id, "secret_word", {"word":state["word"]})
    asyncio.create_task(round_timer(code, state["current_round"]))

async def round_timer(code: str, round_number: int):
    while code in rooms:
        state = rooms[code]
        if state["status"] != "playing" or state["current_round"] != round_number: return
        remaining = max(0, int(state["deadline"] - asyncio.get_event_loop().time()))
        await broadcast(code, "timer_updated", {"seconds":remaining})
        if remaining <= 0:
            await end_round(code, "time_up")
            return
        await asyncio.sleep(1)

async def end_round(code: str, reason: str):
    state = rooms.get(code)
    if not state or state["status"] != "playing": return
    state["status"] = "round_result"
    dbs = SessionLocal()
    try:
        rnd = dbs.query(GameRound).filter_by(id=state["round_db_id"]).first()
        if rnd: rnd.ended_at=datetime.now(timezone.utc)
        dbs.commit()
    finally: dbs.close()
    await broadcast(code, "round_ended", {"word":state["word"],"reason":reason,
                       "scores":[{"player_id":p["id"],"display_name":p["display_name"],"score":p["score"]} for p in state["players"].values()]})
    await asyncio.sleep(5)
    if code in rooms and rooms[code]["status"] == "round_result":
        await start_round(code)

@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok", "service": settings.app_name}

@app.get("/api/protocol", tags=["meta"])
def protocol():
    return {"transport": "WebSocket", "url_pattern": "/ws/{room_code}/{player_id}",
            "envelope": {"event": "event_name", "data": {}},
            "client_events": ["ready", "start_game", "draw_stroke", "clear_canvas", "guess", "ping"],
            "server_events": ["lobby_updated", "round_started", "secret_word", "draw_stroke", "canvas_cleared", "guess_message", "correct_guess", "score_updated", "timer_updated", "round_ended", "player_disconnected", "game_completed"]}

@app.get("/api/games/history", tags=["history & stats"])
def games_history():
    dbs = SessionLocal()
    try:
        rows = dbs.query(GameRoom).filter(GameRoom.status == "completed").order_by(GameRoom.ended_at.desc()).limit(100).all()
        return [{"room_code": r.room_code, "status": r.status, "current_round": r.current_round, "total_rounds": r.total_rounds, "created_at": r.created_at, "started_at": r.started_at, "ended_at": r.ended_at} for r in rows]
    finally: dbs.close()

@app.get("/api/games/{room_code}", tags=["history & stats"])
def game_details(room_code: str):
    dbs = SessionLocal()
    try:
        room = dbs.query(GameRoom).filter_by(room_code=room_code.upper()).first()
        if not room: raise HTTPException(404, "Room not found")
        rounds = dbs.query(GameRound).filter_by(room_id=room.id).order_by(GameRound.round_number).all()
        players = dbs.query(Player).filter_by(room_id=room.id).all()
        return {"room_code": room.room_code, "status": room.status, "current_round": room.current_round, "total_rounds": room.total_rounds,
                "players": [{"id": p.id, "display_name": p.display_name, "score": p.score} for p in players],
                "rounds": [{"round_number": r.round_number, "drawer_player_id": r.drawer_player_id,
                            "word": r.word if r.ended_at is not None or room.status == "completed" else None,
                            "started_at": r.started_at, "ended_at": r.ended_at} for r in rounds]}
    finally: dbs.close()

@app.get("/api/leaderboard", tags=["history & stats"])
def global_leaderboard():
    dbs = SessionLocal()
    try:
        players = dbs.query(Player).order_by(Player.score.desc()).limit(100).all()
        return [{"player_id": p.id, "display_name": p.display_name, "score": p.score} for p in players]
    finally: dbs.close()

@app.get("/api/players/{display_name}/stats", tags=["history & stats"])
def player_stats(display_name: str):
    dbs = SessionLocal()
    try:
        players = dbs.query(Player).filter(Player.display_name == display_name).all()
        if not players: raise HTTPException(404, "Player not found")
        return {"display_name": display_name, "games_joined": len(players), "total_score": sum(p.score for p in players),
                "rooms": [{"room_id": p.room_id, "score": p.score, "joined_at": p.joined_at, "left_at": p.left_at} for p in players]}
    finally: dbs.close()

@app.websocket("/ws/{room_code}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, player_id: int):
    code = room_code.upper()
    state = rooms.get(code)
    if not state or player_id not in state["players"]:
        await websocket.close(code=1008, reason="Unknown room or player")
        return
    await websocket.accept()
    connections.setdefault(code, {})[player_id] = websocket
    await broadcast(code, "lobby_updated", public_state(code))
    await broadcast(code, "player_connected", {"player_id":player_id,"display_name":state["players"][player_id]["display_name"]})
    try:
        while True:
            msg = await websocket.receive_json()
            event = msg.get("event")
            data = msg.get("data") or {}
            async with locks.setdefault(code, asyncio.Lock()):
                if event == "ready":
                    state["players"][player_id]["ready"] = bool(data.get("ready", True))
                    await broadcast(code, "lobby_updated", public_state(code))
                elif event == "start_game":
                    if state["status"] != "lobby":
                        await send_player(code, player_id, "error", {"message":"Game has already started"})
                    elif len(state["players"]) < settings.min_players:
                        await send_player(code, player_id, "error", {"message":f"At least {settings.min_players} players are required"})
                    elif player_id != min(state["players"].keys()):
                        await send_player(code, player_id, "error", {"message":"Only the room creator can start the game"})
                    elif not all(p["ready"] for p in state["players"].values()):
                        await send_player(code, player_id, "error", {"message":"All players must be ready"})
                    else:
                        await start_round(code)
                elif event == "draw_stroke":
                    if state["status"] == "playing" and player_id == state["drawer_id"]:
                        stroke = data
                        if isinstance(stroke, dict) and all(k in stroke for k in ("x1","y1","x2","y2")):
                            await broadcast(code, "draw_stroke", {"player_id":player_id, **{k:stroke[k] for k in ("x1","y1","x2","y2")},
                                "color":str(stroke.get("color","#111827"))[:20],"width":max(1,min(30,int(stroke.get("width",4)))),"tool":stroke.get("tool","brush")})
                elif event == "clear_canvas":
                    if state["status"] == "playing" and player_id == state["drawer_id"]:
                        await broadcast(code, "canvas_cleared", {"player_id":player_id})
                elif event == "guess":
                    if state["status"] != "playing":
                        await send_player(code, player_id, "error", {"message":"No active round"})
                    elif player_id == state["drawer_id"]:
                        await send_player(code, player_id, "error", {"message":"The drawer cannot guess"})
                    elif player_id in state["guessed"]:
                        await send_player(code, player_id, "error", {"message":"You have already guessed correctly"})
                    else:
                        guess_text = str(data.get("text",""))[:120].strip()
                        correct = re.sub(r"\s+"," ",guess_text).casefold() == re.sub(r"\s+"," ",state["word"]).casefold()
                        dbs = SessionLocal()
                        try:
                            dbs.add(Guess(round_id=state["round_db_id"], player_id=player_id, guess_text=guess_text, is_correct=correct))
                            if correct:
                                elapsed = settings.round_seconds - max(0, int(state["deadline"] - asyncio.get_event_loop().time()))
                                points = max(50, 100 + (settings.round_seconds-elapsed))
                                state["players"][player_id]["score"] += points
                            dbs.commit()
                        finally: dbs.close()
                        if correct:
                            state["guessed"].add(player_id)
                            await broadcast(code, "correct_guess", {"player_id":player_id,"display_name":state["players"][player_id]["display_name"],
                                                   "points":points,"score":state["players"][player_id]["score"]})
                            await broadcast(code, "score_updated", {"players":[{"id":p["id"],"display_name":p["display_name"],"score":p["score"]} for p in state["players"].values()]})
                            if len(state["guessed"]) >= len(state["players"])-1:
                                await end_round(code, "all_guessed")
                        else:
                            await broadcast(code, "guess_message", {"player_id":player_id,"display_name":state["players"][player_id]["display_name"],"text":guess_text})
                elif event == "ping":
                    await send_player(code, player_id, "pong", {})
    except WebSocketDisconnect:
        if connections.get(code, {}).get(player_id) is websocket:
            connections[code].pop(player_id, None)
        await broadcast(code, "player_disconnected", {"player_id":player_id,"display_name":state["players"][player_id]["display_name"]})
        if state.get("status") == "playing" and player_id == state.get("drawer_id"):
            await end_round(code, "drawer_disconnected")
