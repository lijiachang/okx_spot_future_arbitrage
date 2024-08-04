import asyncio
import logging
import re
import time
from typing import List, Optional, Union

import requests
import ujson as json
from django.conf import settings

from basis_alpha import config
from common.common import FundingRate, IntervalTask, PositionTier, PublicTicker, PublicTrade, SysTime
from common.okx_common import IndexConvertor, InstrumentConverter, SizeConvertor
from data_source.spiders.data_type import KLine
from data_source.spiders.http_client import HTTPClientBase
from data_source.spiders.okx_config import OPTION_CURRENCIES, SUBJECT_MAP, get_inst_family, get_uly
from tools.instruments import EEInstrument, get_subject_by, parse_ee_instrument

from .base_spider import BaseWSClient, NewOrderBookManager, Trade

logger = logging.getLogger(__name__)

TIMEOUT = 15


class OK_INST_TYPE:
    SPOT = "SPOT"
    SWAP = "SWAP"
    FUTURES = "FUTURES"
    OPTION = "OPTION"
    MARGIN = "MARGIN"


SUPPORTED_SUBJECT_TYPE = set(SUBJECT_MAP.keys())  # OKX支持的EE交易类型
SUPPORTED_INST_TYPE = set(SUBJECT_MAP.values())  # EE支持的OKX交易类型

okInstType = {
    config.SUBJECT_TYPE.SPOT.name: OK_INST_TYPE.SPOT,
    config.SUBJECT_TYPE.SWAP_USD.name: OK_INST_TYPE.SWAP,
    config.SUBJECT_TYPE.SWAP_USDT.name: OK_INST_TYPE.SWAP,
    config.SUBJECT_TYPE.SWAP_USDC.name: OK_INST_TYPE.SWAP,
    config.SUBJECT_TYPE.FUTURE_USD.name: OK_INST_TYPE.FUTURES,
    config.SUBJECT_TYPE.FUTURE_USDT.name: OK_INST_TYPE.FUTURES,
    config.SUBJECT_TYPE.OPTION.name: OK_INST_TYPE.OPTION,
    config.SUBJECT_TYPE.OPTION_USDC.name: OK_INST_TYPE.OPTION,
    config.SUBJECT_TYPE.MARGIN.name: OK_INST_TYPE.MARGIN,
}


