from dotenv import load_dotenv
import os

load_dotenv()

# Telethon credentials
API_ID = int(os.getenv('TELEGRAM_API_ID') or 0)
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')
SESSION = os.getenv('TELETHON_SESSION', 'telegram_session')

# Bots/entities
OUTER_BOT = os.getenv('OUTER_BOT')   # regular bot (sends commands)
WALLET_BOT = os.getenv('WALLET_BOT') # wallet bot (receives forwarded commands)
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []

# Optional wallet address env
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')

__all__ = ['API_ID','API_HASH','PHONE','SESSION','OUTER_BOT','WALLET_BOT','ADMIN_IDS','WALLET_ADDRESS']
