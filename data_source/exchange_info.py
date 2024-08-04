import json
import re
from asyncio.log import logger
from collections import defaultdict
from functools import lru_cache
from typing import Dict, List

from redis.client import Redis

from common.capacity import InstrumentWithCap
from tools.instruments import EEInstrument, parse_ee_instrument


class ExchangeBase:
    def __init__(self, instrument_name: str):
        self.instrument_name = instrument_name

    def spot(self):
        """instrument_name e.g.:ETH-USDT"""
        return self.instrument_name.split("-")[0]

    def swap_usd(self):
        """instrument_name e.g.:ETH-PERPETUAL"""
        return self.instrument_name.split("-")[0]

    def swap_usdt(self):
        """instrument_name e.g.:BTCUSDT-PERPETUAL"""
        return self.instrument_name.split("USDT-")[0]

    def swap_usdc(self):
        """instrument_name e.g.:BTCUSDC-PERPETUAL"""
        return self.instrument_name.split("USDC-")[0]

    def future_usd(self):
        """instrument_name e.g.:BTC-24JUN22"""
        return self.instrument_name.split("-")[0]

    def future_usdt(self):
        """instrument_name e.g.:BTCUSDT-25MAR22"""
        return self.instrument_name.split("USDT-")[0]

    def option(self):
        """instrument_name e.g.:BTC-18MAR22-34000-C"""
        return self.instrument_name.split("-")[0]

    def option_usdt(self):
        """instrument_name e.g.:BTC-USDT-18MAR22-34000-C"""
        return self.instrument_name.split("-")[0]

    def option_usdc(self):
        """instrument_name e.g.:BTC-USDC-18MAR22-34000-C"""
        return self.instrument_name.split("-")[0]

    def index(self):
        """instrument_name e.g.:BTC-30DEC22"""
        return re.split("[-_]", self.instrument_name)[0]

    def unknown(self):
        """unknown subject:
        Let me guess
        """
        if "USDT-" in self.instrument_name:
            return self.instrument_name.split("USDT-")[0]
        else:
            return self.instrument_name.split("-")[0]  # 不包含-则返回string自己


class DERIBIT(ExchangeBase):
    pass


class BITCOM(ExchangeBase):
    def future_usd(self):
        """instrument_name e.g.:ETH-25MAR22-F"""
        return super(BITCOM, self).future_usd()


class OKEX(ExchangeBase):
    def swap_usdt(self):
        """BTC-USDT-PERPETUAL"""
        return self.instrument_name.split("-USDT-")[0]

    def future_usdt(self):
        """BTC-USDT-31MAR23"""
        return self.instrument_name.split("-USDT-")[0]


class HUOBI(ExchangeBase):
    pass


class BYBIT(ExchangeBase):
    pass


class BINANCE(ExchangeBase):
    pass


class BITGET(ExchangeBase):
    pass


class BITWELLEX(ExchangeBase):
    pass


class XDERI(ExchangeBase):
    pass


class KUCOIN(ExchangeBase):
    pass


class CurrencyGetter:
    """获取 instrument_name上的币种"""

    def __init__(self, instrument_name: str, subject_type: str = None, exchange: str = None):
        """
        @param instrument_name: 交易对
        @param subject_type: 下单品种
        @param exchange:交易所名称
        """
        self.instrument_name = instrument_name
        self.subject_type = subject_type
        self.exchange = exchange

    @staticmethod
    @lru_cache(40960)
    def get_currency(instrument_name: str, subject_type: str = None, exchange: str = None):
        exchange = str(exchange).upper() if exchange else ExchangeBase.__name__
        instrument_name = instrument_name.upper()
        subject_type = str(subject_type).lower() if subject_type else ExchangeBase.unknown.__name__
        base_currency = getattr(globals()[exchange](instrument_name), subject_type)()
        return base_currency

    def __repr__(self):
        return self.get_currency(self.instrument_name, self.subject_type, self.exchange)


