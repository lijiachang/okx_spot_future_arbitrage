import asyncio
import json
import logging
from functools import partialmethod

import aiohttp
from aiohttp.web import HTTPException

logger = logging.getLogger(__name__)


class HTTPClientBase:
    def base_http_url(self) -> str:
        raise NotImplementedError

    async def http(self, endpoint, *args, method="get", **kwargs):
        url = f"{self.base_http_url()}{endpoint}"
        logger.info(f"{method.upper()} {url}, {args}, {kwargs}")
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with getattr(session, method)(url, *args, timeout=10, **kwargs) as resp:
                    resp_text = await resp.text()
                    if method == "post":
                        logger.info(f"=> {resp_text}")
                    return json.loads(resp_text)
        except HTTPException as e:
            logger.error(f"execute_api_call failed: {method} {url} code:{e.status_code}, {e}")
            return {"http_error": f"code:{e.status_code}"}
        except asyncio.exceptions.TimeoutError:
            logger.error(f"execute_api_call timeout: {method} {url}")
            return {"http_error": "timeout"}

    get = partialmethod(http, method="get")
    post = partialmethod(http, method="post")
