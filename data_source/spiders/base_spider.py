import asyncio
import functools
import json
import logging
import time
from itertools import cycle
from typing import Optional, Set

import aioredis
import msgpack
import websockets
from django.conf import settings
from order_book import OrderBook as C_OrderBook  # https://github.com/bmoscon/orderbook

from common.topic import generate_data_source_topic
from data_source.exchange_info import InstrumentInfoManager
from tools.instruments import EEInstrument, get_subject_by
from tools.qps_calculator import QpsCalculator
from tools.throttling import Throttling
from tools.time_parse import get_expired_from_instrument_name

logger = logging.getLogger(__name__)


class Trade:
    def __init__(
        self,
        trade_seq=None,
        date_ms=None,
        price=None,
        mark_price=None,
        index_price=None,
        instrument_name=None,
        direction=None,
        amount=None,
        iv=None,
    ):
        self.trade_seq = trade_seq
        self.date_ms = date_ms
        self.price = price
        self.mark_price = mark_price
        self.instrument_name = instrument_name
        self.direction = direction
        self.amount = amount
        self.iv = iv

    def to_json(self):
        return dict(
            trade_seq=self.trade_seq,
            date_ms=self.date_ms,
            price=self.price,
            mark_price=self.mark_price,
            instrument_name=self.instrument_name,
            direction=self.direction,
            amount=self.amount,
            iv=self.iv,
        )


class FastOrderBook(C_OrderBook):
    def __init__(
        self,
        instrument_name,
        bids: dict,
        asks: dict,
        change_id,
        timestamp,
        greeks=None,
        mark_price=None,
        mark_iv=None,
        bid_iv=None,
        ask_iv=None,
        connection_id=None,
        max_depth: int = None,
    ):
        if max_depth:
            super().__init__(max_depth=max_depth)
        else:
            super().__init__()

        self.instrument_name = instrument_name
        self.expired_date = get_expired_from_instrument_name(instrument_name)
        self.timestamp = timestamp
        self.change_id = change_id
        self.greeks = greeks
        self.mark_price = mark_price
        self.mark_iv = mark_iv
        self.bid_iv = bid_iv
        self.ask_iv = ask_iv
        self.bids = bids
        self.asks = asks
        self._connection_id = connection_id

    def __setattr__(self, key, value):
        if key in ("bids", "asks"):
            super().__setattr__(key, value)
        else:
            self.__dict__[key] = value

    def to_json(self, level=0):
        asks = self.asks.to_list(level) if level else self.asks.to_list()
        bids = self.bids.to_list(level) if level else self.bids.to_list()
        result = {
            "instrument_name": self.instrument_name,
            "asks": asks,
            "bids": bids,
            "due_time": self.expired_date.timestamp() if self.expired_date else 0,
            "data_ms": self.timestamp,  # orderbook更新时间
            "msg_ms": int(time.time() * 1000),  # 消息发出时间
            "greeks": self.greeks,
            "mark_price": self.mark_price,
            "mark_iv": self.mark_iv or 0,
            "bid_iv": self.bid_iv or 0,
            "ask_iv": self.ask_iv or 0,
            "connection_id": self._connection_id,
        }
        return result

    def __str__(self):
        result = self.to_json()
        return json.dumps(result)

    def __bool__(self):
        return True


class NewOrderBookManager:
    def __init__(self, orderbook_max_depth=None):
        self.orderbook_max_depth = orderbook_max_depth
        self.orderbooks = {
            # instrument_name: orderbook
        }
        self.tickers = {
            # instrument_name: ticker data
        }
        self._connection_id = int(time.time() * 1000)

    def snapshot(self, instrument_name: str, pure_bids: dict, pure_asks: dict, change_id, timestamp):
        self.orderbooks[instrument_name] = FastOrderBook(
            instrument_name,
            pure_bids,
            pure_asks,
            change_id,
            timestamp,
            connection_id=self._connection_id,
            max_depth=self.orderbook_max_depth,
        )

    def change_ticker(self, instrument_name, greeks, mark_price, mark_iv, bid_iv, ask_iv):
        """修改报价相关数据
        特别的，mark_iv 需要的是 * 100 后的数值，因为 Deribit 发来的已经 * 100。
        """
        orderbook = self.orderbooks.get(instrument_name)
        if orderbook is None:
            # logger.info(f"return {instrument_name} due to no orderbook")
            return
        orderbook.greeks = greeks
        orderbook.mark_price = mark_price
        orderbook.mark_iv = mark_iv
        orderbook.bid_iv = bid_iv
        orderbook.ask_iv = ask_iv

    def __getitem__(self, key):
        return self.orderbooks[key]

    def get(self, key, default=None):
        return self.orderbooks.get(key, default)


