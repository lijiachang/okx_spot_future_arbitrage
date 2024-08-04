"""
orderbook管理类
"""
import logging
from dataclasses import dataclass
from typing import Literal

import aioredis
import msgpack

from tools.instruments import get_subject_by

logger = logging.getLogger(__name__)


@dataclass
class OrderBookInfo:
    instrument_name: str
    side: Literal["asks", "bids"]
    depth: int  # 深度 从1开始
    price: float
    expire_days: int | None  # 合约交割剩余天数, 现货为None
    data_ms: int  # 数据时间


class OrderBookManager:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def get_orderbook(self, instrument_name: str) -> dict:
        currency = instrument_name.split("-")[0]
        subject = get_subject_by(instrument_name)
        key = f"EXECUTE_ENGINE.SPIDER.OKEX.{subject}.{currency}.{instrument_name}.BOOK"
        data = msgpack.unpackb(await self.redis.get(key))
        logger.debug(f"get orderbook {instrument_name}: {data}")
        return data

    async def get_price(self, instrument_name: str, side: Literal["asks", "bids"], depth: int = 1) -> float:
        data = await self.get_orderbook(instrument_name)
        depth_index = depth - 1
        return float(data[side][depth_index][0])

    async def get_orderbook_info(
        self, instrument_name: str, side: Literal["asks", "bids"], depth: int = 1
    ) -> OrderBookInfo:
        data = await self.get_orderbook(instrument_name)
        depth_index = depth - 1
        return OrderBookInfo(
            instrument_name=instrument_name,
            side=side,
            depth=depth,
            price=float(data[side][depth_index][0]) if data[side] else 0,
            expire_days=data["expiration_days"],
            data_ms=data["data_ms"],
        )
