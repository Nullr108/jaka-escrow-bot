import re
from telethon import events
import logging
from .config import OUTER_BOT, WALLET_BOT, ADMIN_IDS
from .utils import to_entity
from .flow import TelegramFlow


def register_handlers(client, flow: TelegramFlow):
    @client.on(events.NewMessage())
    async def _on_new_message(event):
        try:
            #sender_id - получить идентификатор отправителя сообщения
            sender_id = getattr(event.message, 'sender_id', None)
            # sender_str - строковое представление идентификатора отправителя
            sender_str = str(sender_id) if sender_id is not None else None
            # raw - текст сообщения
            raw = event.message.message or ''

            # If message from OUTER_BOT (regular bot)
            if OUTER_BOT and sender_str == str(OUTER_BOT):
                if raw.strip().startswith('[REQ_'):
                    req_number = re.search(r'\[REQ_(\w+)\]', raw).group(1)
                    if 'get_history' in raw:
                        history = await flow.get_bot_message_history(WALLET_BOT, limit=10)
                        await client.send_message(to_entity(OUTER_BOT), f'History:\n{history}')
                        return
                    
                    if '/balance' in raw:
                        response = await flow.send_wallet_command(raw, OUTER_BOT)
                        await client.send_message(to_entity(OUTER_BOT), f'REQ_{req_number} {response}')
                        return
                    
                    if 'get_last_message' in raw:
                        # Extract request_id if present
                        req_match = re.search(r'\[REQ_(\w+)\]', raw)
                        request_id = req_match.group(1) if req_match else None
                        try:
                            msgs = await client.get_messages(to_entity(WALLET_BOT), limit=1)
                            # msgs возвращает объект Message или список, проверяем оба случая
                            if msgs:
                                # Если это объект Message (не список)
                                if hasattr(msgs, 'message'):
                                    last_msg_text = msgs[0].message
                                # Если это список
                                elif isinstance(msgs, list) and len(msgs) > 0 and hasattr(msgs[0], 'message'):
                                    last_msg_text = msgs[0].message
                                else:
                                    last_msg_text = "No messages found"
                            else:
                                last_msg_text = "No messages found"
                            
                            # Send response back with request_id marker if present
                            if request_id:
                                response_text = f"[REQ_{request_id}] {last_msg_text}"
                            else:
                                response_text = last_msg_text
                            
                            await client.send_message(to_entity(OUTER_BOT), response_text)
                        except Exception as e:
                            error_msg = f"Error fetching last message: {str(e)}"
                            if request_id:
                                response_text = f"[REQ_{request_id}] {error_msg}"
                            else:
                                response_text = error_msg
                            await client.send_message(to_entity(OUTER_BOT), response_text)
                        return
                    
                    if '/btc' in raw:
                        response = await flow.send_wallet_command(raw)
                        if type(response) != str:
                            await client.send_message(
                                to_entity(OUTER_BOT),
                                f'[REQ_{req_number}]',
                                file=response.media,
                                caption=f'[REQ_{req_number}]',
                                buttons=response.reply_markup)
                            return
                        
                    if '/solve_captcha' in raw:
                        # Обработать решение капчи - нажать кнопку в WALLET_BOT
                        success = await flow.handle_captcha_solution(raw)
                        if success:
                            await client.send_message(to_entity(OUTER_BOT), f'[REQ_{req_number}] solved')
                        else:
                            await client.send_message(to_entity(OUTER_BOT), f'[REQ_{req_number}] error: failed to click button')
                        return
                else:
                    if 'Who let the dogs out?' in raw:
                        await flow.send_somthing(raw, ADMIN_IDS[0])
                        return
                    
                '''
                пример команды с маркером [REQ_*]:
                [REQ_12345] balance
                '''

                # if pending prompt exists for this requester, resolve
                pending = flow.pending_button_prompts.get(sender_str)
                #проверка наличия ожидающего запроса
                if pending and not pending['future'].done():
                    txt = raw.strip()
                    if txt.isdigit():
                        pending['future'].set_result(int(txt))
                    else:
                        pending['future'].set_result(txt)
                    return

                # otherwise handle new command
                await flow.process_flow(raw, OUTER_BOT)
                return

            # If message from WALLET_BOT
            if WALLET_BOT and sender_str == str(WALLET_BOT):
                # Check if there are pending wallet API responses waiting
                for request_id, pending_info in flow.pending_wallet_responses.items():
                    if not pending_info['future'].done():
                        # Resolve the first pending request with this message
                        pending_info['future'].set_result(event.message)
                        return

                # If WALLET_BOT sends unexpected message, forward to OUTER_BOT as notification placeholder
                if OUTER_BOT:
                    try:
                        await client.forward_messages(to_entity(OUTER_BOT), event.message)
                    except Exception:
                        pass
                return

            return
        except Exception:
            return