class BaseWSClient:
    TIMEOUT_THRESHOLD = 5
    TIMEOUT_KILLER_COOLDOWN = 25
    TIMEOUT_KILLER_PERIOD = 5
    MESSAGE_DELAY = 0.1

    EXCHANGE: str = None

    def __init__(self, *args, zmq_port=None, **kwargs):
        self.main_task = None
        self.gen_cycle_queue_cache = None
        self.queue_object_caches: list = []
        self.map_instrument_queue = {}  # {instrument_name: queue_object}
        self.orderbook_manager = None
        self.zmq_publisher = None
        self.last_updated_at = time.time()

        self.mark_price_at = {}
        self.kline_at = {}
        self.underlying_price_at = {}
        self.expiration_at = {}
        self.expiration_days = {}
        self.book_published_at = {}
        self._throtting = Throttling()
        self.qps_calculator = QpsCalculator(f"{self.EXCHANGE}_book")
        self.redis: Optional[aioredis.Redis] = None
        self.topic_cache: Set[str] = set()
        self.instrument_info: Set[InstrumentInfoManager] = set()
        self.setup_task: Optional[asyncio.Task] = None

    def cache_topic(self, topic: str):
        self.topic_cache.add(topic)
        return

    def cache_instrument(self, info: EEInstrument):
        self.instrument_info.add(InstrumentInfoManager(f"{self.EXCHANGE}", info))
        return

    def get_subject_type(self, instrument_name):
        return get_subject_by(instrument_name)

    @functools.lru_cache(maxsize=4096)
    def build_topic(self, instrument_name: str, data_type="book", subject_type=None):
        if data_type == "index_price" and "_" in instrument_name:
            currency = instrument_name.split("_")[0]  # index price 的 instrument_name 形如 BTC_USD
        else:
            currency = instrument_name.split("-")[0]
        return generate_data_source_topic(
            self.EXCHANGE,
            subject_type or self.get_subject_type(instrument_name),
            currency,
            instrument_name,
            data_type=data_type,
        )

    async def periodic_task(self):
        """
        周期性任务，获取 k 线计算最近交易量等
        """
        pass

    def get_url(self) -> str:
        pass

    async def async_get_url(self) -> str:
        return ""

    async def setup(self):
        """
        订阅初始化操作
        """
        pass

    async def pong(self, ts):
        pass

    async def dispatch_message(self, message):
        pass

    async def handler(self):
        async for message in self.websocket:
            try:
                await self.dispatch_message(message)
            except Exception as e:
                logger.exception(str(e), exc_info=True, stack_info=True)

    @classmethod
    async def create(cls, *args, **kwargs):
        self = cls(*args, **kwargs)
        self.redis = await aioredis.from_url(f"{settings.REDIS_URL}")
        return self

    def create_ws_connection(self, url, max_size=2**20 * 10, **kwargs):
        return websockets.connect(url, max_size=max_size, **kwargs)

    async def start(self):
        """
        1.创建ws连接
        2.订阅初始化
        3.接收消息
        """
        url = self.get_url()
        if not url:
            url = await self.async_get_url()
        logger.info(f"start:{url}")
        try:
            async with self.create_ws_connection(url, max_size=2**20 * 10) as websocket:
                self.orderbook_manager = NewOrderBookManager()
                logger.info("connection established")
                self.websocket = websocket
                if self.setup_task:
                    logger.info("cancel last setup task")
                    self.setup_task.cancel()  # 取消上次的setup任务，防止setup未完成，导致重复创建任务
                self.setup_task = asyncio.create_task(self.setup())
                self.setup_task.add_done_callback(lambda task: task.result())
                await self.handler()
        except Exception as e:
            with self._throtting as got_token:
                if got_token:
                    logger.exception(str(e), exc_info=True)

    async def timeout_killer(self):
        while True:
            # self._clear_book_cache()
            await asyncio.sleep(self.TIMEOUT_KILLER_PERIOD)
            timeout = time.time() - self.last_updated_at
            # logger.warning(f"timeout: {timeout}")
            if timeout > self.TIMEOUT_THRESHOLD:
                try:
                    logger.info("websocket not fresh, kill it!")
                    cancel_success = self.main_task.cancel()
                    if hasattr(self, "websocket"):
                        await self.websocket.close()
                    logger.info(f"websocket not fresh, kill result: {cancel_success}")
                    logger.info(f"now killer will sleep {self.TIMEOUT_KILLER_COOLDOWN}s")
                    await asyncio.sleep(self.TIMEOUT_KILLER_COOLDOWN)
                except Exception as e:
                    logger.exception(str(e), exc_info=True)

    @classmethod
    async def run(cls, *args, **kwargs):
        """
        启动任务
        1.创建周期性任务循环
        2.启动主任务，该任务在一个循环内，在特殊情况下，可以中止该任务，触发重新启动
        """
        loop = asyncio.get_event_loop()
        client = await cls.create(*args, **kwargs)
        client.loop = loop
        loop.create_task(client.periodic_task())
        loop.create_task(client.timeout_killer())
        while True:
            try:
                client.main_task = loop.create_task(client.start())
                await client.main_task
            except BaseException as e:
                logger.exception(e, exc_info=True)
                logger.info("canceled!")
            await asyncio.sleep(1)

    async def publish_mark_price(self, instrument_name, mark_price):
        now = time.time()
        if (instrument_name not in self.mark_price_at) or (now - self.mark_price_at[instrument_name]) >= 1:
            topic = self.build_topic(instrument_name, data_type="mark_price")
            # SWAP do not have the expiration_at
            expiration_at = self.expiration_at.get(instrument_name)
            payload = dict(
                instrument_name=instrument_name, mark_price=mark_price, ms=now * 1000, expiration_at=expiration_at
            )
            await self._publish(topic, payload)
            self.mark_price_at[instrument_name] = now

    async def publish_underlying_price(self, instrument_name, underlying_price):
        now = time.time()
        if instrument_name not in self.underlying_price_at or (now - self.underlying_price_at[instrument_name]) >= 1:
            topic = self.build_topic(instrument_name, data_type="underlying_price")
            expiration_at = self.expiration_at.get(instrument_name)
            payload = dict(
                instrument_name=instrument_name,
                underlying_price=underlying_price,
                ms=now * 1000,
                expiration_at=expiration_at,
            )
            await self._publish(topic, payload)
            self.underlying_price_at[instrument_name] = now

    async def _publish_book(self, topic, packed_payload, instrument_name=None, **kwargs):
        now = time.time()
        self.book_published_at[topic] = now
        await self._publish(topic, packed_payload, **kwargs)

    async def publish_book(self, topic, payload):
        """发布新的盘口快照"""
        # logger.info(f"topic: {topic}, book: {payload}")
        instrument_name = payload["instrument_name"]
        payload["expiration_at"] = self.expiration_at.get(instrument_name)
        payload["expiration_days"] = self.expiration_days.get(instrument_name)
        fut = self.fut_publish_book(topic, payload, instrument_name)
        await self.task_dispatch(instrument_name, fut)

    async def fut_publish_book(self, topic, payload, instrument_name):
        self.qps_calculator.incr()
        packed_payload = msgpack.packb(payload)
        # await self._publish_book(topic, packed_payload, packed=True, data_ms=payload['data_ms'])
        await self._set_cache(topic, packed_payload)

    async def consumer_worker(self, queue):
        while True:
            fut = await queue.get()
            try:
                await fut
            except Exception as e:
                logger.exception(str(e), exc_info=True, stack_info=True)
            finally:
                queue.task_done()

    async def task_dispatch(self, instrument_name, fut):
        if instrument_name in self.map_instrument_queue:
            await self.map_instrument_queue[instrument_name].put(fut)
        else:
            # 限制consumer协程数
            if len(self.queue_object_caches) < settings.SPIDER_CONSUMER_WORKERS:
                queue = asyncio.Queue(maxsize=settings.SPIDER_WEBSOCKET_MESSAGE_QUEUE_MAX_SIZE)
                asyncio.create_task(self.consumer_worker(queue))  # 新建一个消费者协程，并绑定一个queue
                self.queue_object_caches.append(queue)
            else:  # 复用consumer
                if not self.gen_cycle_queue_cache:
                    self.gen_cycle_queue_cache = cycle(self.queue_object_caches)  # 循环生成：已缓存的消费者queue
                queue = next(self.gen_cycle_queue_cache)

            self.map_instrument_queue[instrument_name] = queue
            await queue.put(fut)

    async def publish_kline(self, topic, payload):
        """
        发布新的kline
        """
        now = int(time.time())
        if topic not in self.kline_at or (now - self.kline_at[topic]) >= 1:
            await self._publish(topic, payload)
            self.kline_at[topic] = now
            # logger.info("publish kline topic %s, payload %s", topic, payload)

    async def _set_cache(self, topic, packed_payload):
        await self.redis.set(topic, packed_payload)
        # logger.info("book cache:%s", topic)

    async def _publish(self, topic, payload, packed=False, **kwargs):
        # if self.zmq_publisher:
        #     # sync call, quick
        #     self.zmq_publisher.send(topic, payload, packed=packed)
        # if self.producer:
        #     await self.producer.send(topic, payload, expiration=5 * 60,
        #                              need_throttle=False, packed=packed,
        #                              **kwargs)
        self.cache_topic(topic)

    async def publish_trade(self, topic, payload):
        await self._publish(topic, payload)

    async def publish_index_price(self, index_name, index_price, ms=None, **extra):
        """
        extra: 额外需要携带的信息，比如永续合约的funding fee
        """
        topic = self.build_topic(index_name, data_type="index_price", subject_type="index")
        ms = ms or time.time() * 1000
        payload = dict(index_name=index_name, index_price=index_price, ms=ms, **extra)
        logger.debug("publish index price topic %s, payload %s", topic, payload)
        await self._publish(topic, payload)
        # 由于 publish_index_price extra 中可能包含如 funding fee 等信息，这里只缓存 index_price
        if index_price and not extra:
            ttl = 60 * 60 * 24 * 3  # 3 天
            await self.redis.set(topic, json.dumps(payload), ex=ttl)

    async def publish_funding_rate(self, instrument_name, funding_rate, next_funding_rate, ms, next_ms):
        """
        @param instrument_name:
        @param funding_rate: 资金费率
        @param next_funding_rate:	下一期预测资金费率
        @param ms: 最新的到期结算的资金费时间，Unix时间戳的毫秒数格式，如 1597026383085
        @param next_ms:下一期资金费时间
        """
        topic = self.build_topic(instrument_name, data_type="funding_rate")
        payload = dict(
            instrument_name=instrument_name,
            funding_rate=funding_rate,
            next_funding_rate=next_funding_rate,
            ms=ms,
            next_ms=next_ms,
        )
        logger.debug("publish funding rate topic %s, payload %s", topic, payload)
        await self._publish(topic, payload)

    async def publish_open_interest(self, instrument_name, open_interest=None, open_interest_currency=None, ms=None):
        """
        @param instrument_name:
        @param open_interest: 持仓量，暂时按张为单位，后续按需提供张数转换
        @param open_interest_currency: 持仓量，按币为单位
        @param ms:数据更新的时间，Unix时间戳的毫秒数格式，如 1597026383085
        """
        topic = self.build_topic(instrument_name, data_type="open_interest")
        payload = dict(
            instrument_name=instrument_name,
            open_interest=open_interest,
            open_interest_currency=open_interest_currency,
            ms=ms,
        )
        # logger.debug("publish_open_interest topic %s, payload %s", topic, payload)
        packed_payload = msgpack.packb(payload)
        await self._set_cache(topic, packed_payload)

    async def publish_ticker(self, instrument_name, payload):
        """
        获取成交数据
        :param instrument_name:
        :param payload:
        :return:
        """
        topic = self.build_topic(instrument_name, data_type="ticker")
        # logger.debug("publish_ticker topic %s, payload %s", topic, payload)
        packed_payload = msgpack.packb(payload)
        await self._set_cache(topic, packed_payload)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(BaseWSClient.run(["BTC"]))
