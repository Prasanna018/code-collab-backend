from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import uuid
import os
import time
from datetime import datetime
from typing import Dict, Set
import json
from starlette.middleware.gzip import GZipMiddleware

from database import (
    connect_to_mongodb,
    close_mongodb_connection,
    create_space,
    get_space,
    update_space_code,
    update_space_language,
    delete_space,
    cleanup_expired_spaces,
    add_user_to_space,
    remove_user_from_space
)
from models import SpaceCreate, SpaceResponse, CodeUpdate

# Environment variables
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", "1"))

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.user_ids: Dict[WebSocket, str] = {}
        self.last_db_write: Dict[str, float] = {}

    async def connect(self, websocket: WebSocket, space_id: str, user_id: str):
        await websocket.accept()
        if space_id not in self.active_connections:
            self.active_connections[space_id] = set()
        self.active_connections[space_id].add(websocket)
        self.user_ids[websocket] = user_id
        await add_user_to_space(space_id, user_id)
        print(f"✅ User {user_id} connected to space {space_id}")

    async def disconnect(self, websocket: WebSocket, space_id: str):
        if space_id in self.active_connections:
            self.active_connections[space_id].discard(websocket)
            if not self.active_connections[space_id]:
                del self.active_connections[space_id]
        
        user_id = self.user_ids.pop(websocket, None)
        if user_id:
            await remove_user_from_space(space_id, user_id)
            print(f"❌ User {user_id} disconnected from space {space_id}")

    async def broadcast(self, space_id: str, message: dict, exclude: WebSocket = None):
        """Send message to all connections in a space except the sender"""
        if space_id not in self.active_connections:
            return
        
        message_json = json.dumps(message)
        dead_connections = set()
        
        for connection in self.active_connections[space_id]:
            if connection == exclude:
                continue
            try:
                await connection.send_text(message_json)
            except Exception as e:
                print(f"Error broadcasting to connection: {e}")
                dead_connections.add(connection)
        
        # Clean up dead connections
        for dead_conn in dead_connections:
            await self.disconnect(dead_conn, space_id)

    def get_active_users(self, space_id: str) -> int:
        """Get count of active users in a space"""
        if space_id not in self.active_connections:
            return 0
        return len(self.active_connections[space_id])


manager = ConnectionManager()


# Background task for cleanup
async def periodic_cleanup():
    """Background task to clean up expired spaces"""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)  # Convert hours to seconds
        await cleanup_expired_spaces()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events for startup and shutdown"""
    # Startup
    await connect_to_mongodb()
    cleanup_task = asyncio.create_task(periodic_cleanup())
    
    yield
    
    # Shutdown
    cleanup_task.cancel()
    await close_mongodb_connection()


# FastAPI app
app = FastAPI(
    title="CodeCollab API",
    description="Real-time code sharing and collaboration",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# REST API Endpoints
@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "ok",
        "message": "CodeCollab API is running",
        "version": "1.0.0"
    }


@app.post("/api/spaces", response_model=SpaceResponse)
async def create_new_space(space_data: SpaceCreate):
    """Create a new code space"""
    space_id = str(uuid.uuid4())[:8]  # Short UUID for cleaner URLs
    
    space_doc = await create_space(
        space_id=space_id,
        language=space_data.language,
        initial_code=space_data.initial_code
    )
    
    invite_link = f"{FRONTEND_URL}/space/{space_id}"
    
    return SpaceResponse(
        space_id=space_id,
        language=space_doc["language"],
        code=space_doc["code"],
        created_at=space_doc["created_at"],
        invite_link=invite_link
    )


@app.get("/api/spaces/{space_id}", response_model=SpaceResponse)
async def get_space_details(space_id: str):
    """Get details of a specific space"""
    space = await get_space(space_id)
    
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    
    invite_link = f"{FRONTEND_URL}/space/{space_id}"
    
    return SpaceResponse(
        space_id=space["space_id"],
        language=space["language"],
        code=space["code"],
        created_at=space["created_at"],
        invite_link=invite_link
    )


@app.delete("/api/spaces/{space_id}")
async def delete_space_endpoint(space_id: str):
    """Delete a space"""
    deleted = await delete_space(space_id)
    
    if not deleted:
        raise HTTPException(status_code=404, detail="Space not found")
    
    return {"message": "Space deleted successfully"}


@app.get("/api/spaces/{space_id}/users")
async def get_active_users_count(space_id: str):
    """Get count of active users in a space"""
    space = await get_space(space_id)
    
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    
    active_count = manager.get_active_users(space_id)
    
    return {
        "space_id": space_id,
        "active_users": active_count
    }


# WebSocket endpoint
@app.websocket("/ws/{space_id}")
async def websocket_endpoint(websocket: WebSocket, space_id: str):
    """WebSocket endpoint for real-time code synchronization"""
    # Check if space exists
    space = await get_space(space_id)
    if not space:
        await websocket.close(code=4004, reason="Space not found")
        return
    
    # Generate unique user ID for this connection
    user_id = str(uuid.uuid4())[:8]
    
    await manager.connect(websocket, space_id, user_id)
    
    try:
        # Send initial state to new user
        await websocket.send_json({
            "type": "init",
            "space_id": space_id,
            "user_id": user_id,
            "code": space["code"],
            "language": space["language"],
            "active_users": manager.get_active_users(space_id)
        })
        
        # Notify others that a new user joined
        await manager.broadcast(
            space_id,
            {
                "type": "user_join",
                "user_id": user_id,
                "active_users": manager.get_active_users(space_id)
            },
            exclude=websocket
        )
        
        # Listen for messages
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            message_type = message.get("type")
            
            if message_type == "code_change":
                new_code = message.get("code", "")
                now = time.time()
                last = manager.last_db_write.get(space_id, 0)
                if now - last >= 0.5:
                    await update_space_code(space_id, new_code)
                    manager.last_db_write[space_id] = now
                
                await manager.broadcast(
                    space_id,
                    {
                        "type": "code_change",
                        "code": new_code,
                        "user_id": user_id
                    },
                    exclude=websocket
                )
            
            elif message_type == "language_change":
                new_language = message.get("language", "python")
                await update_space_language(space_id, new_language)
                
                await manager.broadcast(
                    space_id,
                    {
                        "type": "language_change",
                        "language": new_language,
                        "user_id": user_id
                    },
                    exclude=websocket
                )
            
            elif message_type == "cursor_move":
                await manager.broadcast(
                    space_id,
                    {
                        "type": "cursor_move",
                        "user_id": user_id,
                        "cursor_position": message.get("cursor_position", {})
                    },
                    exclude=websocket
                )
    
    except WebSocketDisconnect:
        await manager.disconnect(websocket, space_id)
        # Notify others that user left
        await manager.broadcast(
            space_id,
            {
                "type": "user_leave",
                "user_id": user_id,
                "active_users": manager.get_active_users(space_id)
            }
        )
    except Exception as e:
        print(f"WebSocket error: {e}")
        await manager.disconnect(websocket, space_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
