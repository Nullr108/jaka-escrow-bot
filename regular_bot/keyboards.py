from typing import Optional
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from db import get_deals_for_user
import logging

logger = logging.getLogger(__name__)


async def get_dynamic_keyboard(user_id: int, deal_id: Optional[int] = None, state: Optional[str] = None) -> Optional[ReplyKeyboardMarkup]:
    """Функция для динамической клавиатуры по ролям пользователя.
    
    Args:
        user_id: ID пользователя Telegram
        deal_id: ID сделки (опционально)
        state: Текущее состояние бота
        
    Returns:
        ReplyKeyboardMarkup с кнопками действий или None
    """

    if state == 'NewDeal:buyer_username':
        if deal_id is not None:
            return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
        return None

    if state == 'GetWalletAddress:waiting_for_address':
        # Если ожидаем адрес кошелька, не показываем другие кнопки
        return None

    deals = await get_deals_for_user(user_id)

    buttons = [KeyboardButton(text="/new_deal")]  # Всегда доступно

    for deal in deals:
        if user_id == deal['buyer_id'] and not deal['buyer_wallet']:
            buttons.append(KeyboardButton(text=f"/accept {deal['deal_id']}"))
        if user_id == deal['seller_id']:
            if not deal['deposited']:
                buttons.append(KeyboardButton(text=f"/deposit {deal['deal_id']}"))
                # Добавляем кнопку удаления сделки для создателя, если депозит ещё не внесён
                # это можно сделать через проверку статуса сделки
                if not deal['closed']:
                    buttons.append(KeyboardButton(text=f"/delete {deal['deal_id']}"))
            else:
                buttons.append(KeyboardButton(text=f"/confirm {deal['deal_id']}"))

    if len(buttons) > 1:  # Если есть действия помимо /new_deal
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]  # По 2 в ряд
        return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/new_deal")]], resize_keyboard=True)