class OkexWSClient(BaseWSClient):
    BLACK_CURRENCIES: List[str] = []
    TIMEOUT_THRESHOLD = 50
    TIMEOUT_KILLER_COOLDOWN = 250
    TIMEOUT_KILLER_PERIOD = 50

    EXCHANGE = config.EXCHANGE.OKEX.name
    SUBJECT_TYPE = config.SUBJECT_TYPE.OPTION.name

    def __init__(self, interest_currencies, *args, kind="", zmq_port=None, **kwargs):
        self.interest_currencies = [ic for ic in interest_currencies if ic not in self.BLACK_CURRENCIES]
        self.instrument_prefix_re = re.compile(f'^({"|".join(self.interest_currencies)})-(USD|USDT|USDC)-')
        self.spot_instrument_prefix_re = re.compile(
            f'^({"|".join(self.interest_currencies)})-({config.CURRENCY.spot_quote_exp()})$'
        )  # 现货交易对匹配
        self.last_log_ts = None
        self.last_updated_at = time.time()
        self.depth = 5
        self.orderbook_max_depth = 5  # 盘口深度截取
        self.kinds = [k for k in kind.upper().split(",") if k] if kind else SUPPORTED_SUBJECT_TYPE
        self.inst_types = set(okInstType[k] for k in self.kinds) if self.kinds else SUPPORTED_INST_TYPE  # OKX交易类型列表
        self.instruments_info = {}
        self.mark_price_cache = {}
        self.candles = (
            "1m",
            "3m",
            "5m",
            "15m",
            "30m",
            "1H",
            "2H",
            "4H",
            "6H",
            "12H",
            "1D",
            "2D",
            "3D",
            "5D",
            "1W",
            "1M",
            "3M",
        )  # 订阅的K线周期
        super().__init__(zmq_port=zmq_port)

        self.http_client = OkexFutureHTTPClient()

    def _build_message(self, method, params=None):
        ret = {
            "op": method,
            "args": params,
        }
        msg = json.dumps(ret)
        logger.debug(f"<= {msg}")
        return msg

    async def batch_subscribe(self, channels):
        # channels: list of dict [{"channel": "", "instId": ""}]
        channels = list(channels)
        page_size = 500
        for i in range(int(len(channels) / page_size) + 1):
            sub_list = channels[i * page_size : (i + 1) * page_size]
            if sub_list:
                await self.send("subscribe", params=sub_list, ignore_response=True)

    async def periodic_task(self):
        async def log():
            logger.debug(f"queue_object_caches size: {[q.qsize() for q in self.queue_object_caches]}")

        IntervalTask(log, 60, 60).run_in_background()

    async def setup(self):
        self.orderbook_manager = NewOrderBookManager(orderbook_max_depth=self.orderbook_max_depth)
        self.channels = set()
        self.instruments_info = {}
        await self.get_instruments_to_subscribe()
        await self.subscribe_instruments()
        # if not self.zmq_publisher:
        #     await self.setup_index_tickers()
        logger.info("setup finished")

    async def pong(self, message):
        if message["params"]["type"] == "test_request":
            await self.send("public/test", ignore_response=True)
        else:
            return

    async def setup_index_tickers(self):
        channel_list = []
        for base_currency in self.interest_currencies:
            if base_currency in ("USDC", "USDT"):
                continue
            for quote_currency in ("USD", "USDT", "BTC"):
                channel_list.append({"channel": "index-tickers", "instId": f"{base_currency}-{quote_currency}"})
        await self.batch_subscribe(channel_list)

    @staticmethod
    def parse_instrument_info(instrument: dict) -> Optional[EEInstrument]:
        """
        instrument e.g.:
        {'alias': '',
          'baseCcy': '',
          'category': '1',
          'ctMult': '1',
          'ctType': 'linear',
          'ctVal': '100',
          'ctValCcy': 'XRP', # 合约面值计价币种
          'expTime': '',
          'instId': 'XRP-USDT-SWAP',
          'instType': 'SWAP',
          'lever': '75',
          'listTime': '1628652662528',
          'lotSz': '1',  # 下单数量精度，如 1：BTC-USDT-200925 0.001：BTC-USDT
          'maxIcebergSz': '100000000',
          'maxLmtSz': '100000000',
          'maxMktSz': '5000',
          'maxStopSz': '5000',
          'maxTriggerSz': '100000000',
          'maxTwapSz': '100000000',
          'minSz': '1', # 最小下单数量
          'optType': '',
          'quoteCcy': '',
          'settleCcy': 'USDT',
          'state': 'live',
          'stk': '',
          'tickSz': '0.00001',  # 下单价格精度，如 0.0001
          'uly': 'XRP-USDT'}"""

        ins = parse_ee_instrument(
            InstrumentConverter.to_system(instrument["instId"]), exchange=config.EXCHANGE.OKEX.name
        )

        if not ins:
            return None

        contract_value = float(instrument.get("ctVal") or 1)  # 张数面值，
        size_min = float(instrument.get("minSz") or -1)  # 最小下单张数
        size_ticker = float(instrument.get("lotSz") or -1)  # 下单数量精度，张数

        return EEInstrument(
            name=ins.name,
            subject=ins.subject,
            base=ins.base,
            quote=ins.quote,
            price_tick=float(instrument.get("tickSz") or -1),
            size_tick=size_ticker * contract_value,
            size_min=size_min * contract_value,
            contract_value_currency=instrument.get("ctValCcy", ""),
            contract_value=contract_value,
            contract_mult=float(instrument.get("ctMult") or 1),
            settlement_currency=instrument.get("settleCcy", ""),
        )

    async def subscribe_instruments(self):
        """
        2022年12月28日起  订阅instruments不再推送全量数据
        当有产品状态变化时（如期货交割、期权行权、新合约/币对上线、人工暂停/恢复交易等），推送产品的增量数据。
        """
        params = []

        for t in self.inst_types:
            params.extend(
                [
                    {
                        "channel": "instruments",
                        "instType": t,
                    }
                ]
            )
            if t == "OPTION":
                for option_currency in set(OPTION_CURRENCIES) & set(self.interest_currencies):
                    if config.SUBJECT_TYPE.OPTION in self.kinds:
                        params.append(
                            {
                                "channel": "opt-summary",
                                "instFamily": f"{option_currency}-USD",
                            }
                        )
                    if config.SUBJECT_TYPE.OPTION_USDC in self.kinds:
                        params.append(
                            {
                                "channel": "opt-summary",
                                "instFamily": f"{option_currency}-USDC",
                            }
                        )

        await self.send("subscribe", params=params, ignore_response=True)

    async def get_instruments_to_subscribe(self):
        instruments_urls = []
        for inst_type in self.inst_types:
            # 期权需要指定uly
            if inst_type == OK_INST_TYPE.OPTION:
                for option_currency in set(OPTION_CURRENCIES) & set(self.interest_currencies):
                    # 为支持USDC期权，新增了参数instFamily（交易品种）
                    option_url = "/api/v5/public/instruments?instType=OPTION&uly={0}-USD&instFamily={0}-USD"
                    option_usdc_url = "/api/v5/public/instruments?instType=OPTION&uly={0}-USD&instFamily={0}-USDC"
                    if config.SUBJECT_TYPE.OPTION in self.kinds:
                        instruments_urls.append(option_url.format(option_currency))
                    if config.SUBJECT_TYPE.OPTION_USDC in self.kinds:
                        instruments_urls.append(option_usdc_url.format(option_currency))
            else:
                instruments_urls.append(f"/api/v5/public/instruments?instType={inst_type}")

        http_client = HTTPClientBase()  # 借来的aiohttp客户端
        http_client.base_http_url = self.get_rest_url

        headers = {"x-simulated-trading": "1"} if settings.SPIDER_TESTNET else {}
        resp_list = await asyncio.gather(*[http_client.get(url, headers=headers) for url in instruments_urls])
        # 批量订阅其中的instrument
        for resp in resp_list:
            if resp.get("code") == "0":
                await self.parse_instruments(resp.get("data", []))

    async def parse_instruments(self, instrument_info_list: List[dict]):
        """解析instruments信息(来自rest或者websocket），然后批量订阅"""
        instrument_list = []
        now_ms = time.time() * 1000
        inst_type = None  # 同一批instruments的inst_type是一样的(因为在订阅websocket和rest请求时指定了instType)
        # kinds中指定了要爬取的subject。如果存在SWAP和FUTURES，取出支持的quote_currency，用于过滤instruments列表，按需订阅
        # 如self.kinds =['SWAP_USD', 'SWAP_USDT', ]，则swap_quote_currency=['USD', 'USDT']
        swap_quote_currency = [subject.replace("SWAP_", "") for subject in self.kinds if subject.startswith("SWAP_")]
        future_quote_currency = [
            subject.replace("FUTURE_", "") for subject in self.kinds if subject.startswith("FUTURE_")
        ]

        for item in instrument_info_list:
            inst_id = item["instId"]  # okx交易所的instrument_name

            if not inst_type:
                inst_type = item["instType"]

            if self.instrument_prefix_re.match(inst_id) or self.spot_instrument_prefix_re.match(inst_id):
                # 由于OKX对于SWAP和FUTURE的定义比较广泛，EE中的SWAP_USD/SWAP_USDT/SWAP_USDC是不同的subject，但是在OKX中是同一个instType：SWAP
                # 所以需要针对SWAP和FUTURES的instType，再做一次过滤，依据是quote_currency（即结算货币）
                if inst_type == OK_INST_TYPE.SWAP and swap_quote_currency:
                    quote_currency = inst_id.split("-")[1]  # 'BTC-USD-SWAP' -> 'USD'
                    if quote_currency not in swap_quote_currency:
                        continue
                if inst_type == OK_INST_TYPE.FUTURES and future_quote_currency:
                    quote_currency = inst_id.split("-")[1]  # 'BTC-USD-221230' -> 'USD'
                    if quote_currency not in future_quote_currency:
                        continue
                # 过滤到已到期的合约和期权
                if inst_type in (OK_INST_TYPE.FUTURES, OK_INST_TYPE.OPTION):
                    instrument_name = InstrumentConverter.to_system(inst_id)
                    expiration_at = int(item["expTime"])  # ms timestamp
                    if expiration_at < now_ms:
                        continue
                    else:
                        self.expiration_at[instrument_name] = expiration_at
                        self.expiration_days[instrument_name] = (expiration_at - now_ms) // 86400000

                instrument_list.append(inst_id)
                # 缓存记录instrument_name
                ins = self.parse_instrument_info(item)
                self.cache_instrument(ins)
                # 缓存instrument信息
                self.instruments_info[inst_id] = item

        logger.debug(f"expiration_at: {self.expiration_at}")
        logger.debug(f"expiration_days: {self.expiration_days}")
        logger.debug(f"exchange instrument_list: {instrument_list}")
        """
        books 首次推400档快照数据，以后增量推送，每100毫秒推送一次变化的数据
        books5 首次推5档快照数据，以后定量推送，每100毫秒当5档快照数据有变化推送一次5档数据
        bbo-tbt 首次推1档快照数据，以后定量推送，每10毫秒当1档快照数据有变化推送一次1档数据
        """
        channel_list = [{"channel": "books5", "instId": item} for item in instrument_list]
        if not self.zmq_publisher:
            # channel_list += [{"channel": "candle1m", "instId": item} for item in instrument_list]
            # channel_list += [{"channel": "trades", "instId": item} for item in instrument_list]
            # if inst_type in (OK_INST_TYPE.FUTURES, OK_INST_TYPE.SWAP, OK_INST_TYPE.OPTION):
            #     channel_list += [{"channel": "mark-price", "instId": item} for item in instrument_list]
            # if inst_type == OK_INST_TYPE.SWAP:
            #     channel_list += [{"channel": "funding-rate", "instId": item} for item in instrument_list]
            if inst_type in (OK_INST_TYPE.SWAP, OK_INST_TYPE.FUTURES):  # 为ND Broker Trade界面提供的数据
                # Kline订阅不同粒度的频道
                # kline_channels = [f"candle{item}" for item in self.candles if item != '1m']  # 还要订阅除1m以外的其他粒度
                # channel_list += [{"channel": ch, "instId": item} for ch in kline_channels for item in instrument_list]

                channel_list += [{"channel": "open-interest", "instId": item} for item in instrument_list]  # 持仓总量
                channel_list += [{"channel": "tickers", "instId": item} for item in instrument_list]  # 行情数据

        await self.batch_subscribe(channel_list)

    async def handle_open_interest(self, message):
        """
        获取持仓总量，每3s有数据更新推送一次数据

        {
            "arg": {
                "channel": "open-interest",
                "instId": "LTC-USD-SWAP"
            },
            "data": [{
                "instType": "SWAP",
                "instId": "LTC-USD-SWAP",
                "oi": "5000",
                "oiCcy": "555.55",
                "ts": "1597026383085"
            }]
        }
        """
        data = message["data"]
        for item in data:
            instrument_name = InstrumentConverter.to_system(item["instId"])
            open_interest = item["oi"]
            open_interest_currency = item["oiCcy"]
            ms = int(item["ts"])
            await self.publish_open_interest(instrument_name, open_interest, open_interest_currency, ms)

    async def handle_tickers(self, message):
        """
        获取产品的最新成交价、买一价、卖一价和24小时交易量等信息。
        最快100ms推送一次，没有触发事件时不推送，触发推送的事件有：成交、买一卖一发生变动。

        {
            "arg": {
                "channel": "tickers",
                "instId": "LTC-USD-200327"
            },
            "data": [{
                "instType": "SWAP",
                "instId": "LTC-USD-200327",
                "last": "9999.99", # 最新成交价
                "lastSz": "0.1", # 最新成交的数量
                "askPx": "9999.99", # 卖一价
                "askSz": "11", # 卖一价对应的量
                "bidPx": "8888.88", # 买一价
                "bidSz": "5", # 买一价对应的数量
                "open24h": "9000", # 24小时开盘价
                "high24h": "10000", # 24小时最高价
                "low24h": "8888.88", # 24小时最低价
                "volCcy24h": "2222", # 24小时成交量，以币为单位
                "vol24h": "2222",  # 24小时成交量，以张为单位，后续按需提供张数转换
                "sodUtc0": "2222", # 0点成交量，以币为单位
                "sodUtc8": "2222", # 8点成交量，以币为单位
                "ts": "1597026383085" # 数据产生时间，Unix时间戳的毫秒数格式
            }]
        }
        """
        data = message["data"]
        for item in data:
            instrument_name = InstrumentConverter.to_system(item["instId"])
            subject = get_subject_by(instrument_name)
            payload = PublicTicker(
                subject=subject,
                instrument_name=instrument_name,
                last=item["last"],
                last_size=item["lastSz"],
                ask_price=item["askPx"],
                ask_size=item["askSz"],
                bid_price=item["bidPx"],
                bid_size=item["bidSz"],
                open_24h=item["open24h"],
                high_24h=item["high24h"],
                low_24h=item["low24h"],
                volume_currency_24h=item["volCcy24h"],
                volume_24h=SizeConvertor.to_system(item["vol24h"], instrument_name, subject=subject),
                sod_utc0=item["sodUtc0"],
                sod_utc8=item["sodUtc8"],
                ms=int(item["ts"]),
            )._asdict()
            await self.publish_ticker(instrument_name, payload)

    async def handle_index_tickers(self, message):
        # logger.info(f"handle_index_tickers: {message}")
        data = message["data"]
        for item in data:
            index_name = IndexConvertor.to_system(item["instId"])
            index_price = float(item["idxPx"])
            ms = int(item["ts"])
            # logger.info(f"publish_index_price {index_name}, {index_price}")
            # publish_index_price ETH_USD, 3189.36
            await self.publish_index_price(index_name, index_price, ms=ms)

    async def handle_instruments(self, message):
        """
        # for option
        {'alias': '', 'baseCcy': '', 'category': '1', 'ctMult': '0.1', 'ctType': '', 'ctVal': '1', 'ctValCcy': 'ETH',
        'expTime': '1651219200000', 'instId': 'ETH-USD-220429-2400-P',
        'instType': 'OPTION', 'lever': '', 'listTime': '1646814627960', 'lotSz': '1', 'minSz': '1', 'optType': 'P',
        'quoteCcy': '', 'settleCcy': 'ETH', 'state': 'live', 'stk': '2400',
        'tickSz': '0.0005', 'uly': 'ETH-USD'}
        # for future usd
        {'alias': 'quarter', 'baseCcy': '', 'category': '1', 'ctMult': '1', 'ctType': 'inverse', 'ctVal': '10',
        'ctValCcy': 'USD', 'expTime': '1656057600000', 'instId': 'LTC-USD-220624',
        'instType': 'FUTURES', 'lever': '75', 'listTime': '1639728600851', 'lotSz': '1', 'minSz': '1', 'optType': '',
        'quoteCcy': '', 'settleCcy': 'LTC', 'state': 'live', 'stk': '', 'tickSz': '0.001', 'uly': 'LTC-USD'}
        """
        logger.debug(f"handle_instruments raw: {message}")
        data = message["data"]
        await self.parse_instruments(data)

    async def handle_unknown(self, message):
        logger.debug(f"unknown message: {message}")

    async def handle_opt_summary(self, message):
        # logger.info(f"handle_opt_summary {message}")
        for data in message["data"]:
            greeks = dict(
                vega=float(data["vega"]),
                theta=float(data["theta"]),
                # rho=,  # MISSING?
                gamma=float(data["gamma"]),
                delta=float(data["delta"]),
            )
            instrument_name = data["instId"]
            instrument_name = InstrumentConverter.to_system(instrument_name)
            # logger.info(f"{data['instId']}, instrument_name: {instrument_name}, greeks {greeks}")
            # BTC-USD-220624-45000-P, instrument_name: BTC-24JUN22-45000-P,
            # greeks {'vega': 7.34547e-05, 'theta': -0.0005504538, 'gamma': 2.1075745728, 'delta': -1.0521845761}
            mark_price = self.mark_price_cache.get(instrument_name, None)
            mark_iv = float(data["markVol"]) * 100  # 面向 Deribit 编程，PB 项目需要 mark_iv 为百分数
            self.orderbook_manager.change_ticker(
                instrument_name,
                greeks,
                mark_price,
                mark_iv,
                float(data.get("bidVol", 0)) * 100,
                float(data.get("askVol", 0)) * 100,
            )

            forward_price = float(data["fwdPx"] or 0)
            if forward_price:
                await self.publish_underlying_price(instrument_name, forward_price)

    async def handle_mark_price(self, message):
        # logger.info(f"handle_mark_price {message}")
        data = message["data"]
        for item in data:
            instrument_name = item["instId"]
            inst_type = item["instType"]
            mark_price = float(item["markPx"])
            instrument_name = InstrumentConverter.to_system(instrument_name)
            self.mark_price_cache[instrument_name] = mark_price
            # logger.info(f"publish_mark_price {instrument_name}, {mark_price}")
            # publish_mark_price BTC-USDT-30SEP22, 43162.653502533016
            await self.publish_mark_price(instrument_name, mark_price)
            # 对于 FUTURES 和 SWAP 类型需要更新 mark_price（由于 handle_opt_summary 中只处理了 OPTION 类型）
            if inst_type in [OK_INST_TYPE.SWAP, OK_INST_TYPE.FUTURES]:
                self.orderbook_manager.change_ticker(instrument_name, None, mark_price, None, None, None)

    async def handle_funding_rate(self, message):
        """
        获取永续合约资金费率，30秒到90秒内推送一次数据


        {
            "arg": {
                "channel": "funding-rate",
                "instId": "BTC-USD-SWAP"
            },
            "data": [
                {
                    "fundingRate": "0.0001515",
                    "fundingTime": "1622822400000",
                    "instId": "BTC-USD-SWAP",
                    "instType": "SWAP",
                    "nextFundingRate": "0.00029",
                    "nextFundingTime": "1622851200000"
                }
            ]
        }
        """
        # logger.info(f"handle_funding_rate {message}")
        data = message["data"]
        for item in data:
            instrument_name = InstrumentConverter.to_system(item["instId"])
            funding_rate = item["fundingRate"]
            next_funding_rate = item["nextFundingRate"]
            ms = int(item["fundingTime"])
            next_ms = int(item["nextFundingTime"])
            await self.publish_funding_rate(instrument_name, funding_rate, next_funding_rate, ms, next_ms)

    async def handle_candle(self, message):
        """ic| message: {'arg': {'channel': 'candle30m', 'instId': 'ETH-USDC-SWAP'},
        'data': [['1678332600000',
                  '1540.86',
                  '1540.86',
                  '1540.86',
                  '1540.86',
                  '0',
                  '0',
                  '0',
                  '0']]}"""
        arg = message["arg"]
        granularity = arg["channel"].replace("candle", "")  # 1m/30m/1H/1M...
        granularity = granularity.replace("M", "MONTH")  # M和m做区分，m是分钟，M是月。 区分为后面upper
        data_type = "kline" if granularity == "1m" else f"kline_{granularity}"
        instrument_name = InstrumentConverter.to_system(arg["instId"])
        data = message["data"]
        for item in data:
            kline = self._parse_kline_message(instrument_name, raw_data=item)
            topic = self.build_topic(instrument_name, data_type=data_type)
            # logger.info(f"publish_kline {topic}, {kline}")
            # publish_kline ETH-USDT-PERPETUAL, KLine(current_ms=1649644926020, open=3172.09, close=3172.33,
            await self.publish_kline(topic=topic, payload=kline._asdict())

    async def handle_trades(self, message):
        arg = message["arg"]
        instrument_name = InstrumentConverter.to_system(arg["instId"])
        data = message["data"]
        for item in data:
            topic = self.build_topic(instrument_name, data_type="trade")
            sz = float(item["sz"])  # okx对于sz的定义是： 现货是币数，合约和期权是张数
            if self.SUBJECT_TYPE in (config.SUBJECT_TYPE.SWAP_USD, config.SUBJECT_TYPE.FUTURE_USD):
                # 币本位：amount是USD数量
                amount = SizeConvertor.to_system(sz, instrument_name, self.SUBJECT_TYPE, force_convert=True)
            else:
                # 现货、U本位、期权：amount是币数
                amount = SizeConvertor.to_system(sz, instrument_name, self.SUBJECT_TYPE)

            await self.publish_trade(
                topic,
                Trade(
                    trade_seq=item["tradeId"],
                    date_ms=item["ts"],
                    price=float(item["px"]),
                    amount=amount,
                    iv=None,
                    instrument_name=instrument_name,
                    direction=1 if item["side"] == "buy" else -1,  # buy=1, sell=-1
                    mark_price=None,
                    index_price=None,
                ).to_json(),
            )

    @classmethod
    def _parse_kline_message(cls, instrument_name, raw_data) -> KLine:
        """
        [
          "1629993600000", # ts Unix时间戳的毫秒数格式
          "42500",  # open
          "48199.9", # high
          "41006.1", # low
          "41006.1", # close
          "3587.41204591", # 交易量，以张为单位 如果是衍生品合约，数值为合约的张数。
                           如果是币币/币币杠杆，数值为交易货币的数量 （base) 量
          "166741046.22583129" # 交易量，以币为单位
                               如果是衍生品合约，数值为交易货币的数量。
                               如果是币币/币币杠杆，数值为计价货币的数量。 (quote) 钱
          "166741046.22583129", 交易量，以计价货币为单位
                                如：BTC-USDT 和 BTC-USDT-SWAP, 单位均是 USDT；
                                BTC-USD-SWAP 单位是 USD
          "0"  K线状态  0 代表 K 线未完结，1 代表 K 线已完结。
        ]
        """
        current_ms = int(time.time() * 1000)
        kline = KLine(
            current_ms=current_ms,
            open=float(raw_data[1]),
            close=float(raw_data[4]),
            high=float(raw_data[2]),
            low=float(raw_data[3]),
            amount=float(raw_data[6]),
            vol=float(raw_data[5]),  # 张数, 按需提供转换
            data_us=int(raw_data[0]),
            instrument_name=instrument_name,
        )
        return kline

    async def handle_books(self, message):
        # print(self.orderbook_manager.orderbooks)
        arg = message["arg"]
        #  channel = arg['channel']
        if "action" in message:
            action = message["action"]
        else:
            # books5 等 只有全量
            action = "snapshot"

        instId = arg["instId"]
        instrument_name = InstrumentConverter.to_system(instId)
        data = message["data"]

        contract_size = float(self.instruments_info[instId]["ctMult"] or 1) * float(
            self.instruments_info[instId]["ctVal"] or 1
        )

        if action == "snapshot":  # snapshot：全量
            for item in data:
                timestamp = int(item["ts"])
                top_bids = item["bids"][: self.orderbook_max_depth]
                top_asks = item["asks"][: self.orderbook_max_depth]
                # 合约 level=["411.8","10", "0","4"] 411.8为深度价格，10为此价格的合约张数，0该字段已弃用(始终为0)，4为此价格的订单数量
                # 现货 level=["411.8", "10", "0", "4"] 411.8为深度价格，10为此价格的币的数量，0该字段已弃用(始终为0)，4为此价格的订单数量
                bids = {float(price): float(amount) * contract_size for price, amount, *_ in top_bids}
                asks = {float(price): float(amount) * contract_size for price, amount, *_ in top_asks}
                self.orderbook_manager.snapshot(instrument_name, bids, asks, None, timestamp)
        else:  # update：增量
            orderbook = self.orderbook_manager.get(instrument_name)
            for update in data:
                timestamp = int(update["ts"])
                for side in ("bids", "asks"):
                    for price, amount, *_ in update[side]:
                        price = float(price)
                        amount = float(amount) * contract_size
                        if amount == 0:
                            if price in orderbook[side]:
                                del orderbook[side][price]
                        else:
                            orderbook[side][price] = amount
                orderbook.timestamp = timestamp

        orderbook = self.orderbook_manager.get(instrument_name)
        topic = self.build_topic(instrument_name, subject_type=get_subject_by(instrument_name))
        json_orderbook = orderbook.to_json()
        json_orderbook["instrument_name"] = instrument_name

        await self.publish_book(topic, json_orderbook)

    def __getattr__(self, name):
        return self.handle_unknown

    async def dispatch_message(self, message):
        # logger.info(f"message=> {message}")
        message = json.loads(message)
        #  arg = message['arg']
        #  channel = arg['channel']
        event = message.get("event", None) or message.get("arg", {}).get("channel", None) or "unknown"
        if event.startswith("candle"):
            event = "candle"
        elif event.startswith("books"):
            event = "books"
        await getattr(self, f"handle_{event}".replace("-", "_"))(message)
        self.last_updated_at = time.time()

    async def send(self, method, params=None, ignore_response=False):

        msg = self._build_message(method, params=params)
        await self.websocket.send(msg)

    def get_url(self):
        return self.get_public_url()

    @classmethod
    def get_rest_url(cls):
        return "https://www.okx.com"

    def get_base_url(self):
        url = settings.OKEX_WS_URL
        if not url:
            if settings.SPIDER_TESTNET:
                url = "wss://wspap.okx.com:8443"
            else:
                url = "wss://ws.okx.com:8443"
        return url

    def get_public_url(self):
        url = self.get_base_url() + "/ws/v5/public"
        if settings.SPIDER_TESTNET:
            url += "?brokerId=9999"
        return url

    def get_private_url(self):
        url = self.get_base_url() + "/ws/v5/private"
        if settings.SPIDER_TESTNET:
            url += "?brokerId=9999"
        return url

    @classmethod
    def _http_get(cls, url, params=None):
        if params is None:
            params = {}
        logger.info("http_get %s", url)
        headers = {}
        if settings.SPIDER_TESTNET:
            headers["X-SIMULATED-TRADING"] = "1"
        resp = requests.get(url, params=params, headers=headers).json()
        logger.debug("_http_get url:%s  params:%s  resp:%s", url, params, resp)
        if resp["code"] != "0":
            logger.error("_http_get failed ==> %s", resp)
            raise Exception("get %s failed, resp:%s" % (url, resp))
        return resp

    @classmethod
    def get_all_currencies(cls) -> List[str]:
        url = cls.get_rest_url() + "/api/v5/public/instruments?instType=SWAP"
        resp = cls._http_get(url)
        currencies = []
        for item in resp["data"]:
            currencies.append(item["instId"].split("-")[0])
        currencies = list(set(currencies))
        currencies.sort()
        return currencies

    @classmethod
    def get_funding_rate(cls, instrument_name) -> Union[FundingRate, dict]:
        instId = InstrumentConverter.to_exchange(instrument_name)
        url = cls.get_rest_url() + "/api/v5/public/funding-rate?instId=" + instId
        resp = cls._http_get(url)
        # {
        #    "code": "0",
        #    "data": [
        #        {
        #            "fundingRate": "0.0001515",
        #            "fundingTime": "1622822400000",
        #            "instId": "BTC-USD-SWAP",
        #            "instType": "SWAP",
        #            "nextFundingRate": "0.00029",
        #            "nextFundingTime": "1622851200000"
        #        }
        #    ],
        #    "msg": ""
        # }
        if len(resp["data"]) == 0:
            return resp
        return FundingRate(
            instrument_name=instrument_name,
            funding_rate=float(resp["data"][0]["fundingRate"]),
            funding_time=int(resp["data"][0]["fundingTime"]),
            next_funding_rate=float(resp["data"][0]["nextFundingRate"]),
        )

    @classmethod
    def get_funding_rate_history(cls, instrument_name, start_ms, end_ms, limit=100) -> List[FundingRate]:
        instId = InstrumentConverter.to_exchange(instrument_name)
        url = cls.get_rest_url() + "/api/v5/public/funding-rate-history?instId=" + instId
        url += "&before=%s" % int(start_ms)
        url += "&after=%s" % int(end_ms)
        url += "&limit=%s" % int(limit)
        resp = cls._http_get(url)
        result = []
        for data in resp["data"]:
            rate = FundingRate(
                instrument_name=instrument_name,
                funding_rate=float(data["fundingRate"]),
                funding_time=int(data["fundingTime"]),
            )
            result.append(rate)
        return result

    @classmethod
    def get_candles(cls, instrument_name: str, bar: str, start_ms, end_ms, limit=300) -> List[KLine]:
        """
        获取K线数据
        @param bar: 时间粒度，默认值1m
            如 [1m/3m/5m/15m/30m/1H/2H/4H]
            香港时间开盘价k线：[6H/12H/1D/2D/3D/1W/1M/3M/6M/1Y]
            UTC时间开盘价k线：[/6Hutc/12Hutc/1Dutc/2Dutc/3Dutc/1Wutc/1Mutc/3Mutc/6Mutc/1Yutc]
        """
        inst_id = InstrumentConverter.to_exchange(instrument_name)
        params = {"instId": inst_id, "limit": limit}
        if bar:
            params["bar"] = bar
        if start_ms:
            params["before"] = start_ms
        if end_ms:
            params["after"] = end_ms
        url = cls.get_rest_url() + "/api/v5/market/candles"
        resp = cls._http_get(url, params)
        result = []
        for data in resp["data"]:
            result.append(cls._parse_kline_message(instrument_name, data))
        return result

    @classmethod
    def get_position_tiers(
        cls, subject: str, trade_mode: str, currency=None, instrument_name=None, tier=None
    ) -> List[PositionTier]:
        """
        获取仓位档位 (提示，现货没有仓位）
        api doc: https://www.okx.com/docs-v5/zh/#rest-api-public-data-get-position-tiers
        """
        params = {
            "instType": okInstType[subject],
            "tdMode": trade_mode,
        }
        if subject in (config.SUBJECT_TYPE.option() + config.SUBJECT_TYPE.swap() + config.SUBJECT_TYPE.future()):
            if currency:
                uly = get_uly(currency, subject)
                params["uly"] = uly
            else:
                raise Exception("currency is needed for swap/future/option")
        if subject in config.SUBJECT_TYPE.swap():
            params["instFamily"] = get_inst_family(currency, subject)

        if subject == config.SUBJECT_TYPE.MARGIN.name:
            if instrument_name:
                params["instId"] = instrument_name
            else:
                raise Exception("inst is needed for margin")

        if tier:
            params["tier"] = tier

        url = cls.get_rest_url() + "/api/v5/public/position-tiers"
        resp = cls._http_get(url, params)
        result = []
        for data in resp["data"]:
            result.append(
                PositionTier(
                    currency=data.get("uly", "").split("-")[0],
                    instrument_name=data.get("instId", ""),
                    tier=data.get("tier", ""),
                    min_size=data.get("minSz", ""),
                    max_size=data.get("maxSz", ""),
                    mmr=data.get("mmr", ""),
                    imr=data.get("imr", ""),
                    max_lever=data.get("maxLever", ""),
                    option_margin_factor=data.get("optMgnFactor", ""),
                    quote_max_loan=data.get("quoteMaxLoan", ""),
                    base_max_loan=data.get("baseMaxLoan", ""),
                )
            )
        return result

    @classmethod
    def systime(cls) -> SysTime:
        url = cls.get_rest_url() + "/api/v5/public/time"
        rsp = cls._http_get(url)
        for data in rsp["data"]:
            return SysTime(
                ts=int(data.get("ts") or 0),
            )

    @classmethod
    def proxy_call(cls, name: str, params=None):
        if params is None:
            params = {}
        url = cls.get_rest_url() + name
        return cls._http_get(url, params)

    @classmethod
    def get_public_trades(cls, instrument_name: str, limit: int = 100) -> List[PublicTrade]:
        """
        获取公共成交数据
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-market-data-get-trades
        @param instrument_name: EE交易对
        @param limit: 结果集数量，最大为500，不填默认返回100条
        @return:
        """

        inst_id = InstrumentConverter.to_exchange(instrument_name)
        params = {"instId": inst_id, "limit": limit}
        url = cls.get_rest_url() + "/api/v5/market/trades"
        resp = cls._http_get(url, params)
        result = []
        for data in resp["data"]:
            subject = get_subject_by(instrument_name)
            # EE对于币本位：amount单位是USD (okex的单位是张数)
            # EE对于U本位和期权：amount单位是币数 （okex的单位是张数）
            force_convert = (
                True if subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name) else False
            )
            amount = SizeConvertor.to_system(
                size=float(data.get("sz") or 0.0),
                subject=subject,
                force_convert=force_convert,
                system_instrument=instrument_name,
            )
            side = config.SIDE.SELL if data["side"] == "sell" else config.SIDE.BUY
            result.append(
                PublicTrade(
                    instrument_name=instrument_name,
                    trade_id=data.get("tradeId"),
                    price=data.get("px"),
                    amount=amount,
                    side=side,
                    timestamp=data.get("ts"),
                )
            )
        return result

    @classmethod
    def get_public_tickers(
        cls, subject: str, currency: Optional[str] = None, instrument_name: Optional[str] = None
    ) -> List[PublicTicker]:
        """
        获取产品行情信息
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-market-data-get-tickers # 获取所有产品行情信息
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-market-data-get-ticker # 获取单个产品行情信息
        """
        if instrument_name:
            inst_id = InstrumentConverter.to_exchange(instrument_name)
            params = {"instId": inst_id}
            url = cls.get_rest_url() + "/api/v5/market/ticker"
        else:
            params = {"instType": okInstType[subject], "uly": get_uly(currency, subject) if currency else None}
            url = cls.get_rest_url() + "/api/v5/market/tickers"

        resp = cls._http_get(url, params)
        result = []
        data = resp["data"]
        for item in data:
            instrument_name_ = instrument_name or InstrumentConverter.to_system(item["instId"])
            subject_ = get_subject_by(instrument_name_)
            if subject != subject_:
                continue
            result.append(
                PublicTicker(
                    subject=subject,
                    instrument_name=instrument_name_,
                    last=item["last"],
                    last_size=item["lastSz"],
                    ask_price=item["askPx"],
                    ask_size=item["askSz"],
                    bid_price=item["bidPx"],
                    bid_size=item["bidSz"],
                    open_24h=item["open24h"],
                    high_24h=item["high24h"],
                    low_24h=item["low24h"],
                    volume_currency_24h=item["volCcy24h"],
                    volume_24h=SizeConvertor.to_system(item["vol24h"], instrument_name_, subject=subject),
                    sod_utc0=item["sodUtc0"],
                    sod_utc8=item["sodUtc8"],
                    ms=int(item["ts"]),
                )
            )
        return result


