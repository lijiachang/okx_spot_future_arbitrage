import asyncio
import json
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram Bot.
    Docs: https://core.telegram.org/api
    获取chat_id: https://api.telegram.org/botXXX:YYYY/getUpdates
    """

    BASE_URL = "https://api.telegram.org"

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    async def send_text_msg(self, content: str) -> Optional[dict]:
        url = "{base_url}/bot{token}/sendMessage?chat_id={chat_id}&text={content}".format(
            base_url=self.BASE_URL, token=self.token, chat_id=chat_id, content=content
        )
        logger.info(f"TelegramBot send_text_msg {url}")
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(url, timeout=10) as resp:
                    resp_text = await resp.text()
                    return json.loads(resp_text)
        except Exception as e:
            logger.error(f"TelegramBot send_text_msg failed:{url}, {e}")
            return None


if __name__ == "__main__":
    token = "6464780082:AAGjSMHcXK2sOuzuYBjBmjWVAdU3DqM5B0c"
    chat_id = "1829233245"
    content = "Hello, World!"
    asyncio.run(TelegramBot(token, chat_id).send_text_msg(content))
