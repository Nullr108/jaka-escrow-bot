from typing import List, Any


def to_entity(value: Any):
    """Return an entity suitable for Telethon send/forward (int id or string username)."""
    # Преобразует значение в int, если это возможно, иначе возвращает строку
    if value is None:
        return None
    s = str(value)
    if s.isdigit():
        return int(s)
    return s


def extract_buttons(msg) -> List[str]:
    """Extract inline button texts from a Telethon message into a flat list."""
    buttons = []
    try:
        if getattr(msg, 'buttons', None):
            for row in msg.buttons:
                for b in row:
                    buttons.append(getattr(b, 'text', str(b)))
    except Exception:
        pass
    return buttons


async def safe_forward(client, target, msg):
    """Forward `msg` to `target` with exception handling."""
    try:
        await client.forward_messages(target, msg)
        return True
    except Exception:
        return False
