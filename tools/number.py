import time
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from uuid import uuid4

from basis_alpha import config

# SOR 订单交易所前缀
SOR_EXCHANGE_PREFIX = 0

EXCHANGE_PREFIX_MAPPING = {
    config.EXCHANGE.DERIBIT.name: config.EXCHANGE.DERIBIT.prefix,
    config.EXCHANGE.BITCOM.name: config.EXCHANGE.BITCOM.prefix,
    config.EXCHANGE.OKEX.name: config.EXCHANGE.OKEX.prefix,
    config.EXCHANGE.BINANCE.name: config.EXCHANGE.BINANCE.prefix,
    config.EXCHANGE.HUOBI.name: config.EXCHANGE.HUOBI.prefix,
    config.EXCHANGE.BYBIT.name: config.EXCHANGE.BYBIT.prefix,
}

SUBJECT_PREFIX_MAPPING = {
    config.SUBJECT_TYPE.OPTION.name: config.SUBJECT_TYPE.OPTION.prefix,
    config.SUBJECT_TYPE.OPTION_USDT.name: config.SUBJECT_TYPE.OPTION_USDT.prefix,
    config.SUBJECT_TYPE.OPTION_USDC.name: config.SUBJECT_TYPE.OPTION_USDC.prefix,
    config.SUBJECT_TYPE.SPOT.name: config.SUBJECT_TYPE.SPOT.prefix,
    config.SUBJECT_TYPE.SWAP_USD.name: config.SUBJECT_TYPE.SWAP_USD.prefix,
    config.SUBJECT_TYPE.FUTURE_USD.name: config.SUBJECT_TYPE.FUTURE_USD.prefix,
    config.SUBJECT_TYPE.FUTURE_USDT.name: config.SUBJECT_TYPE.FUTURE_USDT.prefix,
    config.SUBJECT_TYPE.SWAP_USDT.name: config.SUBJECT_TYPE.SWAP_USDT.prefix,
    config.SUBJECT_TYPE.SWAP_USDC.name: config.SUBJECT_TYPE.SWAP_USDC.prefix,
}
REVERSE_SUBJECT_PREFIX_MAP = {v: k for k, v in SUBJECT_PREFIX_MAPPING.items()}


def to_decimal(num, prec=None):
    """prec 精度"""
    if prec:
        try:
            num = round(num, prec)
        except InvalidOperation:
            pass
    return Decimal(str(num))


def decimal_round_down(num, prec=6):
    """
    不进行四舍五入的截断
    """
    prec = to_decimal(0.1) ** prec
    return float(Decimal(str(num)).quantize(Decimal(str(prec)), rounding=ROUND_DOWN))


def get_decimal_count(num):
    # 获取小数位数
    return abs(Decimal(str(num)).as_tuple().exponent)


def generate_order_id(subject):
    """
    根据subject的不同，返回不同的后缀的订单
    """
    order_id = generate_digit_id()
    # 去除 exchange_prefix 作为交易所编号的作用
    exchange_prefix = 1
    subject_suffix = SUBJECT_PREFIX_MAPPING[subject]
    return f"{order_id}{exchange_prefix}{subject_suffix}"


def get_subject_by_order_id(order_id):
    """
    根据订单 id 判断 subject 类型
    """
    subject_part = int(str(order_id)[-2:])  # e.g option subject_part is 60
    return REVERSE_SUBJECT_PREFIX_MAP.get(subject_part)


def generate_sor_order_id(subject):
    """
    SOR 订单 id, 返回 特殊 exchange 前缀
    """
    order_id = generate_digit_id()
    subject_prefix = SUBJECT_PREFIX_MAPPING[subject]
    # return f'{order_id}{SOR_EXCHANGE_PREFIX:02d}{subject_prefix}'
    return f"{order_id}{SOR_EXCHANGE_PREFIX}{subject_prefix}"


def generate_digit_id():
    """返回10^-6s级别时间戳"""
    ts = int(time.time() * 1000 * 1000)
    return ts


def generate_str_id():
    return str(uuid4()).replace("-", "")


def trim_zero(number):
    return str(number).rstrip("0").rstrip(".")


def float_equal(fa, fb, threshold=0.000001):
    return abs(float(fa) - float(fb)) < threshold


def float_gte(fa, fb, threshold=0.000001):
    return round(float(fa) - float(fb), 6) >= 0


def float_gt(fa, fb, threshold=0.000001):
    return round(float(fa) - float(fb), 6) > 0


class ExactFloat(float):
    """正确精度的float"""

    def __add__(self, other):
        return float(to_decimal(self) + to_decimal(other))

    def __radd__(self, other):
        return self + other

    def __sub__(self, other):
        return float(to_decimal(self) - to_decimal(other))

    def __rsub__(self, other):
        return float(to_decimal(other) - to_decimal(self))

    def __mul__(self, other):
        return float(to_decimal(self) * to_decimal(other))

    def __rmul__(self, other):
        return self * other

    def __truediv__(self, other):
        return float(to_decimal(self) / to_decimal(other))

    def __rtruediv__(self, other):
        return float(to_decimal(other) / to_decimal(self))
