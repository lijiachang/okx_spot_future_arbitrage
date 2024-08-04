import asyncio
import logging
import time
from typing import NamedTuple

import websockets

from basis_alpha import config
from common.common import ACCOUNT_PROPERTY
from common.topic import generate_exchange_hub_topic, generate_fee_rate_topic
from tools.account import make_printable_account

logger = logging.getLogger(__name__)

SUBJECT_MAP = {
    config.SUBJECT_TYPE.OPTION.name: "option",
    config.SUBJECT_TYPE.FUTURE_USD.name: "future",
    config.SUBJECT_TYPE.SWAP_USD.name: "future",
}


class Position(NamedTuple):
    currency: str
    instrument_name: str
    size: float
    direction: str


class AccountSummary(NamedTuple):
    currency: str
    available_funds: float

    delta_total: float
    options_delta: float
    future_delta: float
    options_gamma: float
    options_theta: float
    options_vega: float

    options_value: float
    options_pl: float


class OrderDetail(NamedTuple):
    order_id: str
    label: str
    direction: str
    order_status: str

    amount: float
    price: float

    filled_amount: float
    avg_price: float


class Instruction(NamedTuple):
    instrument_name: str
    side: str
    size: float
    price: float
    client_order_id: str

    @property
    def total_value(self):
        return self.price * self.size


class Base:
    WS_URL = ""
    TIMEOUT_THRESHOLD = 3 * 60  # 3分钟
    TIMEOUT_KILLER_PERIOD = 30
    TIMEOUT_KILLER_COOLDOWN = 25
    PERIOD_TASK_INTERVAL = 60

    async def get_url(self):
        return self.WS_URL

    def __init__(self, credential, accountid, exchange, *args):
        self.account_id = None
        self.credential = credential
        self.background_loop = None
        self.timeout_killer = None
        self.period_task = None

        self.last_updated_at = time.time()
        self.period_task_funcs = []
        self.shutdowned = False

        self.printable_account_id = make_printable_account(accountid)

    async def _run_period_task(self):
        current_ws = self.websocket
        while not self.shutdowned and current_ws == self.websocket:
            await asyncio.sleep(self.PERIOD_TASK_INTERVAL)
            for func in self.period_task_funcs:
                await func()

    async def _timeout_killer(self):
        current_ws = self.websocket
        while not self.shutdowned and current_ws == self.websocket:
            await asyncio.sleep(self.TIMEOUT_KILLER_PERIOD)
            timeout = time.time() - self.last_updated_at
            # logger.info(f'timeout: {timeout}, {type(self)}, {self.account_id}')
            logger.info(f"SELF ID: {id(self)}, {type(self)}, {self.account_id}")
            if timeout > self.TIMEOUT_THRESHOLD:
                try:
                    logger.info(f"websocket not fresh, call start again! {self.account_id}")
                    await self.start()
                    await asyncio.sleep(self.TIMEOUT_KILLER_COOLDOWN)
                except Exception as e:
                    logger.exception(str(e), exc_info=True)

    def create_ws_connection(self, url):
        return websockets.connect(url)

    async def _background_loop(self):
        url = await self.complete_or_canceled(self.get_url)
        if not url:
            logger.error(f"get_url failed , exit from backgroud_loop, accountid:{self.account_id}")
            return
        logger.info(f"start:{url}")

        async with self.create_ws_connection(url) as websocket:
            logger.info("connection established")
            self.websocket = websocket
            setup = asyncio.get_event_loop().create_task(self.setup())
            handler = asyncio.get_event_loop().create_task(self.handler())
            self.timeout_killer = asyncio.get_event_loop().create_task(self._timeout_killer())
            self.period_task = asyncio.get_event_loop().create_task(self._run_period_task())
            await setup
            await handler

    async def start(self):
        self.last_updated_at = time.time()
        if self.background_loop:
            self.background_loop.cancel()
        if self.timeout_killer:
            self.timeout_killer.cancel()
        if self.period_task:
            self.period_task.cancel()
        self.background_loop = asyncio.get_event_loop().create_task(self._background_loop())

    async def handler(self):
        async for message in self.websocket:
            self.last_updated_at = time.time()
            await self.on_ws_message(message)

    async def on_ws_message(self, message):
        pass

    async def setup(self):
        pass

    async def update_auth(self, client_id, client_secret, client_passphrase=None):
        pass

    def need_retry_auth(self) -> (bool, str):
        return True, ""

    def build_topic(self, account_id, exchange, method, currency, subject=None):
        return generate_exchange_hub_topic(account_id, exchange, method, currency, subject=subject)

    def build_fee_rate_topic(self, account, exchange):
        return generate_fee_rate_topic(account, exchange)

    def build_base_currency_position_topic(self, account_id, exchange, currency, subject=None):
        return self.build_topic(account_id, exchange, ACCOUNT_PROPERTY.POSITION, currency, subject=subject)

    def build_quote_currency_position_topic(self, account_id, exchange, quote_currency, subject=None) -> (str, str):
        """
        return: (topic, currency)
        """
        quote_currency = "QUOTE_{}".format(quote_currency)
        return (
            self.build_topic(account_id, exchange, ACCOUNT_PROPERTY.POSITION, quote_currency, subject=subject),
            quote_currency,
        )

    async def shutdown(self):
        if self.background_loop:
            self.background_loop.cancel()
        if self.timeout_killer:
            self.timeout_killer.cancel()
        if self.period_task:
            self.period_task.cancel()

        self.shutdowned = True
        logger.info(f"shutdown SELF ID: {id(self)}, {type(self)}, {self.account_id}")

    def backoff_seconds(self) -> float:
        return 1

    async def complete_or_canceled(self, func, *args, **kwargs):
        while True:
            try:
                return await func(*args, **kwargs)
            except asyncio.CancelledError as e:
                logger.error(e)
                return None
            except BaseException as e:
                logger.exception("unexcepted error %s %s", func.__name__, e)
                await asyncio.sleep(self.backoff_seconds())
