import json
import logging
from concurrent import futures
from typing import List, Dict

import redis
import requests
from django.conf import settings

from basis_alpha.config import Accuracy, EXCHANGE
from tools.class_tools import StrongSingleton
from tools.number import get_decimal_count

logger = logging.getLogger(__name__)
redis_cache = redis.from_url(settings.REDIS_URL)


class OKXInfoLoader(metaclass=StrongSingleton):
    """解析出okx货币的张数面值/下单精度"""
    SPOT_URL = 'https://www.okx.com/api/v5/public/instruments?instType=SPOT'
    SWAP_URL = 'https://www.okx.com/api/v5/public/instruments?instType=SWAP'
    FUTURE_URL = 'https://www.okx.com/api/v5/public/instruments?instType=FUTURES'
    # https://www.okx.com/api/v5/public/instruments?instType=OPTION&uly=BTC-USD

    def __init__(self):
        self.testnet = settings.TESTNET

        urls = [self.SPOT_URL, self.SWAP_URL, self.FUTURE_URL]
        executor = futures.ThreadPoolExecutor()
        results = executor.map(self.get_instruments_info, urls)
        spot_instruments, swap_instruments, future_instruments = results

        # U本位永续 张数面值map
        self.swap_usdt_contract_value_map = self.contract_value_parser(swap_instruments)
        # U本位合约 张数面值map
        self.future_usdt_contract_value_map = self.contract_value_parser(future_instruments)

        # U本位永续 下单精度
        self.swap_usdt_accuracy_map = self.contract_accuracy_parser(swap_instruments, filter_='USDT')
        # U本位合约 下单精度
        self.future_usdt_accuracy_map = self.contract_accuracy_parser(future_instruments, filter_='USDT')

        # 现货 下单精度
        self.spot_accuracy_map = self.spot_accuracy_parser(spot_instruments, filter_='USDT')
        self.spot_accuracy_map += self.spot_accuracy_parser(spot_instruments, filter_='BTC')
        self.spot_accuracy_map += self.spot_accuracy_parser(spot_instruments, filter_='USDC')

    def get_instruments_info(self, url: str) -> List[dict]:
        headers = {'x-simulated-trading': '1'} if self.testnet else None  # 模拟盘数据要加headers
        resp = requests.get(url, headers=headers).json()
        instruments_list = resp.get('data')
        exchange = EXCHANGE.OKEX.name
        subject = url.split('instType=')[-1]  # 注意：这里的subject是OKX交易所定义的，非EE内部
        net = 'testnet' if self.testnet else 'mainnet'

        if (resp.get('code') != "0") or (not instruments_list):
            # 访问rest接口失败，则从redis中获取
            logger.info("rest api get instruments failed ==> %s", resp)
            instruments_list = self.redis_get(exchange, subject, net)
        else:
            # 缓存到redis
            self.redis_save(exchange, subject, net, instruments_list)
        return instruments_list

    @staticmethod
    def redis_save(exchange: str, subject: str, net: str, instruments_list: List[dict]):
        key = f'{exchange}:{net}:{subject}:public_instruments_info'
        redis_cache.set(key, json.dumps(instruments_list))

    @staticmethod
    def redis_get(exchange: str, subject: str, net: str) -> List[dict]:
        key = f'{exchange}:{net}:{subject}:public_instruments_info'
        result = redis_cache.get(key)
        if not result:
            return []
        return json.loads(result)

    @staticmethod
    def contract_value_parser(instruments_info_list: list) -> Dict[str, int]:
        """从instrument信息中解析出 张数面值字段
        @return {币种: 张数面值}
        """
        return {instrument_info['ctValCcy']: instrument_info['ctVal'] for instrument_info in instruments_info_list
                if instrument_info['uly'].endswith('-USDT')}

    @staticmethod
    def contract_accuracy_parser(instruments_info_list: list, *, filter_: str) -> List[Accuracy]:
        """从swap/future instrument信息中解析出精度信息 生成Accuracy对象"""
        accuracy_list = []
        for instruments_info in instruments_info_list:
            name: str = instruments_info["uly"]  # "uly": "DOT-USD"
            if not name.endswith(filter_):
                continue
            price_accuracy: int = get_decimal_count(instruments_info['tickSz'])  # "tickSz": "0.01",
            size_accuracy: int = get_decimal_count(instruments_info['ctVal'])  # "ctVal": "0.1",
            size_min = None  # 暂不考虑, 通过交易所限制
            size_multiple: int = int(val) if (val := float(instruments_info['ctVal'])) >= 1 else None  # "ctVal": "0.1",

            accuracy_list.append(Accuracy(name, size_accuracy, price_accuracy, size_multiple, size_min))
        return accuracy_list

    @staticmethod
    def spot_accuracy_parser(instruments_info_list: list, *, filter_: str) -> List[Accuracy]:
        """从spot instrument信息中解析出精度信息 生成Accuracy对象"""
        accuracy_list = []
        for instruments_info in instruments_info_list:
            name: str = instruments_info["instId"]  # "instId": "ATOM-BTC",
            if not name.endswith(filter_):
                continue
            price_accuracy: int = get_decimal_count(instruments_info['tickSz'])  # "tickSz": "0.0000001",
            size_accuracy: int = get_decimal_count(instruments_info['lotSz'])  # "lotSz": "0.0001",
            size_min = None  # 暂不考虑, 通过交易所限制
            size_multiple = None

            accuracy_list.append(Accuracy(name, size_accuracy, price_accuracy, size_multiple, size_min))
        return accuracy_list
