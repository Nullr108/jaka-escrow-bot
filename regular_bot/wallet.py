import asyncio
import uuid
import re
import logging
from typing import Dict, Any
from aiogram import Bot
from aiogram.types import Message
from telethon import TelegramClient
from telethon.tl.types import PeerUser
from aiogram import Router
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from regular_bot.config import WALLET_BOT, INNER_BOT, ADMIN_IDS
from regular_bot.utils import to_entity

logger = logging.getLogger(__name__)

# В памяти храним pending responses: request_id -> asyncio.Future
pending_responses: Dict[str, asyncio.Future] = {}


class TelethonWalletAPI:
    """Adapter that sends text commands to the wallet bot via telethon_bot intermediary.

    Flow:
    1. This bot (regular_bot via aiogram) sends command (with request_id) to OUTER_BOT (telethon_bot)
    2. telethon_bot forwards it to WALLET_BOT as plain text
    3. WALLET_BOT replies to telethon_bot with response text
    4. telethon_bot relays the response back to OUTER_BOT (this bot)
    5. This bot receives it via wallet_response_listener, extracts request_id, and resolves the Future

    Command format: "[REQ_<request_id>] <command> <params>"
    Response format: "[REQ_<request_id>] <response_text>"
    """
    def __init__(self, bot: Bot, router: Router, client: TelegramClient):
        self.bot = bot
        self.router = router
        self.client = client

    async def telethon_req(self, action:str, message: Message, state:FSMContext, buyer_id = None, amount = None):
        try:
            if action!='send_crypto':
                async with self.client.conversation(to_entity(WALLET_BOT), timeout=10) as conv:
                    await conv.send_message(f"{action}")
                    response = await conv.get_response()

                    if response.media is not None:
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

                        await state.update_data(
                            button_texts=button_texts,
                            response=response,
                            conv=conv,
                            prev_state=state.get_state())
                        await state.set_state("waiting_btc_button")

                        await message.answer_photo(  
                            photo=photo,  
                            reply_markup=keyboard  
                        )
                        return None
                    
                    else:
                        if action=="/btc":
                            msg = response.message
                            lines = msg.splitlines()
                            msg = ''.join(lines[0:3])
                            logger.info(f'{msg}')
                            return msg
                        if action=='/balance':
                            return response.message
                        
        except Exception as e:
            logger.error("Error in _on_message", exc_info=e)

        try:
            if action=='send_crypto':
                entity = await self.client.get_entity(buyer_id)
                username = entity.username
                async with self.client.conversation(to_entity(WALLET_BOT), timeout=10) as conv:
                    amount = str(amount)[:-2]
                    await conv.send_message(f"Перевод @{username} {amount}")
                    response = await conv.get_response()
                    if response.reply_markup is not None:
                        await response.click(text='✅Подтверждаю')
            return "skip"
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
        
    async def send_command(self, command: str, params: Dict[str, Any] = None, timeout: int = 30) -> str:
        request_id = uuid.uuid4().hex[:8]  # short unique ID
        #requset_id - уникальный идентификатор запроса, формируется с помощью uuid(это случайная строка).uuid4(это версия 4 uuid).hex(преобразование в шестнадцатеричную строку)[:8](берём первые 8 символов)
        # Format command with request_id marker
        if params:
            params_str = ' '.join(f"{k}={v}" for k, v in params.items())
            text = f"[REQ_{request_id}] {command} {params_str}"
        else:
            text = f"[REQ_{request_id}] {command}"

        fut = asyncio.get_event_loop().create_future()
        pending_responses[request_id] = fut

        # Send command to OUTER_BOT (telethon_bot) which will forward to WALLET_BOT
        await self.bot.send_message(INNER_BOT, text)
        return await asyncio.wait_for(fut, timeout=timeout)
        
    async def get_wallet_address(self) -> str:
        """Get wallet address from WALLET_BOT."""
        res = await self.send_command('get_address', {})
        # Extract address from response (e.g., "Address: 1A1z7agoat...")
        match = re.search(r'(?:address|Address|адрес)[\s:]+(\S+)', res, re.IGNORECASE)
        return match.group(1) if match else res.strip()

    async def get_transaction(self, tx_hash: str) -> Dict[str, Any]:
        """Get transaction details (outputs, confirmations)."""
        res = await self.send_command('get_tx', {'tx_hash': tx_hash})
        # Parse response: expected format "outputs: [...] confirmations: N"
        result = {'text': res, 'outputs': [], 'confirmations': 0}
        
        # Try to extract confirmations
        conf_match = re.search(r'(?:confirmations|conf)[\s:]*(\d+)', res, re.IGNORECASE)
        if conf_match:
            result['confirmations'] = int(conf_match.group(1))
        
        # Try to extract output addresses and amounts
        # This is simplified; WALLET_BOT should provide structured response
        output_matches = re.findall(r'(\S+?)\s*[:-]\s*([\d.]+)', res)
        for addr, amount in output_matches:
            result['outputs'].append({'address': addr, 'value': float(amount)})
        
        return result

    async def wait_for_confirmations(self, tx_hash: str, min_confirmations: int = 1, timeout: int = 300) -> bool:
        """Wait for transaction to reach min_confirmations."""
        try:
            res = await self.send_command('wait_confirm', {'tx_hash': tx_hash, 'min_confirmations': min_confirmations}, timeout=timeout)
            # Check if response indicates success (contains "ok", "confirmed", or similar)
            return bool(re.search(r'(?:ok|success|confirmed|ready)', res, re.IGNORECASE))
        except asyncio.TimeoutError:
            return False

    async def send_to(self, address: str, amount: float) -> Dict[str, Any]:
        """Send crypto to address."""
        res = await self.send_command('send_to', {'address': address, 'amount': amount}, timeout=60)
        # Parse response: expected to contain txid/tx_hash
        result = {'text': res}
        
        # Try to extract txid
        txid_match = re.search(r'(?:txid|tx_hash|hash)[\s:]*(\S+)', res, re.IGNORECASE)
        if txid_match:
            result['txid'] = txid_match.group(1)
        
        return result

    async def get_bot_message_history(self, WALLET_BOT, limit: int = None): 
        history = await self.bot.send_message(INNER_BOT, f'/get_history {WALLET_BOT} {limit or "all"}')
        return history

    async def get_courses(self):
        """Get list of courses."""
        request_id = uuid.uuid4().hex[:8]
        text = f"[REQ_{request_id}] /btc"

        fut = asyncio.get_event_loop().create_future()
        pending_responses[request_id] = fut

        response: Message = await self.send_command(command='/btc')
        return response

    async def get_last_message_from_wallet(self, timeout: int = 30) -> str:
        """Get last message from WALLET_BOT via telethon_bot intermediary.
        
        Flow:
        1. Send command with request_id marker to INNER_BOT
        2. telethon_bot processes 'get_last_message' and fetches last message from WALLET_BOT
        3. telethon_bot sends response back with [REQ_*] marker
        4. wallet_response_listener intercepts and resolves the Future
        
        Args:
            timeout: Maximum time to wait for response (seconds)
            
        Returns:
            Text of the last message from WALLET_BOT
            
        Raises:
            asyncio.TimeoutError: If no response within timeout
            Exception: If response contains an error
        """
        request_id = uuid.uuid4().hex[:8]
        text = f"[REQ_{request_id}] get_last_message"
        
        fut = asyncio.get_event_loop().create_future()
        pending_responses[request_id] = fut
        
        try:
            # Send command to INNER_BOT (telethon_bot)
            await self.bot.send_message(INNER_BOT, text)
            
            # Wait for response
            response = await asyncio.wait_for(fut, timeout=timeout)
            logger.info(f'{response}')
            
            # Handle response structure
            if isinstance(response, dict):
                if 'error' in response:
                    raise Exception(f"Error from telethon_bot: {response['error']}")
                return response.get('response', '')
            
            return str(response)
            
        except asyncio.TimeoutError:
            pending_responses.pop(request_id, None)
            raise asyncio.TimeoutError(f"No response from telethon_bot for get_last_message within {timeout}s")
        except Exception as e:
            pending_responses.pop(request_id, None)
            raise

async def wallet_response_listener(message: Message) -> None:
    """Listen for responses from telethon_bot (relayed from WALLET_BOT) and route to futures.

    telethon_bot forwards responses from WALLET_BOT to this bot (regular_bot).
    Expected format: "[REQ_<request_id>] <response_text>"
    
    If the message doesn't match this format, returns False to allow other handlers to process it.
    """
    try:
        if not message.from_user or str(message.from_user.id) != str(INNER_BOT):
            return  # Not from telethon_bot, skip this listener
        
        text = (message.text or message.caption or '').strip()
        if not text:
            return  # No text content
        
        # Check if this is a wallet API response with [REQ_*] marker
        match = re.match(r'\[REQ_(\w+)\]\s*(.*)', text)
        if not match:
            return  # Not a wallet API response, let other handlers process it
        
        request_id = match.group(1)
        response_text = match.group(2)
        
        fut = pending_responses.get(request_id)
        if fut and not fut.done():
            # Determine if response contains error
            if re.search(r'(?:error|failed|fail|exception)', response_text, re.IGNORECASE):
                result = {'error': response_text, 'response': response_text}
            else:
                result = {'response': response_text}
            fut.set_result(result)
    except Exception:
        return
