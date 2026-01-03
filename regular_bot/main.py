import asyncio
import logging
import sys
from pathlib import Path
 
# Add project root to sys.path for imports to work correctly
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from telethon import TelegramClient

from regular_bot.config import TOKEN, OUTER_BOT, OUTER_BOT_USERNAME
from regular_bot.wallet import TelethonWalletAPI, wallet_response_listener
from regular_bot.handlers import setup_handlers
from regular_bot.handlers_callbaks import setup_callbacks
from regular_bot.handlers_callbaks import CallbackHandlers
from db import create_tables
import traceback

# Wallet API instance will be created in main() once Bot is available
wallet_api: TelethonWalletAPI | None = None

# Telethon client instance - will be set in run_telethon_bot()
client = None
client_ready = asyncio.Event()

# Task for telethon_bot background process
telethon_task: asyncio.Task | None = None


async def run_telethon_bot():
    """Run telethon_bot as a background task."""
    global client, client_ready
    
    try:
        # small delay to allow aiogram polling to start first (prevents startup race)
        await asyncio.sleep(2)

        from telethon_bot.client import create_client
        from telethon_bot.flow import TelegramFlow
        from telethon_bot.handlers import register_handlers
        from telethon_bot.config import PHONE
        
        client = create_client()
        flow = TelegramFlow(client)
        register_handlers(client, flow)
        
        await client.start(phone=PHONE)
        client_ready.set()  # Signal that client is ready for use
        
        logging.info('Telethon intermediary bot started FROM MAIN.PY')
        #await client.send_message(OUTER_BOT_USERNAME, '/start_from_bot')
        await client.run_until_disconnected()
        
    except Exception as e:
        logging.error(f'Telethon bot error: {e}', exc_info=True)


async def main() -> None:
    """Initialize bot, dispatcher, and handlers. Start polling."""
    global wallet_api, telethon_task, client, client_ready
    
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Create router
    router = Router()
    
    # Start telethon_bot as a background task
    telethon_task = asyncio.create_task(run_telethon_bot())
    logging.info('Started telethon_bot background task')

    # Wait for telethon client to be ready and is a TelegramClient instance
    while not isinstance(client, TelegramClient):
        await client_ready.wait()
        await asyncio.sleep(0.1)  # Небольшая пауза для предотвращения спинлока
    
    logging.info('Telethon client is ready')

    # Create wallet API instance AFTER client is ready
    wallet_api = TelethonWalletAPI(bot, router, client)
    
    # Создаем таблицы базы данных
    try:
        await create_tables()
        logging.info('Database tables initialized')
    except Exception as e:
        logging.error(f'Error initializing database tables: {e}')
        logging.error(traceback.format_exc())
        # Можно добавить дополнительную обработку ошибки, например, остановку бота
    
    # Setup all message handlers (they will be registered with the router)
    setup_handlers(router, wallet_api, client)
    CallbackHandlers(router, wallet_api, client).setup()
    
    # Register wallet_response_listener as a catch-all message handler (must be last)
    # This catches responses from telethon_bot with [REQ_*] markers
    @router.message()
    async def _wallet_listener(message):
        await wallet_response_listener(message)
    
    # Include the router in dispatcher
    dp.include_router(router)
    
    try:
        # Start polling
        await dp.start_polling(bot)
    finally:
        # Cleanup: cancel telethon task if main bot stops
        if telethon_task and not telethon_task.done():
            telethon_task.cancel()
            try:
                await telethon_task
            except asyncio.CancelledError:
                logging.info('Telethon bot task cancelled')


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
