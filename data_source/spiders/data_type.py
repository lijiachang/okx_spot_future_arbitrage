from typing import NamedTuple


class KLine(NamedTuple):
    current_ms: int  # 当前时间戳
    open: float  # 开盘价
    close: float  # 收盘价格
    high: float  # 最高价
    low: float  # 最低价
    amount: float  # 成交量
    vol: float  # 交易量
    data_us: int  # 数据时间戳
    instrument_name: str


class Funding(NamedTuple):
    data_ms: int
    rate: float
    predict_rate: float
    period: str  # 结算周期
    calculate_us: int  # 下次结算时间
    current_us: int
