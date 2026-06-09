from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), nullable=False, unique=True, index=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), onupdate=func.now())
    total_games_played = Column(Integer, default=0)
    total_wins = Column(Integer, default=0)
    total_score = Column(Integer, default=0)
    highest_score = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    hosted_rooms = relationship("Room", back_populates="host")
    room_participations = relationship("RoomPlayer", back_populates="player")

class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    room_code = Column(String(10), nullable=False, unique=True, index=True)
    room_type = Column(String(20), default="private")
    status = Column(String(30), default="LOBBY")
    host_player_id = Column(Integer, ForeignKey("players.id"))
    max_players = Column(Integer, default=6)
    current_players = Column(Integer, default=0)
    total_rounds = Column(Integer, default=3)
    current_round_number = Column(Integer, default=0)
    current_drawer_index = Column(Integer, default=0)
    game_started = Column(Boolean, default=False)
    
    last_activity_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    host = relationship("Player", back_populates="hosted_rooms", foreign_keys=[host_player_id])
    players = relationship("RoomPlayer", back_populates="room")
    rounds = relationship("GameRound", back_populates="room")

class RoomPlayer(Base):
    __tablename__ = "room_players"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    player_id = Column(Integer, ForeignKey("players.id"))
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    left_at = Column(DateTime(timezone=True), nullable=True)
    final_score = Column(Integer, default=0)
    was_host = Column(Boolean, default=False)
    was_kicked = Column(Boolean, default=False)
    is_online = Column(Boolean, default=True)

    # Relationships
    room = relationship("Room", back_populates="players")
    player = relationship("Player", back_populates="room_participations")

class Word(Base):
    __tablename__ = "words"

    id = Column(Integer, primary_key=True, index=True)
    word = Column(String(255), nullable=False)
    category = Column(String(100))
    difficulty = Column(String(20))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class GameRound(Base):
    __tablename__ = "game_rounds"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    round_number = Column(Integer)
    drawer_player_id = Column(Integer, ForeignKey("players.id"))
    word_id = Column(Integer, ForeignKey("words.id"), nullable=True)
    winner_player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    round_duration_seconds = Column(Integer, default=300)

    # Relationships
    room = relationship("Room", back_populates="rounds")
    word = relationship("Word")
    drawer = relationship("Player", foreign_keys=[drawer_player_id])
    winner = relationship("Player", foreign_keys=[winner_player_id])

class Score(Base):
    __tablename__ = "scores"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    round_id = Column(Integer, ForeignKey("game_rounds.id"))
    player_id = Column(Integer, ForeignKey("players.id"))
    points_earned = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class HostTransfer(Base):
    __tablename__ = "host_transfers"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    old_host_id = Column(Integer, ForeignKey("players.id"))
    new_host_id = Column(Integer, ForeignKey("players.id"))
    transferred_at = Column(DateTime(timezone=True), server_default=func.now())

class VoteKick(Base):
    __tablename__ = "vote_kicks"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    target_player_id = Column(Integer, ForeignKey("players.id"))
    initiated_by = Column(Integer, ForeignKey("players.id"))
    status = Column(String(20)) # e.g., 'PASSED', 'FAILED', 'CANCELLED'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class VoteKickVote(Base):
    __tablename__ = "vote_kick_votes"

    id = Column(Integer, primary_key=True, index=True)
    vote_kick_id = Column(Integer, ForeignKey("vote_kicks.id"))
    player_id = Column(Integer, ForeignKey("players.id"))
    vote = Column(Boolean) # True for Yes, False for No
    voted_at = Column(DateTime(timezone=True), server_default=func.now())

class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    role_name = Column(String(50), nullable=False, unique=True)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PlayerRole(Base):
    __tablename__ = "player_roles"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    role_id = Column(Integer, ForeignKey("roles.id"))
    assigned_at = Column(DateTime(timezone=True), server_default=func.now())

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    player_id = Column(Integer, ForeignKey("players.id"))
    message = Column(Text)
    message_type = Column(String(30)) # e.g., 'CHAT', 'SYSTEM'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class RoomEvent(Base):
    __tablename__ = "room_events"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"))
    event_type = Column(String(50))
    event_data = Column(Text) # JSON string to store arbitrary event data
    created_at = Column(DateTime(timezone=True), server_default=func.now())