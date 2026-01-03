from aiogram import Router
from aiogram.filters import Command, CommandStart, StateFilter
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
import re
from telethon import TelegramClient
from aiogram import F
import asyncio

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
    upsert_user_state,
    update_deal,
    get_deal_id_by_buyer_id,
    get_user_wallet_by_user_id

)

from regular_bot.states import (
    CaptchaVibes,
    NewDeal,
    BuyerAccept,
    SellerDeposit,
    SellerConfirm,
    GetWalletAddress,
    DebugStates,
)
from regular_bot.keyboards import get_dynamic_keyboard
from regular_bot.config import ADMIN_IDS, INNER_BOT, BOT_WALLET_ADDRESS, WALLET_BOT
from regular_bot.utils import to_entity
from regular_bot.wallet import TelethonWalletAPI

logger = logging.getLogger(__name__)


def _is_admin(user_id: Optional[int]) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False
    

def setup_handlers(router: Router, wallet_api: TelethonWalletAPI, client: TelegramClient) -> None:
    """Register all message handlers with the router.
    
    Args:
        router: The aiogram Router instance
        wallet_api: TelethonWalletAPI instance (or None if not configured)
    """
    '''Admin debug UI: /debug shows inline buttons which call callbacks.'''

    @router.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        username_str = message.from_user.username
        if not username_str:
            await message.answer("Пожалуйста, установите username в настройках Telegram и перезапустите бота с /start.")
            return

        # Принудительно создаем пользователя, если его нет
        await upsert_user(username_str, message.from_user.id)
        
        user = await find_user_by_username(username_str)
        wallet = user.get('wallet')
        
        if not wallet:
            await message.answer(f"Привет @{username_str}, я Jescrow-bot, пожалуйста, отправь мне адрес твоего кошелька.",
                                  reply_markup=await get_dynamic_keyboard(message.from_user.id, await state.get_state()))
            await state.set_state(GetWalletAddress.waiting_for_address)
        else:
            await message.answer(f'Привет @{username_str}, хочешь совершить сделку? используй /new_deal')

    @router.message(GetWalletAddress.waiting_for_address)
    async def handle_wallet_address(message: Message, state: FSMContext) -> None:
        address = message.text
        
        if len(address) != 42:
            await message.answer('Не корректный адресс кошелька')
        else:
            await set_user_wallet(message.from_user.username, address)  # Исправлено: используем username вместо id
            await message.answer(f"адрес кошелька установлен: {address}", reply_markup=await get_dynamic_keyboard(message.from_user.id, await state.get_state()))
            await state.clear()

    @router.message(Command("new_deal"))
    async def new_deal_start(message: Message, state: FSMContext) -> None:
        deal_id = await create_deal(seller_id=message.from_user.id)
        await state.update_data(deal_id=deal_id)
        await state.set_state(NewDeal.buyer_username)
        await message.answer("Введите username покупателя (с @, например @buyer).", reply_markup=await get_dynamic_keyboard(message.from_user.id, await state.get_state()))

    @router.message(NewDeal.buyer_username)
    async def process_buyer_username(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        deal_id = data.get("deal_id")


        if message.text == "Отмена":
            await delete_deal(deal_id=deal_id)
            await state.clear()
            await message.answer("окей отмена", reply_markup=await get_dynamic_keyboard(message.from_user.id, await state.get_state()))
            return

        if message.text.startswith('/'):
            await state.clear()
            await message.answer('повторите команду')

        if message.text.startswith('@'):
            buyer_username = message.text.strip('@')
            buyer = await find_user_by_username(buyer_username)

            if not buyer:
                await message.answer("Покупатель не зарегистрирован в боте. Попросите его запустить /start.")
                return
            
            buyer_id = buyer['user_id']
            deal_id = data.get('deal_id')
            await update_deal(deal_id, buyer_id=buyer_username)
            await state.update_data(buyer_username=buyer_username, buyer_id=buyer_username)
            await state.set_state(NewDeal.crypto_amount)

            # Используем telethon_req вместо get_courses  
            result = await wallet_api.telethon_req(action="/btc", message=message, state=state)
            if result is not None:
                course_text = result.text if hasattr(result, 'text') else str(result)
                course_parts = course_text.split()[5:8]
                # Очистка третьей части от нецифровых символов
                cleaned_third_part = course_parts[2][:3]
                course = int(course_parts[0] + course_parts[1] + cleaned_third_part)
                logger.info(f"Course: {course}")
                await state.update_data(course=course)
            
            await message.answer(f"Введите сумму крипты (в рублях) для сделки.\n{course_text}")
        else:
            await state.clear()


    @router.message(NewDeal.crypto_amount)  
    async def process_crypto_amount(message: Message, state: FSMContext) -> None:  
        try:  
            data = await state.get_data()
            amount = float(message.text)
            crypto_amount = round(amount / data.get('course'), 8)
            raf_crypto_amount = format(crypto_amount, ".8f")
            await state.update_data(fiat_amount=amount)
            await state.update_data(crypto_amount=crypto_amount)

            kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Продать по курсу", callback_data="fiat:yes")],
                ]
            )
            await message.answer(f"продать по курсу {data.get('course')} руб:{raf_crypto_amount}btc?\n или введите новую свою сумму", reply_markup=kb)
                
        except ValueError:  
            await message.answer("Неверный формат. Введите число.")

    @router.callback_query(F.data == "fiat:yes")
    async def confirm_deal(callback: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        fiat_amount = data.get('fiat_amount')
        crypto_amount = data.get('crypto_amount')

        await state.update_data(fiat_amount=fiat_amount)
        await state.update_data(crypto_amount=crypto_amount)
        await callback.message.answer("Введите детали оплаты фиата (банковские реквизиты и т.д.).")
        await state.set_state(NewDeal.payment_details)

    @router.message(NewDeal.fiat_amount)
    async def process_fiat_amount(message: Message, state: FSMContext) -> None:
        await state.update_data(fiat_amount=message.text)
        await state.set_state(NewDeal.payment_details)
        await message.answer("Введите детали оплаты фиата (банковские реквизиты и т.д.).")

    @router.message(NewDeal.payment_details)
    async def process_payment_details(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        deal_id = data['deal_id']
        fiat_amount = data["fiat_amount"]
        crypto_amount = data['crypto_amount']
        
        # Обновляем payment_details существующей сделки
        await update_deal(deal_id, payment_details=message.text, fiat_amount=fiat_amount, crypto_amount=crypto_amount)

        


        result = await wallet_api.telethon_req(action="/balance", message=message, state=state)
        if result is not None:
            wallet_text = result.text if hasattr(result, 'text') else str(result)
            wallet = wallet_text.splitlines()[4:5]
            logger.info(f"bot_addres: {wallet}")



        await state.clear()
        keyboard = await get_dynamic_keyboard(message.from_user.id, await state.get_state())

        await message.answer(
            f"Сделка #{deal_id} создана. Отправьте {data['crypto_amount']} BTC на адрес бота: {wallet}.",
            reply_markup=keyboard
        )
        
        # Уведомляем покупателя
        buyer_keyboard = await get_dynamic_keyboard(data['buyer_id'], await state.get_state())
        await message.bot.send_message(
            data['buyer_id'],
            f"Новая сделка #{deal_id} от @{message.from_user.username}. Крипта: {data['crypto_amount']} BTC, фиат: {data['fiat_amount'] * 0.03}. Подтвердите с /accept {deal_id}.",
            reply_markup=buyer_keyboard
        )

    @router.message(Command("accept"))
    async def buyer_accept_start(message: Message, state: FSMContext) -> None:
        try:
            parts = message.text.split()
            
            # Проверяем наличие deal_id в команде
            if len(parts) < 2:
                await message.answer("Использование: /accept [номер сделки]")
                return
            
            # Пытаемся извлечь deal_id из команды
            try:
                input_deal_id = int(parts[1])
            except ValueError:
                await message.answer("Неверный формат номера сделки. Используйте целое число.")
                return
            
            # Проверяем сделку по buyer_id
            deal_id = await get_deal_id_by_buyer_id(message.from_user.id)
            
            deal = await get_deal_by_id(deal_id)
            if not deal or deal['buyer_id'] != message.from_user.id:
                await message.answer("Неверный ID сделки.")
                logger.info(f'{deal_id, deal, message.from_user.id}')
                return

            wallet = await get_user_wallet_by_user_id(message.from_user.id)

            if wallet is not None:
                await state.set_state(BuyerAccept.wallet_address)
                await state.update_data(deal_id=deal_id)
                await message.answer("Введите ваш BTC адрес для получения крипты.", reply_markup=ReplyKeyboardRemove())
        except Exception as e:
            logger.error(f"Ошибка в buyer_accept_start: {e}")
            await message.answer("Произошла ошибка при обработке команды /accept.")

    @router.message(BuyerAccept.wallet_address)
    async def process_buyer_wallet(message: Message, state: FSMContext) -> None:
        address = message.text.strip()
        
        # Простая валидация BTC адреса (26-35 символов, без пробелов)
        if not (len(address) == 42 and ' ' not in address):
            await message.answer("Неверный BTC адрес.")
            return
        
        data = await state.get_data()
        deal_id = data['deal_id']
        await update_deal_buyer_wallet(deal_id, address)
        
        await state.clear()
        keyboard = await get_dynamic_keyboard(message.from_user.id, await state.get_state())
        await message.answer("Адрес сохранен. Ждите депозита от продавца.", reply_markup=keyboard)
        
        # Уведомляем продавца
        deal = await get_deal_by_id(deal_id)
        seller_id = deal['seller_id']
        seller_keyboard = await get_dynamic_keyboard(seller_id, await state.get_state())
        await message.bot.send_message(
            seller_id,
            f"Покупатель принял сделку #{deal_id} и предоставил адрес.",
            reply_markup=seller_keyboard
        )

    @router.message(Command("deposit"))
    async def seller_deposit_start(message: Message, state: FSMContext) -> None:
        try:
            parts = message.text.split()
            deal_id = int(parts[1]) if len(parts) > 1 else None
            
            if deal_id is None:
                await message.answer("Использование: /deposit <deal_id>")
                return
            
            deal = await get_deal_by_id(deal_id)
            if not deal or deal['seller_id'] != message.from_user.id:
                await message.answer("Неверный ID сделки.")
                return
            
            # Отмечаем депозит как внесённый
            await set_deal_deposited(deal_id)
            
            keyboard = await get_dynamic_keyboard(message.from_user.id, await state.get_state())
            await message.answer(
                f"Депозит зафиксирован для сделки #{deal_id}.",
                reply_markup=keyboard
            )
            
            # Уведомляем покупателя
            data = await state.get_data()
            payment_details = data.get('payment_details')
            fiat_amount = data.get('fiat_amount')

            buyer_id = deal['buyer_id']
            buyer_keyboard = await get_dynamic_keyboard(buyer_id, await state.get_state())
            await message.bot.send_message(
                buyer_id,
                f"Продавец внёс депозит для сделки #{deal_id}. Ожидаем подтверждения.\n отправьте рубли {fiat_amount * 1.03} | комиссия составила {fiat_amount * 0.03} : 3% \n данные о реквизитах:\n {payment_details}",
                reply_markup=buyer_keyboard
            )
            
        except (ValueError, IndexError):
            await message.answer("Использование: /deposit <deal_id>")
        except Exception as e:
            await message.answer(f"Ошибка: {str(e)}")

    @router.message(Command("confirm"))
    async def seller_confirm_start(message: Message, state: FSMContext) -> None:
        try:
            parts = message.text.split()
            deal_id = int(parts[1]) if len(parts) > 1 else None
            if deal_id is None:
                await message.answer("Использование: /confirm <deal_id>")
                return
            deal = await get_deal_by_id(deal_id)
            if not deal or deal['seller_id'] != message.from_user.id or not deal['deposited']:
                await message.answer("Неверный ID или депозит не подтвержден.")
                return
            await state.set_state(SellerConfirm.confirm)
            await state.update_data(deal_id=deal_id)
            await message.answer("Подтвердите получение фиата: да/нет", reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]],
                resize_keyboard=True,
            ))
        except:
            await message.answer("Использование: /confirm <deal_id>")

    @router.message(SellerConfirm.confirm)
    async def process_confirm(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        deal_id = data['deal_id']

        if message.text.lower() == "нет":
            await delete_deal(deal_id)

        if message.text.lower() == "да":
            try:
                deal = await get_deal_by_id(deal_id)
                amount = deal['fiat_amount']
                buyer_id = deal['buyer_id']
                
                # Отправляем команду через wallet_api
                await wallet_api.telethon_req(action = "send_crypto", buyer_id=buyer_id, amount=amount, message=message, state=state)
                    
                
                keyboard = await get_dynamic_keyboard(message.from_user.id, await state.get_state())
                await message.answer(
                    f"Команда отправки отправлена. Ожидаем подтверждения от бота кошелька.",
                    reply_markup=keyboard
                )
                
                buyer_keyboard = await get_dynamic_keyboard(buyer_id, await state.get_state())
                await message.bot.send_message(
                    buyer_id,
                    f"Крипта из сделки #{deal_id} в пути на ваш адрес.",
                    reply_markup=buyer_keyboard
                )
                
                # Закрываем сделку (сохраняем в БД для истории)
                await close_deal(deal_id)
                
            except Exception as e:
                await message.answer(f"Ошибка отправки: {str(e)}")
        else:
            await message.answer("Сделка не подтверждена. Обсудите с покупателем.")
        
        await state.clear()
        keyboard = await get_dynamic_keyboard(message.from_user.id, await state.get_state())
        await message.answer("Готово.", reply_markup=keyboard)

    @router.message(Command("delete"))
    async def delete_deal_start(message: Message, state: FSMContext) -> None:
        try:
            parts = message.text.split()
            deal_id = int(parts[1]) if len(parts) > 1 else None
            
            if deal_id is None:
                await message.answer("Использование: /delete <deal_id>")
                return
            
            deal = await get_deal_by_id(deal_id)
            if not deal or deal['seller_id'] != message.from_user.id:
                await message.answer("Неверный ID сделки или у вас нет прав на удаление.")
                return
            
            # Проверяем, что сделка ещё не имеет депозита
            if deal['deposited']:
                await message.answer("Невозможно удалить сделку после внесения депозита.")
                return
            
            # Удаляем сделку
            await delete_deal(deal_id)
            
            keyboard = await get_dynamic_keyboard(message.from_user.id, await state.get_state())
            await message.answer(
                f"Сделка #{deal_id} удалена.",
                reply_markup=keyboard
            )
            
        except (ValueError, IndexError):
            await message.answer("Использование: /delete <deal_id>")
        except Exception as e:
            await message.answer(f"Ошибка при удалении сделки: {str(e)}")

    @router.callback_query(StateFilter("waiting_btc_button"))
    async def cb_btc_buttons(callback: CallbackQuery, state: FSMContext) -> None:  
        """Обрабатывает callback от BTC inline кнопок."""    
        current_state = await state.get_state()    
        
        if current_state == "waiting_btc_button":    
            button_text = callback.data    
            logger.info(f'{button_text}')   
            
            data = await state.get_data()    
            response = data.get("response")    
            conv = data.get("conv")   
            
            if conv and response:  
                try:  
                    await response.click(text=button_text)
                    asyncio.sleep(0.5)
                    new_response = await conv.get_response()
                    
                    await state.set_state(data.get('prev_state'))

                    if new_response:  
                        if new_response.media:
                            # Handle another media response if needed  
                            file_bytes = await new_response.download_media(bytes)  
                            photo = BufferedInputFile(file_bytes, filename="btc_result.png")  
                            await callback.message.answer_photo(photo=photo)  
                        else:  
                            # Handle text response  
                            msg = new_response.message  
                            await callback.message.answer(msg)  
                    
                    await callback.answer()
                    
    
                except Exception as e:  
                    logger.error(f"Error in callback: {e}", exc_info=e)  
                    await callback.answer("Произошла ошибка", show_alert=True)
