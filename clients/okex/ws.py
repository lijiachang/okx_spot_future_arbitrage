import asyncio
import concurrent.futures
import datetime
import logging
from typing import Optional
from uuid import uuid4

import ujson as json
import websockets
from django.conf import settings

from basis_alpha import config
from clients.base import Base
from clients.formatters.factory import FormatMethod, FormatterFactory

from .common import Signer
from .config import ACCOUNT_SUMMARY_CURRENCIES
from .http import OkexHttpClient, capability

WAIT_TIMEOUT = 10

logger = logging.getLogger(__name__)


class OkexAuthBase(Base):
    GREEKS_CHANNEL: str
    POSITION_CHANNEL: str
    ORDER_CHANNEL: str
    SUMMARY_CHANNEL: str

    def __init__(self, auth=None, account_id=None):
        self.exchange_name = config.EXCHANGE.OKEX.name
        self.client_id, self.client_secret, self.client_passphrase = auth
        self.signer = Signer(*auth)
        self.account_id = account_id
        self.queue = asyncio.Queue()
        self.login_succeed = asyncio.Event()
        self.queues = {}
        self.token = None
        self.position_cache = {}
        self.okex_period_task = []
        super().__init__(auth, account_id, self.exchange_name)

    async def get_auth_result(self):
        try:
            ret = await asyncio.wait_for(self.login_succeed.wait(), WAIT_TIMEOUT)
        except concurrent.futures._base.TimeoutError:
            logger.info("get_auth_result failed")
            ret = False
        except asyncio.exceptions.TimeoutError:
            logger.info(f"get_auth_result failed, account_id: {self.account_id} timeout")
            ret = False
        logger.info(f"get_auth_result {ret}")
        return ret

    def _build_message(self, method, params=None, msg_id=None):
        params = dict(op=method, args=params)
        if msg_id:
            params.update({"id": msg_id})

        ret = json.dumps(params)
        return ret

    async def get_url(self):
        return self.get_private_url()

    def get_base_url(self):
        url = settings.OKEX_WS_URL
        if not url:
            if settings.TESTNET:
                url = "wss://wspap.okx.com:8443"
            else:
                url = "wss://ws.okx.com:8443"
        return url

    def get_public_url(self):
        url = self.get_base_url() + "/ws/v5/public"
        if settings.TESTNET:
            url += "?brokerId=9999"
        return url

    def get_private_url(self):
        url = self.get_base_url() + "/ws/v5/private"
        if settings.TESTNET:
            url += "?brokerId=9999"
        return url

    async def on_auth_success(self, success=True):
        #  await self.queue.put(success)
        self.login_succeed.set()

    async def setup(self):
        logger.info("setup")
        # ws断连接后重置缓存
        self.position_cache = {}
        auth_result = await self.auth()
        if auth_result:
            logger.info("auth succeed!!")
            await self.on_auth_success()
            await self.subscribe()
        else:
            logger.error("auth_failed")

    @capability.register
    async def auth(self, wait_for=0):
        logger.info("auth")
        self.login_succeed.clear()
        data = self.signer.get_signature()
        await self.send("login", [data])
        try:
            resp = await asyncio.wait_for(self.queue.get(), WAIT_TIMEOUT)
        except Exception as e:
            logger.error(str(e))
            return False
        logger.info(f"login_resp: {resp}")
        login_code = resp["code"]
        if login_code != "0":
            logger.error(f"login_failed: {resp}")
        else:
            return True

    async def update_auth(self, client_id, client_secret, client_passphrase=None):
        if self.client_id != client_id or self.client_secret != client_secret:
            self.client_id, self.client_secret, self.client_passphrase = (
                client_id,
                client_secret,
                client_passphrase,
            )
            await self.start()
            await asyncio.wait_for(self.login_succeed.wait(), WAIT_TIMEOUT)

    async def subscribe(self, currencies=ACCOUNT_SUMMARY_CURRENCIES, kind=("option", "future")):
        await self.send(
            method="subscribe",
            params=[
                {
                    "channel": self.SUMMARY_CHANNEL,
                    "currency": c,
                    "interval": "100ms",
                }
                for c in currencies
            ]
            + [
                {
                    "channel": self.ORDER_CHANNEL,
                    "instType": "ANY",
                }
            ]
            + [
                {
                    "channel": self.POSITION_CHANNEL,
                    "instType": "ANY",
                    "extraParams": ' {"updateInterval": "2000"} '
                    # 0: 仅根据持仓事件推送数据
                    # 2000, 3000, 4000: 根据持仓事件推送，且根据设置的时间间隔定时推送（ms）
                },
                {
                    "channel": self.GREEKS_CHANNEL,
                },
            ],
        )

        for task in self.okex_period_task[:]:
            task.cancel()  # 避免重复订阅
            self.okex_period_task.remove(task)
            logger.info(f"cancel okex period task, account_id: {self.account_id}, {task}")
        self.create_period_task()

    def create_period_task(self):
        task = asyncio.get_event_loop().create_task(self.clean_positions_cache())
        self.okex_period_task.append(task)

    async def send(self, method, params, ignore_response=True):
        for _ in range(3):
            try:
                if not ignore_response:
                    msg_id = str(uuid4())
                    queue = asyncio.Queue()
                else:
                    msg_id = None
                    queue = None
                msg = self._build_message(method, params=params, msg_id=msg_id)
                # logger.info(f'<= {msg}')
                await self.websocket.send(msg)
                if not ignore_response:
                    return await asyncio.wait_for(queue.get(), WAIT_TIMEOUT)
                return
            except (
                websockets.exceptions.ConnectionClosedOK,
                websockets.exceptions.ConnectionClosedError,
                # concurrent.futures._base.TimeoutError,
            ) as e:
                logger.error(f"连接中断，重新建立连接 {str(e)}")
                await self.start()
                await self.get_auth_result()

    def clean_positions_cache(self):
        pass


