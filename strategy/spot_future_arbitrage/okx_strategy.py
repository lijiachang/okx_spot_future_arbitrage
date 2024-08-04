# import os
# import django
#
# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "basis_alpha.settings")
# django.setup()


import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Coroutine, List, Optional

import aioredis
from django.conf import settings
from django.utils import timezone

from basis_alpha.config import SUBJECT_TYPE
from clients.okex.ws import OkexWSClient
from common.common import IntervalTask
from data_source.orderbook_manager import OrderBookManager
from strategy.models import Account, Order, Strategy
from tools.number import generate_order_id
from tools.telegram import TelegramBot

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageRate:
    currency: str  # 币种
    spot_instrument_name: str  # 现货交易对
    future_instrument_name: str  # 期货交易对
    basis: float  # 基差（合约价格 - 现货价格）
    spread_rate: float  # 价差率
    apy: float  # 收益率（年化）
    future_expire_days: int  # 合约交割剩余天数
    timestamp: int  # 数据时间


# todo abstract
class SpotFutureArbitrage:
    def __init__(self, strategy_name: str, account_name: str):
        self.background_tasks = set()
        self.init_event = asyncio.Event()
        self.maker_trade: Optional[OkexWSClient] = None
        self.taker_trade: Optional[OkexWSClient] = None
        self.redis: Optional[aioredis.Redis] = None
        self.assets: dict[str, dict] = {}
        self.strategy_config: Optional[Strategy] = None
        self.order_book: Optional[OrderBookManager] = None
        self.telegram_bot = TelegramBot(settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID)

        self.strategy_name = strategy_name  # "spot_future_arbitrage"
        self.account_name = account_name  # "jiachang_test"

    async def init_account(self):
        self.redis = await aioredis.from_url(f"{settings.REDIS_URL}")
        self.order_book = OrderBookManager(self.redis)

        account = await Account.objects.aget(name=self.account_name)
        self.strategy_config = await Strategy.objects.aget(name=self.strategy_name)

        decrypted_api_secret = account.decrypt_api_secret()
        auth = (account.api_key, decrypted_api_secret, account.api_passphrase)
        self.maker_trade = self.taker_trade = OkexWSClient(auth, account.name, self)
        await self.maker_trade.start()

    async def start(self):
        await self.init_account()
        self.create_task(self.periodic_task)

        while True:
            try:
                await self.init_event.wait()
                await asyncio.create_task(self.open_positions_by_rank())
                # await asyncio.sleep(10000)  # for test todo remove me
            except Exception as e:
                logger.error(f"open_positions_by_rank error: {e}")
                logger.exception(e, exc_info=True)
                await asyncio.sleep(5)
            await asyncio.sleep(0.5)

    async def periodic_task(self):
        logger.info("periodic_task start")
        IntervalTask(self._update_strategy_config, 10, 10).run_in_background()

    def create_task(self, coro: Callable[..., Coroutine], *args, **kwargs):
        task = asyncio.create_task(coro(*args, **kwargs))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def _update_strategy_config(self):
        self.strategy_config = await Strategy.objects.aget(name=self.strategy_name)
        logger.info("strategy config updated")

    async def get_arbitrage_ranks(self, top_n=10) -> List[ArbitrageRate]:
        """
        获取实时价差率排名前N的交易对

        计算公式:
        现货买入,合约卖出
        收益率 = (合约卖出价格 - 现货买入价格) / 现货买入价格 / future_expire_days * 365 (%)

        :param top_n: 获取前N个价差率的交易对,默认前10
        """
        arbitrage_rates = []

        # 获取所有交易对的现货和合约盘口数据
        keys = await self.redis.keys("EXECUTE_ENGINE.SPIDER.OKEX.*.BOOK")
        """
        EXECUTE_ENGINE.SPIDER.OKEX.FUTURE_USD.BTC.BTC-5APR24.BOOK
        EXECUTE_ENGINE.SPIDER.OKEX.SPOT.ETC.ETC-USDT.BOOK
        """
        keys = [key.decode() for key in keys]
        spot_keys = [key for key in keys if "SPOT" in key and "USDT" in key]
        future_keys = [key for key in keys if "FUTURE_USD" in key]
        for spot_key in spot_keys:
            _, _, exchange, subject, currency, spot_instrument_name, data_type = spot_key.split(".")
            spot_data = await self.order_book.get_orderbook_info(spot_instrument_name, "asks", 1)
            spot_price = spot_data.price  # 现货 卖一 价格
            for future_key in future_keys:
                if currency not in future_key:
                    continue
                if self.strategy_config.black_list and currency in self.strategy_config.black_list:
                    continue
                _, _, _, _, _, future_instrument_name, _ = future_key.split(".")
                future_data = await self.order_book.get_orderbook_info(future_instrument_name, "bids", 1)
                future_price = future_data.price  # 合约 买一 价格
                if future_price == 0:
                    continue
                future_expire_days = future_data.expire_days  # 合约到期天数
                if future_expire_days <= 0:
                    continue

                # 计算收益率
                basis = future_price - spot_price
                spread_rate = basis / spot_price * 100
                apy = spread_rate / future_expire_days * 365
                timestamp = min(spot_data.data_ms, future_data.data_ms)
                arbitrage_rates.append(
                    ArbitrageRate(
                        currency=currency,
                        spot_instrument_name=spot_instrument_name,
                        future_instrument_name=future_instrument_name,
                        basis=basis,
                        spread_rate=spread_rate,
                        apy=apy,
                        future_expire_days=future_expire_days,
                        timestamp=timestamp,
                    )
                )

        # 按照价差率排序
        arbitrage_rates.sort(key=lambda x: x.spread_rate, reverse=True)

        return arbitrage_rates[:top_n]

    async def open_positions_by_rank(self, top_n=20):
        """
        根据收益率排名开仓
        """
        # 获取收益率排名
        arbitrage_ranks = await self.get_arbitrage_ranks(top_n)
        logger.info(f"arbitrage_ranks: {arbitrage_ranks}")

        for arb_rate in arbitrage_ranks:
            # 判断数据是否超时
            time_diff = int(time.time() - arb_rate.timestamp / 1000)
            if time_diff > 60:
                logger.warning(f"orderbook data is timeout, {time_diff=} s, {arb_rate}")
                continue

            # 忽略近期合约
            if arb_rate.future_expire_days < 30:
                logger.info(f"ignore near future contract, {arb_rate}")
                continue

            # 检查收益率是否达标
            if arb_rate.apy < self.strategy_config.min_open_rate:
                logger.info(f"apy is too low, {arb_rate}")
                continue

            # 检查是否允许开仓
            if await self.check_open_position(arb_rate.currency) is False:
                continue

            # 开仓
            logger.info(f"prepare_open_position, {arb_rate}")
            await self.open_position(arb_rate.spot_instrument_name, arb_rate.future_instrument_name)
            return

    async def check_open_position(self, currency) -> bool:
        """
        检查是否允许开仓

        :return: True 允许开仓 False 不允许开仓
        """
        # 策略是否开启
        if not self.strategy_config.is_active:
            return False

        # 检查现货USDT余额是否充足
        usdt_balance = self.assets.get("USDT", {}).get("equity", 0)
        if usdt_balance < self.strategy_config.per_order_usd:
            logger.warning(f"USDT balance is not enough, {usdt_balance}")
            return False

        # 检查仓位是否达到上限
        # 限制单个币种的最大持仓量USD市值
        success, positions = await self.taker_trade.get_positions(subject=SUBJECT_TYPE.FUTURE_USD, currency=currency)
        if not success:
            logger.warning(f"get positions failed, {positions}")
            return False
        sum_position_value = sum([pos["size_usd"] for pos in positions])
        max_position_value = self.strategy_config.max_position_value
        logger.info(
            f"currency: {currency}, sum_position_value: {sum_position_value}, max_position_value: {max_position_value}"
        )
        if max_position_value and abs(sum_position_value) >= max_position_value:
            return False

        return True

    async def open_position(self, spot_instrument_name, future_instrument_name):
        """
        开仓

        :param spot_instrument_name: 现货交易对名称
        :param future_instrument_name: 合约交易对名称
        """
        logger.debug(f"begin to open position, {spot_instrument_name}, {future_instrument_name}")

        depth = 1 if settings.TESTNET else 5  # 主要针对test环境中的盘口深度不足
        # 获取现货[卖5]价格
        spot_ask_price = await self.order_book.get_price(spot_instrument_name, "asks", depth)
        # 获取合约[买5]价格
        future_bid_price = await self.order_book.get_price(future_instrument_name, "bids", depth)

        # 计算下单数量
        #   - 单笔订单金额（USD) per_order_usd
        #     - 100的整数倍，每笔订单按照该数据进行下单
        per_order_usd = self.strategy_config.per_order_usd
        spot_size = per_order_usd / spot_ask_price

        # 下单
        spot_payload = {
            "instrument_name": spot_instrument_name,
            "client_order_id": generate_order_id(subject=SUBJECT_TYPE.SPOT),
            "price": spot_ask_price,
            "amount": spot_size,
            "side": "buy",
            "order_type": "limit",
            "reduce_only": False,
        }
        future_payload = {
            "instrument_name": future_instrument_name,
            "client_order_id": generate_order_id(subject=SUBJECT_TYPE.FUTURE_USD),
            "price": future_bid_price,
            "amount": per_order_usd,
            "side": "sell",
            "order_type": "limit",
            "reduce_only": False,
        }
        success, data = await self.maker_trade.batch_take_order([spot_payload, future_payload])
        if not success:
            text = f"open_position error: batch_take_order, {spot_payload=}, {future_payload=} failed: {data}"
            logger.error(text)
            self.create_task(self.telegram_bot.send_text_msg, text)

    async def get_arbitrage_rate(self, instrument_name: str) -> Optional[ArbitrageRate]:
        """
        根据交易对名称获取对应的收益率
        """
        currency = instrument_name.split("-")[0]
        spot_instrument_name = f"{currency}-USDT"
        future_instrument_name = instrument_name

        spot_data = await self.order_book.get_orderbook_info(spot_instrument_name, "asks", 1)
        spot_price = spot_data.price  # 现货 卖一 价格

        future_data = await self.order_book.get_orderbook_info(future_instrument_name, "bids", 1)
        future_price = future_data.price  # 合约 买一 价格
        future_expire_days = future_data.expire_days  # 合约到期天数

        # 计算收益率
        basis = future_price - spot_price
        spread_rate = basis / spot_price * 100
        apy = spread_rate / future_expire_days * 365
        timestamp = min(spot_data.data_ms, future_data.data_ms)

        return ArbitrageRate(
            currency=currency,
            spot_instrument_name=spot_instrument_name,
            future_instrument_name=future_instrument_name,
            basis=basis,
            spread_rate=spread_rate,
            apy=apy,
            future_expire_days=future_expire_days,
            timestamp=timestamp,
        )

    async def get_account_config(self):
        config = await self.maker_trade.get_account_config()
        print("config: ", config)

    async def on_event_asset_update(self, data: dict):
        # 更新资产信息
        logger.debug("on_event_asset_update %s", data)
        currency = data["currency"]
        self.assets[currency] = data
        self.init_event.set()

    @staticmethod
    async def _order_update(data):
        await Order.objects.aupdate_or_create(
            order_id=data["exchange_order_id"],
            defaults={
                "client_order_id": data["order_id"],
                "instrument_name": data["instrument_name"],
                "side": data["direction"],
                "price": data["price"],
                "filled_price": data["avg_price"],
                "size": data["amount"],
                "filled_size": data["filled_amount"],
                "fee": data["fee"],
                "fee_currency": data["fee_asset"],
                "state": data["state"],
                "raw_data": data["original_data"],
                "created_at": timezone.make_aware(datetime.fromtimestamp(data["created_at"] / 1000)),
                "updated_at": timezone.make_aware(datetime.fromtimestamp(data["updated_at"] / 1000)),
            },
        )
        logger.debug("order to db: %s", data)

    async def on_event_order_update(self, data):
        # 更新订单信息
        logger.debug("on_event_order_update %s", data)
        self.create_task(self._order_update, data)

    async def on_event_trade_update(self, data):
        # 更新成交信息
        logger.debug("on_event_trade_update %s", data)

    async def on_event_position_update(self, data: List[dict]):
        """
        更新仓位信息,
        并进行平仓操作
        """
        logger.debug("on_event_position_update %s", data)
        self.create_task(self._check_positions_to_close, data)

    async def _check_positions_to_close(self, data):
        close_positions = []
        for position in data:
            instrument_name = position["instrument_name"]
            arb_rate = await self.get_arbitrage_rate(instrument_name)
            if not arb_rate:
                continue

            # 检查收益率是否低于平仓阈值
            if arb_rate.apy < self.strategy_config.max_close_rate:
                close_positions.append(position)

        if not close_positions:
            return

        await self.close_positions(close_positions)

    async def close_positions(self, close_positions: list[dict]):
        logger.info(f"begin to close positions: {close_positions}")
        # 平仓操作
        batch_payloads = []
        for position in close_positions:
            instrument_name = position["instrument_name"]
            currency = instrument_name.split("-")[0]
            spot_instrument_name = f"{currency}-USDT"
            future_instrument_name = instrument_name

            depth = 1 if settings.TESTNET else 5  # 主要针对test环境中的盘口深度不足
            # 获取现货[卖5]价格
            spot_bid_price = await self.order_book.get_price(spot_instrument_name, "bids", depth)
            # 获取合约[卖5]价格
            future_ask_price = await self.order_book.get_price(future_instrument_name, "asks", depth)

            # 计算下单数量
            # 每次的平仓数量为per_order_usd
            # 合约数量：
            position_size_usd = min(abs(position["size_usd"]), self.strategy_config.per_order_usd)
            # 现货数量：
            spot_best_bid_price = await self.order_book.get_price(spot_instrument_name, "bids", 1)
            position_size = min(abs(position_size_usd / spot_best_bid_price), self.assets[currency]["equity"])

            # 下单
            spot_payload = {
                "instrument_name": spot_instrument_name,
                "client_order_id": generate_order_id(subject=SUBJECT_TYPE.SPOT),
                "price": spot_bid_price,
                "amount": position_size,
                "side": "sell",
                "order_type": "limit",
                "reduce_only": False,
            }
            future_payload = {
                "instrument_name": future_instrument_name,
                "client_order_id": generate_order_id(subject=SUBJECT_TYPE.FUTURE_USD),
                "price": future_ask_price,
                "amount": position_size_usd,
                "side": "buy",
                "order_type": "limit",
                "reduce_only": True,
            }
            batch_payloads.append(spot_payload)
            batch_payloads.append(future_payload)

        success, data = await self.maker_trade.batch_take_order(batch_payloads)
        if not success:
            text = f"close_positions error: batch_take_order, {batch_payloads=}, failed: {data}"
            logger.error(text)
            self.create_task(self.telegram_bot.send_text_msg, text)


if __name__ == "__main__":

    arb = SpotFutureArbitrage("spot_future_arbitrage", "jiachang_test")
    asyncio.run(arb.start())
