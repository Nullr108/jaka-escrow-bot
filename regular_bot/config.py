from dotenv import load_dotenv
from os import getenv

load_dotenv()

# Настройки бота и окружения
TOKEN = getenv("BOT_TOKEN")
NETWORK = getenv("NETWORK")
# Outer бот (escrow bot) — основной бот с обработчиками
OUTER_BOT = getenv("OUTER_BOT")
OUTER_BOT_USERNAME = getenv("OUTER_BOT_USERNAME")
# Wallet бот (Telethon) — управляет кошельком
WALLET_BOT = getenv("WALLET_BOT")
# Inner бот (если используется)
INNER_BOT = getenv("INNER_BOT")
BOT_WALLET_ADDRESS = getenv("BOT_WALLET_ADDRESS")
ADMIN_IDS = list(map(int, getenv("ADMIN_IDS", "").split(","))) if getenv("ADMIN_IDS") else []

__all__ = ["TOKEN", "NETWORK", "OUTER_BOT", "OUTER_BOT_USERNAME", "WALLET_BOT", "INNER_BOT", "ADMIN_IDS"]