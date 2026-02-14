from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class SpaceCreate(BaseModel):
    """Model for creating a new code space"""
    language: str = "python"
    initial_code: str = ""


class SpaceResponse(BaseModel):
    """Model for space API responses"""
    space_id: str
    language: str
    code: str
    created_at: datetime
    invite_link: str


class CodeUpdate(BaseModel):
    """WebSocket message for code synchronization"""
    type: str  # "code_change", "cursor_move", "user_join", "user_leave"
    space_id: str
    user_id: Optional[str] = None
    code: Optional[str] = None
    cursor_position: Optional[dict] = None
    language: Optional[str] = None


class UserPresence(BaseModel):
    """Model for tracking online users and cursors"""
    user_id: str
    username: str = "Anonymous"
    cursor_line: int = 0
    cursor_column: int = 0
    color: str = "#007acc"  # Default VS Code blue


class SpaceDocument(BaseModel):
    """MongoDB document model for spaces"""
    space_id: str
    code: str
    language: str
    created_at: datetime
    last_updated: datetime
    active_users: List[str] = Field(default_factory=list)