class DataSourceCapManager:
    TTL = 3600

    @classmethod
    def cache_key(cls, exchange) -> str:
        return f"data_source_cap:{exchange}".upper()

    @classmethod
    @lru_cache(maxsize=10240)
    def cache_value_member(cls, topic: str) -> str:
        """
        @param topic: data_source mq topic formatted by basis_alpha/topic.py

        topic format:
        f'EXECUTE_ENGINE.SPIDER.{exchange}.{subject}.{currency}.{instrument_name}.{data_type}'
        """
        items = topic.split(".")
        if len(items) != 7:
            return ""
        data_type = items[-1]
        ins_name = items[-2]
        exchange = items[2]

        if data_type == "INDEX_PRICE":  # INDEX_PRICE的ins_name是BTC_USD
            return ""
        ins = parse_ee_instrument(ins_name, exchange=exchange)
        if not ins:
            return ""
        return f"{data_type}:{ins.name}".upper()

    @classmethod
    def load_all(cls, exchange: str, redis: Redis) -> List[InstrumentWithCap]:
        key = cls.cache_key(exchange)
        members = redis.zrangebyscore(key, "-inf", "+inf")
        if not members:
            return []

        name2cap = defaultdict(list)
        for member in members:
            s: str = member.decode()
            items = s.split(":", maxsplit=1)
            if len(items) != 2:
                continue
            # TODO: check items[0]
            name2cap[items[1]].append(items[0])

        rst = []
        for name, cap in name2cap.items():
            ins = parse_ee_instrument(name)
            if not ins:
                continue
            rst.append(InstrumentWithCap(ins, cap))
        return rst


class InstrumentInfoManager:
    TTL = 3600 * 24

    def __init__(self, exchange: str, info: EEInstrument):
        self.alias = info.alias()
        self.info = info
        self.exchange = exchange
        self._hashv = None
        self._key = None
        self._dumps_cache = None

    def __hash__(self) -> int:
        if not self._hashv:
            self._hashv = self.alias.__hash__() + id(InstrumentInfoManager) + self.exchange.__hash__()
        return self._hashv

    def __eq__(self, other) -> bool:
        return (self.alias == other.alias) and (self.exchange == other.exchange)

    def __str__(self):
        return str(self.info)

    def cache_key(self) -> str:
        if not self._key:
            self._key = self.generate_key(self.exchange, self.info)
        return self._key

    def cache_value(self) -> str:
        if not self._dumps_cache:
            dic = self.info._asdict()
            self._dumps_cache = json.dumps(dic)
        return self._dumps_cache

    @classmethod
    def generate_key(cls, exchange: str, info: EEInstrument) -> str:
        return f"instrument_info:{exchange}:{info.alias()}".upper()

    @classmethod
    def complete_info(cls, redis: Redis, exchange: str, ins_list: List[InstrumentWithCap]) -> List[InstrumentWithCap]:
        rst = []
        if not ins_list:
            return []

        alias2info: Dict[str, EEInstrument] = {}
        for ins_cap in ins_list:
            key = cls.generate_key(exchange, ins_cap.ins)
            info = alias2info.get(key)
            if not info:
                value = redis.get(key)
                if not value:
                    rst.append(ins_cap)
                    continue
                try:
                    info = EEInstrument(**json.loads(value.decode()))
                    alias2info[key] = info
                except (json.decoder.JSONDecodeError, TypeError) as e:
                    logger.error("complte_info decode redis data failed %s %s", type(e), e)
                    rst.append(ins_cap)
                    continue
            rst.append(InstrumentWithCap(ins_cap.ins.complete_info(info), ins_cap.public_topics))

        return rst


def load_data_source_info(redis: Redis, exchange: str) -> List[InstrumentWithCap]:
    return InstrumentInfoManager.complete_info(redis, exchange, DataSourceCapManager.load_all(exchange, redis))


def test_load_info():
    from django_redis import get_redis_connection

    client = get_redis_connection()
    for ins in load_data_source_info(client, "DERIBIT"):
        print(ins._asdict())


def test_exchange_info():
    ins1 = InstrumentInfoManager(EEInstrument("optionA", "OPTION", "btc", "btc", 0.1, 0.1, 0.1, "btc", 1))
    ins2 = InstrumentInfoManager(EEInstrument("optionB", "OPTION", "btc", "btc", 0.2, 0.2, 0.2, "btc", 1))
    s = set()
    s.add(ins1)
    s.add(ins2)
    print(s)
    print(ins1.cache_value())


if __name__ == "__main__":
    print(CurrencyGetter("ETH-USDT"))
    print(CurrencyGetter("BTCUSDT-PERPETUAL", subject_type="SWAP_USDT", exchange="BINANCE"))
    print(CurrencyGetter("ETH-PERPETUAL", subject_type="SWAP_USD", exchange="BINANCE"))
    print(CurrencyGetter("ETH-BTC", subject_type="SPOT", exchange="OKEX"))
    print(CurrencyGetter("ETH-25MAR22-F", subject_type="future_usd", exchange="BITCOM"))
    print(CurrencyGetter("BTCUSDC-PERPETUAL", subject_type="SWAP_USDC", exchange="BYBIT"))
    print(CurrencyGetter("BTC-USDT-31MAR23", subject_type="future_usdt", exchange="OKEX"))
