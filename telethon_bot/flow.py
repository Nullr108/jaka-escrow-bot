import asyncio
import logging
import re
from telethon import events, types
from typing import TypedDict
from telethon.tl.types import TypeReplyMarkup


from .utils import to_entity, extract_buttons, safe_forward
from .config import WALLET_BOT, ADMIN_IDS

class WalletResponse(TypedDict):
    file: bytes
    caption: str
    buttons: TypeReplyMarkup


class TelegramFlow:
    """Core logic: send commands to WALLET_BOT, handle replies, prompt for inline buttons.
    
    Supports two modes:
    1. Plain text commands (for telethon_bot intermediary mode)
    2. Structured commands with [REQ_*] markers (for wallet API compatibility)
    """

    def __init__(self, client):
        self.client = client
        # requester_str -> {'future': Future, 'msg': Message, 'buttons': [...]}
        self.pending_button_prompts = {}
        # request_id -> {'requester': str, 'future': Future}
        self.pending_wallet_responses = {}
        # request_id -> message_with_buttons (для сохранения капч)
        self.pending_captcha_messages = {}
        
    async def forward_message_with_inline_buttons(self, response) -> None | WalletResponse:
        """
         Пересылает сообщение с картинкой и inline-кнопками  

        Args:  
            client: TelegramClient instance  
            response: Message object от бота  
            target_chat: Куда переслать сообщение  
          
        Returns:  
            Message object или None если нет картинки/кнопок  
        """

        # Проверяем наличие медиа (картинки)  
        if not response.media:  
            return None  
          
    # Проверяем наличие inline-кнопок  
        if not response.reply_markup or not isinstance(response.reply_markup, types.ReplyInlineMarkup):  
            return None  

        response: WalletResponse = {'file': response.media, 'caption': response.caption,'buttons': response.reply_markup}
        return response
            
            
            



    async def send_wallet_command(self, command: str, requester: str = None, timeout: int = 30) -> str | WalletResponse:
        # Extract request_id if present in command, or return None  
        req_match = re.match(r'\[REQ_(\w+)\]\s*(.*)', command.strip())  
        if not req_match:  
            return None  
      
        request_id = req_match.group(1)  
        clean_command = req_match.group(2)  
      
        target = to_entity(WALLET_BOT)  
        if target is None:  
            raise RuntimeError('WALLET_BOT not configured')  
        
        try:  
            # Use conversation for request-response pattern  
            async with self.client.conversation(target, timeout=timeout) as conv:  
                # Send command  
                await conv.send_message(clean_command)  
              
                # Wait for response  
                response = await conv.get_response()  
                #check captcha
                captcha = self.forward_message_with_inline_buttons(self, response=response)
                req_entity = to_entity(requester) or requester  

                if captcha != None:
                    # Сохраняем сообщение с капчей для последующего нажатия кнопки
                    self.pending_captcha_messages[request_id] = response
                    response = captcha
                return response        
              
                
              
        except asyncio.TimeoutError:  
            logging.warning(f"Timeout waiting for WALLET_BOT response for request {request_id}")  
            raise

    async def handle_captcha_solution(self, command: str) -> bool:
        """Обработка решения капчи: находит сохраненное сообщение и нажимает кнопку.
        
        Args:
            command: Команда вида "[REQ_abc123] /solve_captcha button_text"
            
        Returns:
            True если успешно нажали кнопку, False иначе
        """
        req_match = re.match(r'\[REQ_(\w+)\]\s*/solve_captcha\s+(.+)', command.strip())
        if not req_match:
            return False
        
        request_id = req_match.group(1)
        solution = req_match.group(2).strip()
        
        # Найти сохраненное сообщение с капчей
        msg = self.pending_captcha_messages.get(request_id)
        if not msg:
            logging.warning(f"No captcha message found for request {request_id}")
            return False
        
        try:
            # Попробовать найти кнопку по тексту или callback_data
            if hasattr(msg, 'reply_markup') and msg.reply_markup:
                buttons = msg.reply_markup.rows if hasattr(msg.reply_markup, 'rows') else []
                
                for row_idx, row in enumerate(buttons):
                    for btn_idx, btn in enumerate(row):
                        btn_text = getattr(btn, 'text', '')
                        btn_data = getattr(btn, 'data', b'').decode() if hasattr(btn, 'data') else ''
                        
                        # Проверяем по тексту или по callback_data
                        if solution.lower() in btn_text.lower() or solution.lower() in btn_data.lower():
                            # Нажимаем кнопку
                            await msg.click(row_idx, btn_idx)
                            # Удаляем из памяти после использования
                            self.pending_captcha_messages.pop(request_id, None)
                            return True
                
                # Если не нашли точное совпадение, пробуем по индексу
                try:
                    idx = int(solution) - 1
                    await msg.click(idx)
                    self.pending_captcha_messages.pop(request_id, None)
                    return True
                except (ValueError, IndexError):
                    pass
            
            logging.warning(f"Could not find button matching solution '{solution}' in captcha message {request_id}")
            return False
            
        except Exception as e:
            logging.error(f"Error clicking captcha button: {e}")
            return False

    # Эта функция отправляет сообщение с кнопками и ждет ответа пользователя
    async def _prompt_buttons_and_wait(self, requester, msg, buttons, timeout=120):
        # requester: telethon.tl.custom.user.User = to_entity(requester)
        req = to_entity(requester)
        # forward media if present
        try:
            if msg and getattr(msg, 'media', None):
                await safe_forward(self.client, req, msg)
        except Exception:
            pass

        # choices_text - сформировать текст с вариантами кнопок
        choices_text = '\n'.join([f"{i+1}. {t}" for i, t in enumerate(buttons)])
        await self.client.send_message(req, f"Выберите кнопку (ответьте номером или текстом):\n{choices_text}")

        # создать Future и сохранить его в pending_button_prompts
        fut = asyncio.get_event_loop().create_future()
        self.pending_button_prompts[str(req)] = {'future': fut, 'msg': msg, 'buttons': buttons}

        # ждать ответа или таймаута
        try:
            #res - ответ пользователя
            #fut - Future, который будет установлен при получении ответа
            res = await asyncio.wait_for(fut, timeout=timeout)
            return res
        except asyncio.TimeoutError:
            return None
        finally:
            self.pending_button_prompts.pop(str(req), None)

    async def process_flow(self, raw_text: str, requester):
        #text - очищенный текст сообщения
        text = raw_text.strip()
        # req - сущность пользователя, отправившего запрос
        req = to_entity(requester)

        if text.startswith('/balance') or 'balance' in text.lower():
            #resp_msg, info - send_command_to_wallet - отправляет команду WALLET_BOT и ждет ответа
            resp_msg, info = await self.send_command_to_wallet('/balance', wait_for_response=True, timeout=30)
            if resp_msg is None:
                await self.client.send_message(req, 'WALLET_BOT не ответил')
                return

            if info.get('has_media') and resp_msg:
                # попытка переслать медиа
                forwarded = await safe_forward(self.client, req, resp_msg)
                if not forwarded:
                    await self.client.send_message(req, info.get('text') or '')
            else:
                await self.client.send_message(req, info.get('text') or '')

            buttons = info.get('buttons') or []
            if buttons:
                #choice - ждем выбора кнопки от пользователя, timeout 120 секунд
                choice = await self._prompt_buttons_and_wait(requester, resp_msg, buttons, timeout=120)
                if choice is None:
                    await self.client.send_message(req, 'Таймаут выбора кнопки')
                    return
                try:
                    # choice may be int or text
                    if isinstance(choice, int):
                        idx = choice - 1
                        await resp_msg.click(idx)
                    else:
                        #idx - найти индекс кнопки по тексту
                        idx = None
                        for i, t in enumerate(buttons):
                            #str делится и занижается и равно выбору пользователя
                            if str(t).strip().lower() == str(choice).strip().lower():
                                idx = i
                                break
                        # если нашли индекс, кликаем по кнопке
                        if idx is not None:
                            await resp_msg.click(idx)
                        else:
                            # fallback: send choice text to wallet
                            await self.client.send_message(to_entity(WALLET_BOT), str(choice))

                    await self.client.send_message(req, 'Кнопка нажата')
                except Exception as e:
                    await self.client.send_message(req, f'Ошибка при нажатии кнопки: {e}')
            return

        # default path: forward arbitrary text to wallet and relay response
        # resp_msg, info - отправляем текст WALLET_BOT и ждем ответа
        resp_msg, info = await self.send_command_to_wallet(text, wait_for_response=True, timeout=30)
        if resp_msg:
            if info.get('has_media'):
                await safe_forward(self.client, req, resp_msg)
            else:
                await self.client.send_message(req, info.get('text') or '')
        
    async def send_somthing(self, text, ADMIN_ID):
        if text == 'Who let the dogs out?':
            return await self.client.send_message(to_entity(ADMIN_ID), "Who, who, who, who?")
    
    '''
    async def get_bot_message_history(self, text, WALLET_BOT, limit: int = None):
        if text != 'get_history':
            return None
        
        messages = []  
        async for message in self.client.iter_messages(to_entity(WALLET_BOT), limit=limit or 10):
            messages.append({  
                'id': message.id,  
                'date': message.date,  
                'text': message.text,  
                'from_bot': message.from_id is not None and message.from_id.user_id == bot_id if message.from_id else False  
            })
        pass
        
    async def send_command_to_wallet(self, command: str, wait_for_response: bool = True, timeout: int = 30):
        # target to_entity - это может быть username или user_id
        target = to_entity(WALLET_BOT)
        if target is None:
            raise RuntimeError('WALLET_BOT not configured')

        await self.client.send_message(target, command)

        if not wait_for_response:
            return None, {'text': None, 'buttons': [], 'has_media': False, 'message': None}

        # wait for response from WALLET_BOT
        try:
            ev = await self.client.wait_for(events.NewMessage(from_users=target), timeout=timeout)
        except asyncio.TimeoutError:
            return None, {'text': None, 'buttons': [], 'has_media': False, 'message': None}

        # parse response
        # msg: telethon.tl.custom.message.Message = ev.message
        msg = ev.message
        info = {
            'text': msg.message or '',
            'buttons': extract_buttons(msg),
            'has_media': bool(getattr(msg, 'media', None)),
            'message': msg,
        }
        return msg, info
    '''
