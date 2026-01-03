import asyncio
from .client import create_client
from .flow import TelegramFlow
from .handlers import register_handlers
from .config import PHONE


async def main():
    client = create_client()
    flow = TelegramFlow(client)
    register_handlers(client, flow)

    await client.start(phone=PHONE)
    print('Telethon intermediary bot started SKIBIDI')
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
