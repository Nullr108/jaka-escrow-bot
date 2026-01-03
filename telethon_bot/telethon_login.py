import logging
import os
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient, events


load_dotenv()


class TelegramEscrowBot:
    """Simple Telethon intermediary bot.

    Responsibilities:
    - Accept plain-text commands from `OUTER_BOT` (regular_bot).
    - Forward commands to `WALLET_BOT` and wait for replies.
    - If a wallet reply contains media, forward it to the requester.
    - If a wallet reply contains inline buttons, send list of buttons to requester
      and wait for their choice; then click the corresponding inline button.
    - Provide `send_command_to_wallet()` to programmatically send commands.
    - Provide a placeholder notification listener (forwarding wallet messages to OUTER_BOT).
    """

    def __init__(self):
        self.api_id = int(os.getenv('TELEGRAM_API_ID') or 0)
        self.api_hash = os.getenv('TELEGRAM_API_HASH')
        self.phone_number = os.getenv('TELEGRAM_PHONE')

        # Who is allowed to send control commands (regular bot)
        self.outer_bot = os.getenv('OUTER_BOT')
        # Wallet bot to which commands will be sent
        self.wallet_bot = os.getenv('WALLET_BOT')

        session_name = os.getenv('TELETHON_SESSION', 'telegram_session')
        self.client = TelegramClient(session_name, self.api_id, self.api_hash)

        # pending button prompts: requester_id_str -> {'future': Future, 'msg': Message, 'buttons': [...]}
        self.pending_button_prompts = {}

    async def send_command_to_wallet(self, command: str, wait_for_response: bool = True, timeout: int = 30):
        """Send `command` to `WALLET_BOT`. Optionally wait for a reply and return (resp_message, info_dict).

        info_dict contains: text, buttons (list), has_media (bool), message (telethon Message)
        """
        if not self.wallet_bot:
            raise RuntimeError('WALLET_BOT is not configured in env')

        target = int(self.wallet_bot) if str(self.wallet_bot).isdigit() else self.wallet_bot
        await self.client.send_message(target, command)

        if not wait_for_response:
            return None, {'text': None, 'buttons': [], 'has_media': False, 'message': None}

        try:
            event = await self.client.wait_for(events.NewMessage(from_users=target), timeout=timeout)
        except asyncio.TimeoutError:
            return None, {'text': None, 'buttons': [], 'has_media': False, 'message': None}

        msg = event.message
        text = msg.message or ''
        has_media = bool(msg.media)

        # Extract inline buttons text
        buttons = []
        try:
            if msg.buttons:
                for row in msg.buttons:
                    for b in row:
                        buttons.append(getattr(b, 'text', str(b)))
        except Exception:
            buttons = []

        return msg, {'text': text, 'buttons': buttons, 'has_media': has_media, 'message': msg}

    async def _prompt_buttons_and_wait(self, requester, msg, buttons, timeout=120):
        """Forward media (if present) and ask `requester` to choose a button.

        Returns selected index (1-based) or button text, or None on timeout.
        """
        req_entity = int(requester) if str(requester).isdigit() else requester

        # forward media/message if exists
        try:
            if msg and msg.media:
                await self.client.forward_messages(req_entity, msg)
        except Exception:
            pass

        # send choices
        choices_text = '\n'.join([f"{i+1}. {t}" for i, t in enumerate(buttons)])
        await self.client.send_message(req_entity, f"Выберите кнопку (ответьте номером или текстом):\n{choices_text}")

        fut = asyncio.get_event_loop().create_future()
        self.pending_button_prompts[str(req_entity)] = {'future': fut, 'msg': msg, 'buttons': buttons}

        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
            return res
        except asyncio.TimeoutError:
            return None
        finally:
            self.pending_button_prompts.pop(str(req_entity), None)

    async def process_flow(self, raw_text: str, requester):
        """Parse a string command from `requester` and run the flow.

        Currently supports:
          - '/balance' or text containing 'balance' -> sends '/balance' to wallet and handles reply/buttons
          - otherwise forwards text to wallet and returns reply
        """
        text = raw_text.strip()
        req_entity = int(requester) if str(requester).isdigit() else requester

        if text.startswith('/balance') or 'balance' in text.lower():
            resp_msg, info = await self.send_command_to_wallet('/balance', wait_for_response=True, timeout=30)
            if resp_msg is None:
                await self.client.send_message(req_entity, 'WALLET_BOT не ответил')
                return

            # forward response
            if info.get('has_media') and resp_msg:
                try:
                    await self.client.forward_messages(req_entity, resp_msg)
                except Exception:
                    await self.client.send_message(req_entity, info.get('text') or '')
            else:
                await self.client.send_message(req_entity, info.get('text') or '')

            # handle inline buttons
            buttons = info.get('buttons') or []
            if buttons:
                choice = await self._prompt_buttons_and_wait(requester, resp_msg, buttons, timeout=120)
                if choice is None:
                    await self.client.send_message(req_entity, 'Время ожидания выбора истекло')
                    return

                # perform click
                try:
                    # if numeric index provided
                    if isinstance(choice, int):
                        idx = choice - 1
                        await resp_msg.click(idx)
                    else:
                        # try to match text
                        idx = None
                        for i, t in enumerate(buttons):
                            if str(t).strip().lower() == str(choice).strip().lower():
                                idx = i
                                break
                        if idx is not None:
                            await resp_msg.click(idx)
                        else:
                            # fallback: send text to wallet bot
                            await self.client.send_message(int(self.wallet_bot) if str(self.wallet_bot).isdigit() else self.wallet_bot, str(choice))

                    await self.client.send_message(req_entity, 'Нажата кнопка')
                except Exception as e:
                    await self.client.send_message(req_entity, f'Ошибка при нажатии кнопки: {e}')
            return

        # default: forward to wallet and send back reply
        resp_msg, info = await self.send_command_to_wallet(text, wait_for_response=True, timeout=30)
        if resp_msg:
            if info.get('has_media'):
                await self.client.forward_messages(req_entity, resp_msg)
            else:
                await self.client.send_message(req_entity, info.get('text') or '')

    async def _on_new_message(self, event):
        """Main event handler for incoming messages."""
        try:
            sender_id = getattr(event.message, 'sender_id', None)
            sender_str = str(sender_id) if sender_id is not None else None

            # If there is a pending prompt for this sender (expecting a button choice), resolve it
            pending = None
            if sender_str and sender_str in self.pending_button_prompts:
                pending = self.pending_button_prompts[sender_str]

            # message text
            raw = event.message.message or ''

            # If message from OUTER_BOT -> treat as command or a response to prompt
            if self.outer_bot and sender_str == str(self.outer_bot):
                # resolve pending choice if exists
                if pending and not pending['future'].done():
                    txt = raw.strip()
                    if txt.isdigit():
                        pending['future'].set_result(int(txt))
                    else:
                        pending['future'].set_result(txt)
                    return

                # otherwise, new command from regular bot
                await self.process_flow(raw, self.outer_bot)
                return

            # If message from WALLET_BOT and not part of waiting flow, forward as notification placeholder
            if self.wallet_bot and sender_str == str(self.wallet_bot):
                # placeholder: forward incoming wallet messages to OUTER_BOT for notification handling
                if self.outer_bot:
                    try:
                        await self.client.forward_messages(int(self.outer_bot) if str(self.outer_bot).isdigit() else self.outer_bot, event.message)
                    except Exception:
                        pass
                return

            # otherwise ignore
            return
        except Exception:
            return

    async def main(self):
        await self.client.start(phone=self.phone_number)
        # Give the outer (aiogram) bot a moment to start polling so it doesn't miss the startup ping
        try:
            await asyncio.sleep(2)
        except Exception:
            pass

        # register handler
        self.client.add_event_handler(self._on_new_message, events.NewMessage())
        print('Telethon intermediary bot started BEBRA')
        await self.client.run_until_disconnected()

    def run(self):
        asyncio.run(self.main())


if __name__ == '__main__':
    # Thin wrapper kept for backward compatibility — use run.py as main module.
    from .run import main
    import asyncio

    asyncio.run(main())