class OkexWSClient(OkexAuthBase):
    ORDER_CHANNEL = "orders"
    SUMMARY_CHANNEL = "account"
    POSITION_CHANNEL = "positions"
    GREEKS_CHANNEL = "account-greeks"

    def __init__(self, auth=None, account_id=None, strategy=None):
        super().__init__(auth, account_id)
        self.http_client = OkexHttpClient(auth, account_id=account_id)
        from strategy.spot_future_arbitrage.okx_strategy import SpotFutureArbitrage

        self.strategy: SpotFutureArbitrage = strategy
        self.account_summary_dict = {}  # {currency: summary_dict}

    async def handle_login(self, message):
        await self.queue.put(message)

    async def handle_subscribe(self, message):
        pass

    async def handle_unsubscribe(self, message):
        pass

    async def handle_error(self, message):
        logger.error("ws error message: %s", message)

    async def handle_orders(self, message):
        category = config.SUBJECT_TYPE.OPTION  # okex的可以随便写

        async def _handle_order(sub_dataset):
            formatted_data = FormatterFactory.format(
                self.account_id,
                self.exchange_name,
                category,
                sub_dataset,
                FormatMethod.ORDER,
            )
            logger.debug(f"_handle_order message: {formatted_data}")
            await self.strategy.on_event_order_update(formatted_data)

        async def _handle_trade(sub_dataset):
            formatted_data = FormatterFactory.format(
                self.account_id,
                self.exchange_name,
                category,
                sub_dataset,
                FormatMethod.TRADE,
            )
            if formatted_data:
                logger.debug(f"_handle_trade message: {formatted_data}")
                await self.strategy.on_event_trade_update(formatted_data)

        for item in message["data"]:
            await _handle_order(item)
            await _handle_trade(item)

    def _cache_formatted_positions(self, formatted_data):
        now = datetime.datetime.utcnow()

        if not self.position_cache:
            self.position_cache = {pos["instrument_name"]: (pos, now) for pos in formatted_data}
            return formatted_data

        for pos in formatted_data:
            self.position_cache[pos["instrument_name"]] = (pos, now)
        return [pos for pos, update_at in self.position_cache.values()]

    async def clean_positions_cache(self):
        logger.info("clean_positions_cache created")
        while not self.shutdowned:
            now = datetime.datetime.utcnow()
            for instrument_name, (pos, last_update_at) in self.position_cache.items():
                if now - last_update_at >= datetime.timedelta(hours=24) and pos.get("size") == 0:
                    del self.position_cache[instrument_name]
            await asyncio.sleep(60)

    async def handle_positions(self, message):
        """
        推送只支持单向持仓
        """
        # 背景，整个 OKX 交易默认使用 portfolio margin mode + cross margin type
        # logger.debug(f'handle_positions message: {message}')
        category = config.SUBJECT_TYPE.OPTION.name
        formatted_data = FormatterFactory.format(
            self.account_id,
            self.exchange_name,
            category,
            message["data"],
            FormatMethod.POSITION,
        )
        # to_publish_data = self._cache_formatted_positions(formatted_data)
        await self.strategy.on_event_position_update(formatted_data)

        for position in message["data"]:
            currency = position.get("ccy")
            if currency in self.account_summary_dict:
                # 缓存住仓位，只有这样account那里才能从仓位里那需要的信息，计算mm
                # 不能像 bybit 一样将整个 position dict 更新进入 account_summary_dict，
                # 因为 position.greeks 在OKX 只代表一个 instrument 的 greeks
                self.account_summary_dict[currency].update({"mgnRatio": position["mgnRatio"]})
                await self._handle_account(self.account_summary_dict[currency])

    async def _handle_account(self, detail):
        currency = detail["ccy"]
        if currency in self.account_summary_dict:
            # 保存 account 信息
            self.account_summary_dict[currency].update(detail)
        else:
            self.account_summary_dict.update({currency: detail})
        category = config.SUBJECT_TYPE.OPTION  # okex的可以随便写
        formatted_data = FormatterFactory.format(
            self.account_id, self.exchange_name, category, detail, FormatMethod.SUMMARY
        )
        # logger.debug(f"formatted_data summary: {formatted_data}")
        await self.strategy.on_event_asset_update(formatted_data)

    async def handle_account(self, message):
        # logger.debug(f'handle_account message: {message}')
        for item in message["data"]:
            for detail in item["details"]:
                await self._handle_account(detail)

    async def handle_account_greeks(self, message):
        """
        内部的 Greeks 并不是单独推送，而是依赖于 Account Summary
        """
        data = message["data"]
        # logger.debug(f'greeks message: {data}')
        for item in data:
            currency = item.get("ccy")
            if currency in self.account_summary_dict:
                self.account_summary_dict[currency].update(item)
                await self._handle_account(self.account_summary_dict[currency])

    async def on_ws_message(self, message):
        logger.debug(f"on_ws_message=> {message}")
        try:
            message = json.loads(message)
            event = message.get("event", None) or message.get("arg", {}).get("channel", None) or "unknown"

            # 事件和处理函数
            event_handlers = {
                "login": self.handle_login,
                "subscribe": self.handle_subscribe,
                "unsubscribe": self.handle_unsubscribe,
                "error": self.handle_error,
                "orders": self.handle_orders,
                "positions": self.handle_positions,
                "account": self.handle_account,
                "account-greeks": self.handle_account_greeks,
                "channel-conn-count": self.handle_channel_conn_count,
            }

            handler = event_handlers.get(event, self.handle_unknown)
            await handler(message)

        except Exception as e:
            logger.exception(str(e), exc_info=True, stack_info=True)

    async def handle_unknown(self, message):
        logger.warning(f"unknown event received: {message}")

    async def handle_channel_conn_count(self, message):
        """ws 连接数量"""
        logger.info(f"handle_channel_conn_count: {message}")

    # == http下单代理 == #
    async def take_order(self, *args, **kwargs):
        return await self.http_client.take_order(*args, **kwargs)

    async def batch_take_order(self, payload_list):
        return await self.http_client.batch_take_order(payload_list)

    async def get_trades(self, order_id, **kwargs):
        return await self.http_client.get_trades(order_id, **kwargs)

    async def check_order_status(self, *args, **kwargs):
        return await self.http_client.check_order_status(*args, **kwargs)

    async def get_orders(self, *args, **kwargs):
        return await self.http_client.get_orders(*args, **kwargs)

    async def get_order_history(self, *args, **kwargs):
        return await self.http_client.get_order_history(*args, **kwargs)

    async def get_trade_history(self, *args, **kwargs):
        return await self.http_client.get_trade_history(*args, **kwargs)

    async def get_open_orders(self, *args, **kwargs):
        return await self.http_client.get_open_orders(*args, **kwargs)

    async def cancel_order(self, order_id=None, **kwargs):
        return await self.http_client.cancel_order(order_id=order_id, **kwargs)

    async def batch_cancel_order_dict(self, orders=None, **kwargs):
        return await self.http_client.batch_cancel_order_dict(orders=orders, **kwargs)

    async def batch_amend_order(self, orders=None, **kwargs):
        return await self.http_client.batch_amend_order(orders=orders, **kwargs)

    async def batch_cancel_order(self, order_id_list=None, **kwargs):
        return await self.http_client.batch_cancel_order(order_id_list=order_id_list, **kwargs)

    async def cancel_all_order(self, subject, currency, instrument_name=None):
        return await self.http_client.cancel_all_order(subject, currency, instrument_name=instrument_name)

    async def get_account_summary(self, currency):
        return await self.http_client.get_account_summary(currency)

    async def get_positions(self, *args, **kwargs):
        return await self.http_client.get_positions(*args, **kwargs)

    async def get_instruments(self, *args, **kwargs):
        return await self.http_client.get_instruments(*args, **kwargs)

    async def get_settlement_history(self, currency, start_ms, end_ms, type="delivery", instrument_name=None):
        """获取交割历史记录"""
        return await self.http_client.get_settlement_history(
            currency, start_ms, end_ms, type=type, instrument_name=instrument_name
        )

    async def get_funding_bills(self, start_ms):
        """获取资金费率账单
        start_ms 可以指定开始时间，单位毫秒
        """
        return await self.http_client.get_funding_bills(start_ms)

    async def get_delivery_prices(self, currency, latest=False, subject=None):
        """获取交割价"""
        return await self.http_client.get_delivery_prices(currency, latest=latest, subject=subject)

    async def get_exchange_order(self, exchange_order_id, order_id=None):
        return False, {}

    async def get_maxsize(self, *args, **kwargs):
        return await self.http_client.get_maxsize(*args, **kwargs)

    async def get_interest_limits(self, type_: Optional[str], currency: Optional[str]):
        return await self.http_client.get_interest_limits(type_, currency)

    async def get_max_loan(self, *args, **kwargs):
        return await self.http_client.get_max_loan(*args, **kwargs)

    async def proxy_call(self, name, params=None):
        if params is None:
            params = {}
        return await self.http_client.proxy_call(name, params)

    async def get_account_config(self):
        return await self.http_client.get_account_config()

    async def set_position_mode(self, *args, **kwargs):
        return await self.http_client.set_position_mode(*args, **kwargs)

    async def set_leverage(self, *args, **kwargs):
        return await self.http_client.set_leverage(*args, **kwargs)

    async def get_leverage_info(self, *args, **kwargs):
        return await self.http_client.get_leverage_info(*args, **kwargs)

    async def get_account_trade_fee(self, *args, **kwargs):
        return await self.http_client.get_account_trade_fee(*args, **kwargs)

    async def borrow_repay(self, *args, **kwargs):
        return await self.http_client.borrow_repay(*args, **kwargs)

    async def purchase_redempt(self, currency: str, amount: str, side: str, rate: str = None):
        return await self.http_client.purchase_redempt(currency=currency, amount=amount, side=side, rate=rate)

    async def get_lending_rate_history(self, currency: str, after: int = None, before: int = None, limit: int = None):
        return await self.http_client.get_lending_rate_history(
            currency=currency, after=after, before=before, limit=limit
        )

    async def get_currencies(self, currency: str = None):
        return await self.http_client.get_currencies(currency=currency)

    async def get_saving_balance(self, currency: str = None):
        return await self.http_client.get_saving_balance(currency=currency)

    async def get_balances(self, currency: str = None):
        return await self.http_client.get_balances(currency=currency)

    async def transfer(
        self,
        currency: str,
        amount: str,
        _from: str,
        to: str,
        sub_account: str = None,
        _type: str = None,
        loan_trans: bool = None,
        client_id: str = None,
        omit_post_risk: bool = None,
    ):
        return await self.http_client.transfer(
            currency=currency,
            amount=amount,
            _from=_from,
            to=to,
            sub_account=sub_account,
            _type=_type,
            loan_trans=loan_trans,
            client_id=client_id,
            omit_post_risk=omit_post_risk,
        )
