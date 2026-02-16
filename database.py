from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

# MongoDB configuration
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "codecollab")
SPACE_EXPIRY_HOURS = int(os.getenv("SPACE_EXPIRY_HOURS", "24"))

# Global database client
client: AsyncIOMotorClient = None
database = None


async def connect_to_mongodb():
    global client, database
    client = AsyncIOMotorClient(MONGODB_URL)
    database = client[DATABASE_NAME]
    print(f"✅ Connected to MongoDB: {DATABASE_NAME}")
    await database.spaces.create_index("space_id", unique=True)
    await database.spaces.create_index(
        [("created_at", 1)],
        expireAfterSeconds=SPACE_EXPIRY_HOURS * 3600
    )


async def close_mongodb_connection():
    """Close MongoDB connection"""
    global client
    if client:
        client.close()
        print("❌ Closed MongoDB connection")


def get_database():
    """Get database instance"""
    return database


# Space operations
async def create_space(space_id: str, language: str = "python", initial_code: str = ""):
    """Create a new code space"""
    space_document = {
        "space_id": space_id,
        "code": initial_code,
        "language": language,
        "created_at": datetime.utcnow(),
        "last_updated": datetime.utcnow(),
        "active_users": []
    }
    await database.spaces.insert_one(space_document)
    return space_document


async def get_space(space_id: str):
    """Get a space by ID"""
    return await database.spaces.find_one({"space_id": space_id})


async def update_space_code(space_id: str, code: str):
    """Update code in a space"""
    result = await database.spaces.update_one(
        {"space_id": space_id},
        {
            "$set": {
                "code": code,
                "last_updated": datetime.utcnow()
            }
        }
    )
    return result.modified_count > 0


async def update_space_language(space_id: str, language: str):
    """Update language in a space"""
    result = await database.spaces.update_one(
        {"space_id": space_id},
        {
            "$set": {
                "language": language,
                "last_updated": datetime.utcnow()
            }
        }
    )
    return result.modified_count > 0


async def delete_space(space_id: str):
    """Delete a space"""
    result = await database.spaces.delete_one({"space_id": space_id})
    return result.deleted_count > 0


async def cleanup_expired_spaces():
    """Delete spaces older than SPACE_EXPIRY_HOURS"""
    expiry_time = datetime.utcnow() - timedelta(hours=SPACE_EXPIRY_HOURS)
    result = await database.spaces.delete_many({
        "created_at": {"$lt": expiry_time}
    })
    if result.deleted_count > 0:
        print(f"🧹 Cleaned up {result.deleted_count} expired spaces")
    return result.deleted_count


async def add_user_to_space(space_id: str, user_id: str):
    """Add a user to the active users list"""
    await database.spaces.update_one(
        {"space_id": space_id},
        {"$addToSet": {"active_users": user_id}}
    )


async def remove_user_from_space(space_id: str, user_id: str):
    """Remove a user from the active users list"""
    await database.spaces.update_one(
        {"space_id": space_id},
        {"$pull": {"active_users": user_id}}
    )