class OkexFutureHTTPClient(HTTPClientBase):
    def base_http_url(self) -> str:
        return "https://www.okx.com"

    async def get_delivery_prices(self, currency, quote):
        """获取交割价
        {
          "code": "0",
          "data": [
            {
              "details": [
                {
                  "insId": "BTC-USD-220429",
                  "px": "39521.0773885913106849",
                  "type": "Delivery"
                }
              ],
              "ts": "1651219200000"
            },
            {
              "details": [
                {
                  "insId": "BTC-USD-220422",
                  "px": "40609.6457379648313515",
                  "type": "Delivery"
                }
              ],
              "ts": "1650614400000"
            }
          ],
          "msg": ""
        }
        """
        # https://www.okx.com/docs-v5/en/#rest-api-public-data-get-delivery-exercise-history
        endpoint = f"/api/v5/public/delivery-exercise-history?uly={currency.upper()}-{quote.upper()}&instType=FUTURES"
        resp = await self.get(endpoint)
        data = list(filter(lambda item: item["details"][0]["type"].upper() == "DELIVERY", resp.get("data")))
        if not data:
            return False, []
        data = [dict(ts=x["ts"], **x["details"][0]) for x in data]
        return True, data

    def get_inverse_symbols(self):
        url = "https://www.okx.com/api/v5/public/instruments?instType=FUTURES"
        response = requests.get(url, timeout=10)
        data = response.json()
        symbols = set()
        for item in data["data"]:
            if item["ctValCcy"] == "USD":
                symbols.add(item["settleCcy"])
        return symbols


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(OkexWSClient.run(["BTC"]))
