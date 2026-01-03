from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BufferedInputFile
)
import logging
from typing import Optional
import asyncio
from telethon import TelegramClient

from db import (
    upsert_user,
    find_user_by_username,
    create_deal,
    get_deal_by_id,
    get_deals_for_user,
    update_deal_buyer_wallet,
    set_deal_deposited,
    set_user_wallet,
    delete_deal,
    close_deal,
    upsert_user_state
)

from regular_bot.states import (
    NewDeal,
    BuyerAccept,
    SellerDeposit,
    SellerConfirm,
    GetWalletAddress,
    DebugStates,
)
from regular_bot.keyboards import get_dynamic_keyboard
from regular_bot.config import ADMIN_IDS, INNER_BOT, WALLET_BOT
from regular_bot.utils import to_entity


from regular_bot.wallet import TelethonWalletAPI

logger = logging.getLogger(__name__)


def _is_admin(user_id: Optional[int]) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False


class CallbackHandlers:
    """Handles all callback queries and debug functionality."""

    def __init__(self, router: Router, wallet_api: TelethonWalletAPI, client: TelegramClient):
        """Initialize callback handlers.

        Args:
            router: The aiogram Router instance
            wallet_api: TelethonWalletAPI instance (or None if not configured)
        """
        self.router = router
        self.wallet_api = wallet_api
        self.client = client

    def setup(self) -> None:
        """Register all callback handlers with the router."""
        self.router.message(Command("debug"))(self.cmd_debug_menu)
        self.router.callback_query(lambda c: c.data and c.data.startswith("debug:"))(
            self.cb_debug_router
        )
        self.router.callback_query()(self.cb_btc_buttons)

    async def cb_btc_buttons(self, callback: CallbackQuery, state: FSMContext) -> None:  
        """Обрабатывает callback от BTC inline кнопок."""  
        current_state = await state.get_state()  
        
        # Проверяем, что мы ждём BTC кнопку  
        if current_state == "waiting_btc_button":  
            button_text = callback.data  
            logger.info(f'{button_text }') 
            
            # Получаем сохранённые данные  
            data = await state.get_data()  
            response = data.get("response")  
            conv = data.get("conv") 
            
            # Здесь можно использовать response для telethon  
            if conv and response:
                try:
                    # Нажимаем кнопку в Telethon по тексту  
                    await response.click(text=button_text)
                    # Получаем новый ответ после нажатия  
                    new_response = await conv.get_response()
                    if new_response:
                        await callback.message.answer(new_response)

                except Exception as e:
                    logger.error()


    async def cmd_debug_menu(self, message: Message, state: FSMContext) -> None:
        """Display admin debug menu."""
        if not _is_admin(message.from_user.id):
            await message.answer("Доступ запрещен: только для администраторов.")
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="State", callback_data="debug:state")],
                [InlineKeyboardButton(text="Clear state", callback_data="debug:clearstate")],
                [InlineKeyboardButton(text="List deals", callback_data="debug:list_deals")],
                [InlineKeyboardButton(text="Get deal (enter id)", callback_data="debug:get_deal")],
                [InlineKeyboardButton(text="send /balance to k-bot", callback_data="debug:k-bot_balance")],
                [InlineKeyboardButton(text="Who lets the dogs out?", callback_data="debug:who_lets_the_dogs_out")],
                [InlineKeyboardButton(text="get User", callback_data="debug:get_user")],
                [InlineKeyboardButton(text="get last message from telethon", callback_data="debug:get_last_message")],
                [InlineKeyboardButton(text="/btc", callback_data="debug:lets_btc")]
            ]
        )
        await message.answer("Debug menu:", reply_markup=kb)

    async def cb_debug_router(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Route debug callback queries to appropriate handlers."""
        user_id = callback.from_user.id
        if not _is_admin(user_id):
            await callback.answer("Доступ запрещен", show_alert=True)
            return

        action = callback.data.split(":", 1)[1]
        await callback.answer()  # acknowledge the callback

        if action == "lets_btc":
            try:
                async with self.client.conversation(to_entity(WALLET_BOT), timeout=10) as conv:
                    await conv.send_message("/btc")
                    response = await conv.get_response()

                    if response.media != None:
                        file_bytes = await response.download_media(bytes)
                        logger.info(f"Медиа получено в память, размер: {len(file_bytes)} байт")

                        button_texts = []
                        if response.reply_markup and hasattr(response.reply_markup, 'rows'):
                            for row in response.reply_markup.rows:  
                                for button in row.buttons:  
                                    if hasattr(button, 'text'):  
                                        button_texts.append(button.text) 
                        
                        photo = BufferedInputFile(file_bytes, filename="btc_image.png")  
                        keyboard = InlineKeyboardMarkup(  
                            inline_keyboard=[  
                                [InlineKeyboardButton(text=text, callback_data=text)]  
                                for text in button_texts  
                            ]  
                        ) 

                        await state.set_state("waiting_btc_button")
                        await state.update_data(
                            button_texts=button_texts,
                            response=response,
                            conv=conv)

                        await callback.message.answer_photo(  
                            photo=photo,  
                            reply_markup=keyboard  
                        )
                    else:
                        msg = response.message
                        lines = msg.splitlines()
                        msg = ''.join(lines[0:3])
                        logger.info(f'{msg}')
                        await callback.message.answer(msg)



            except Exception as e:
                logger.error("Error in _on_message", exc_info=e)

        if action == "get_user":
            user = await find_user_by_username(callback.from_user.username)
            await callback.message.answer(f"User: {user}")
            return

        if action == "state":
            current = await state.get_state()
            data = await state.get_data()
            await callback.message.answer(f"Current state: {current}\nData: {data}")
            return

        if action == "clearstate":
            # clear admin's FSM state and DB state record if possible
            await state.clear()
            try:
                await upsert_user_state(callback.from_user.username, None)
            except Exception:
                pass
            await callback.message.answer("State cleared.")
            return

        if action == "list_deals":
            deals = await get_deals_for_user(user_id) or []
            if not deals:
                await callback.message.answer("Сделки не найдены.")
                return
            lines = []
            for d in deals:
                lines.append(
                    f"#{d.get('id', d.get('deal_id','?'))} seller:{d.get('seller_id')} buyer:{d.get('buyer_id')} amount:{d.get('crypto_amount')} deposited:{d.get('deposited')}"
                )
            await callback.message.answer("\n".join(lines))
            return

        if action == "get_deal":
            # ask admin to send deal id as a message; use FSM to await
            await state.set_state(DebugStates.waiting_for_deal_id)
            await callback.message.answer("Введите ID сделки (число):", reply_markup=ReplyKeyboardRemove())
            return

        if action == "k-bot_balance":
            if self.wallet_api is None:
                await callback.message.answer("Wallet API не настроен.")
                return
            try:
                resp = await self.wallet_api.send_command("/balance", timeout=10)
                text = resp[1].get('text', '') if resp else 'No response'
                await callback.message.answer(f"Response from k-bot:\n{text}")
            except Exception as e:
                await callback.message.answer(f"Error: {str(e)}")
            return

        if action == "who_lets_the_dogs_out":
            await callback.message.bot.send_message(INNER_BOT, "Who let the dogs out?")
            return

        if action == "get_last_message":
            if self.wallet_api is None:
                await callback.message.answer("Wallet API не настроен.")
                return
            try:
                #last_msg = await self.wallet_api.get_last_message_from_wallet(timeout=10)
                msgs = await self.client.get_messages(to_entity(WALLET_BOT), limit=1)
                last_msg = msgs[0].message

                logging.info(f'answer: {last_msg}')
                await callback.message.answer(f"Последнее сообщение из WALLET_BOT:\n\n{last_msg}")
            except asyncio.TimeoutError:
                await callback.message.answer("Таймаут: telethon_bot не ответил в течение 10 секунд.")
            except Exception as e:
                await callback.message.answer(f"Ошибка: {str(e)}")
            return


def setup_callbacks(router: Router, wallet_api: TelethonWalletAPI) -> None:
    """Register all callback handlers with the router (legacy function).

    Args:
        router: The aiogram Router instance
        wallet_api: TelethonWalletAPI instance (or None if not configured)
    """
    handler = CallbackHandlers(router, wallet_api)
    handler.setup()
