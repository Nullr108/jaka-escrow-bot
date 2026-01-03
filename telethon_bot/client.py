from telethon import TelegramClient
from .config import API_ID, API_HASH, SESSION


def create_client() -> TelegramClient:
    """Create and return a Telethon client instance (not started)."""
    return TelegramClient(SESSION, API_ID, API_HASH)
