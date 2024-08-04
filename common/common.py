"""
公用模块
1. 币对信息
2. 合约类型的持仓数据
"""
import asyncio
import json
import logging
from typing import Any, List, Literal, NamedTuple, Optional, Union

import redis
from django.conf import settings

redis_cache = redis.from_url(settings.REDIS_URL)
logger = logging.getLogger(__name__)


class InstrumentInfo:
    @classmethod
    def save(cls, exchange, subject, instrument_name, info: dict):
        key = f"{exchange}:{subject}:{instrument_name}:info"
        redis_cache.set(key, json.dumps(info), ex=60 * 60 * 24 * 365)

    @classmethod
    def get(cls, exchange, subject, instrument_name):
        key = f"{exchange}:{subject}:{instrument_name}:info"
        result = redis_cache.get(key)
        if not result:
            return {}
        return json.loads(result)


class OpenInterest:
    @classmethod
    def save(cls, exchange, subject, instrument_name, oi):
        key = f"{exchange}:{subject}:{instrument_name}:open_interest"
        payload = {
            "open_interest": oi,
        }
        redis_cache.set(key, json.dumps(payload), ex=60 * 60 * 24)

    @classmethod
    def get(cls, exchange, subject, instrument_name):
        key = f"{exchange}:{subject}:{instrument_name}:open_interest"
        result = redis_cache.get(key)
        if not result:
            return {}
        result = json.loads(result)
        return result


class OrderBookSide:
    BIDS = "bids"
    ASKS = "asks"


class IntervalTask:
    def __init__(self, func, interval: int, wait: int = 0):
        self.func = func
        self.interval = interval
        self.wait = wait

    def run_in_background(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self.run_forever())

    async def run_forever(self):
        if self.wait:
            await asyncio.sleep(self.wait)
        while True:
            try:
                await self.func()
                await asyncio.sleep(self.interval)
            except Exception as e:
                logger.exception(e, exc_info=True)
                await asyncio.sleep(5)


class IndexPrice(NamedTuple):
    base_currency: str
    quote_currency: str
    index_price: float
    ms: int


""" 定义通用的返回数据结构 """


class ACCOUNT_PROPERTY:
    POSITION = "position"
    SUMMARY = "summary"
    # 币安、huobi 使用单独的现货 summary
    SPOT_SUMMARY = "spot_summary"
    ORDER = "order"
    TRADE = "trade"
    TRADE_REDO = "trade_redo"
    PM_DETAIL = "pm_detail"
    SETTLEMENT = "settlement"
    DELIVERY_PRICE = "delivery_price"
    FEE_RATE = "fee_rate"
    # bitcom paradigm交易只有trade推送，使用trade信息构造order，以使EE内部状态机完囊
    TRADE_TO_ORDER = "trade_to_order"
    # 批量修改后的订单结果格式化
    BATCH_AMEND_ORDER = "batch_amend_order"


class OrderInfo(NamedTuple):
    """OrderInfo EE 内部Order信息定义 对标 class:XxxParentOrder 如 FutureUSDChildOrder

    重点说明:
    amount: 表示外部数量或张数. 由consumer调用OrderMachine(订单状态机)直接赋值到ChildOrder, 并由childorder 调整父订单filled_size
        SPOT现货/杠杆: 表示`coin`(SpotParentOrder的交易目标币为单位)的数量, 下单交易币的数量(delta)与size无需转换
        FutureUSD/SWAPUSD 币本位(期货)合约: 表示对应交易所该期贷合约的USD数量
            与amount/size相关的计算:
                下单交易总额(usd_delta)为 size
                下单交易币数量(delta, 估算值，实际值会波动)为 size/当前价格
                父订单张数ParentOrder.size = size
                EE及一些broker提供使用估算值下单接口， 提交订单时折算取整成标准张数。 这种接口只为提供下单方便
                下单需要整张下，而交易所成交可能非整张。
        FutureUSDT/SWAPUSDT U本位(期货)合约: 直接表示`coin`为单位的数量， 如 ETH-USDT-PERPETUAL size:0.3 表示 0.3个ETH, 无需转换
        option期权: 直接表示`coin`的数量， 下单交易币数量(delta)与size无需转换

        速记:
        只有币本位是以USD计量, 其他都以币计量.
        只有币本位父子订单size单位不一致。
        EE币本位以100USD为面值， 其他品种计量单位都是1个计量币
        其他broker币本位面值各异， 其他品种计量单位大部分是1个计量币(目前没看到其他规格的)
        币本位面值为1USD的有: deribit bitcom
        BTC币本位面值为100USD的有: binance okex
        ETH币本位面值为10USD的有: binance okex
    """

    instrument_name: str
    exchange: str
    exchange_order_id: str
    direction: Literal[1, -1]  # 方向 BUY=1, SELL=-1
    state: Union[str, int]
    amount: float
    price: float
    filled_amount: float
    avg_price: float  # 必填，在计算订单是否完全成交时候使用
    original_data: dict = None
    order_id: str = ""
    fee: float = 0  # 正数 说明我们是付费，负数说明是maker返佣金
    fee_asset: str = ""
    fee_map: dict = {}
    broker_fee_map: dict = {}
    advanced: str = ""
    created_at: int = 0  # ms
    updated_at: int = 0  # ms
    app_name: str = ""
    channel: int = 0


class Summary(NamedTuple):
    currency: str
    equity: float
    available_funds: float
    im: str  # format '80.33%', '2.4%'
    mm: str  # format '80.33%', '2.4%'
    leverage: float
    delta_total: float
    options_delta: float
    future_delta: float
    options_gamma: float
    options_theta: float
    options_vega: float
    options_value: float
    options_pl: float
    exchange: Optional[str] = ""
    initial_margin: Optional[float] = 0
    maintenance_margin: Optional[float] = 0
    cash_balance: Optional[float] = 0
    pnl: Optional[float] = 0
    updated_at_ts: Optional[int] = 0
    options_session_rpl: Optional[float] = 0
    options_session_upl: Optional[float] = 0
    session_rpl: Optional[float] = 0
    session_upl: Optional[float] = 0
    futures_pl: Optional[float] = 0
    futures_session_rpl: Optional[float] = 0
    futures_session_upl: Optional[float] = 0

    source: Optional[str] = ""
    invalid: Optional[list] = []


class Position(NamedTuple):
    instrument_name: str
    size: float
    direction: Literal[1, -1]  # 方向 BUY=1, SELL=-1
    kind: str
    subject: str

    currency: Optional[str] = ""  # 'BTC', 'DOT'
    quote_currency: Optional[str] = ""
    size_usd: Optional[float] = 0
    mark_price: Optional[float] = 0
    average_price: Optional[float] = 0
    delta: Optional[float] = 0
    gamma: Optional[float] = 0
    theta: Optional[float] = 0
    vega: Optional[float] = 0
    fee: Optional[float] = 0
    pnl: Optional[float] = 0
    options_value: Optional[float] = 0

    initial_margin: Optional[float] = 0
    maintenance_margin: Optional[float] = 0

    mark_price_usd: Optional[float] = 0
    average_price_usd: Optional[float] = 0
    index_price: Optional[float] = 0
    version: Optional[str] = ""

    released_pnl: Optional[float] = 0
    underlying_price: Optional[float] = 0
    invalid: Optional[list] = []

    unreleased_pnl: Optional[float] = 0
    funding: Optional[float] = 0  # 目前只有 deribit/bitcom 有返回（bitcom 计算 released pnl 需要算上 funding）


class Trade(NamedTuple):
    trade_id: str
    order_id: str
    instrument_name: str
    amount: float
    price: float
    side: Optional[Literal[1, -1]]  # 方向 BUY=1, SELL=-1
    fee: float
    is_maker: bool
    original_data: Optional[Any]
    created_at: int  # 交易所时间戳 (ms)

    iv: Optional[float] = 0
    currency: Optional[str] = ""  # 'BTC', 'DOT'
    fee_asset: Optional[str] = ""
    is_block_trade: Optional[bool] = False
    label: Optional[str] = ""
    app_name: str = ""
    channel: int = 0

    broker_fee: Optional[float] = 0
    broker_fee_asset: Optional[str] = ""


class PmEntry(NamedTuple):
    size: float
    pl_vec: List[float]
    instrument_name: str
    exp_tstamp: Optional[int] = None


class PmDetails(NamedTuple):
    vol_range: List[float]

    future_pls: List[float]
    future_entries: List[PmEntry]

    option_pls: List[float]
    option_entries: List[PmEntry]

    def to_dict(self):
        return dict(
            vol_range=self.vol_range,
            future_pls=self.future_pls,
            option_pls=self.option_pls,
            future_entries=[item._asdict() for item in self.future_entries],
            option_entries=[item._asdict() for item in self.option_entries],
        )


class Settlement(NamedTuple):
    timestamp: int  # ms
    instrument_name: str
    size: float
    settle_price: float
    settle_pnl: float  # for delivery
    original_data: dict

    session_upnl: Optional[float]  # for settlement
    session_rpnl: Optional[float]  # for settlement
    session_funding: Optional[float]  # for settlement and swap product
    type: str  # delivery/settlement


class DeliveryPrice(NamedTuple):
    delivery_price: float
    date: str
    instrument: str = ""


class FeeRate(NamedTuple):
    spot: Optional[dict] = None
    option: Optional[dict] = None
    swap_usd: Optional[dict] = None
    swap_usdt: Optional[dict] = None
    future_usd: Optional[dict] = None
    future_usdt: Optional[dict] = None

    def to_dict(self):
        fee_rete_dict = dict(
            spot=self.spot,
            option=self.option,
            swap_usd=self.swap_usd,
            swap_usdt=self.swap_usdt,
            future_usd=self.future_usd,
            future_usdt=self.future_usdt,
        )
        for k in list(fee_rete_dict.keys()):
            if fee_rete_dict[k] is None:
                del fee_rete_dict[k]
        return fee_rete_dict


class AmendOrder(NamedTuple):
    order_id: str
    exchange_order_id: Optional[str] = ""
    # 0 success, -1 fail
    ret_code: Optional[str] = ""
    ret_msg: Optional[str] = ""


class BatchAmendOrder(NamedTuple):
    # 0 success, -1 fail, 1 partial success
    ret_code: str
    ret_msg: str
    ret_data: Optional[dict] = {}


class OrderExchangeStatus(NamedTuple):
    order_id_not_exist: bool


class FundingRate(NamedTuple):
    instrument_name: str
    funding_rate: float
    funding_time: int  # Unix时间戳的毫秒数
    next_funding_rate: Optional[float] = 0  # 下一期预测资金费率


class FundingRateHistoryReq(NamedTuple):
    exchange: str
    instrument_name: str
    start_ms: int
    end_ms: int
    limit: int = 100


class PositionTier(NamedTuple):
    """
    同OKX接口：https://www.okx.com/docs-v5/zh/#rest-api-public-data-get-position-tiers
    uly	String	标的指数
    instId	String	币对
    tier	String	仓位档位
    minSz	String	该档位最少持仓数量 期权/永续/交割 最小持仓量 默认0
    maxSz	String	该档位最多持仓数量 期权/永续/交割
    mmr	String	维持保证金率
    imr	String	最低初始保证金率
    maxLever	String	最高可用杠杆倍数
    optMgnFactor	String	期权保证金系数 （仅适用于期权）
    quoteMaxLoan	String	计价货币 最大借币量（仅适用于杠杆），例如 BTC-USDT 里的 USDT最大借币量
    baseMaxLoan	String	交易货币 最大借币量（仅适用于杠杆），例如 BTC-USDT 里的 BTC最大借币量
    """

    currency: str
    instrument_name: str
    tier: str
    min_size: str
    max_size: str
    mmr: str
    imr: str
    max_lever: str
    option_margin_factor: str
    quote_max_loan: str
    base_max_loan: str


class SysTime(NamedTuple):
    ts: int  # 系统时间，Unix时间戳的毫秒数格式


class AccountMaxSize(NamedTuple):
    """
    instId	String	产品ID
    ccy	String	保证金币种
    maxBuy	String	币币/币币杠杆：最大可买的交易币数量
                    单币种保证金模式下的全仓杠杆订单，为交易币数量
                    交割/永续/期权：最大可开多的合约张数
    maxSell	String	币币/币币杠杆：最大可卖的计价币数量
                    单币种保证金模式下的全仓杠杆订单，为交易币数量
                    交割/永续/期权：最大可开空的合约张数
    """

    instrument_name: str
    currency: str
    max_buy: str
    max_sell: str


class GetCandlesReq(NamedTuple):
    exchange: str
    instrument_name: str
    subject: str = None
    bar: str = None  # 时间粒度，默认值1m
    start_ms: int = None  # 请求此时间戳之前（更旧的数据）的分页内容，传的值为对应接口的ts
    end_ms: int = None  # 请求此时间戳之后（更新的数据）的分页内容，传的值为对应接口的ts
    limit: int = 300  # 分页返回的结果集数量，最大为300


class GetPositionTiersReq(NamedTuple):
    exchange: str
    subject: str
    trade_mode: str  # 保证金模式
    currency: str = None
    instrument_name: str = None
    tier: str = None  # 查指定档位


class GetMaxsizeReq(NamedTuple):
    exchange: str
    subject: str
    instrument_name: str
    trade_mode: str
    currency: str = None
    price: str = None
    leverage: str = None
    un_spot_offset: bool = False


class GetInterestLimitsReq(NamedTuple):
    exchange: str
    subject: str
    type: str = None
    currency: str = None


class PrivateProxyCallReq(NamedTuple):
    exchange: str
    subject: str
    name: str
    params: dict = {}


class PublicTrade(NamedTuple):
    """
    交易所公共成交数据
    """

    instrument_name: str
    trade_id: str
    price: str
    amount: str
    side: str
    timestamp: str


class ErrorResp(NamedTuple):
    error_code: str
    error_resp: str


class PublicTicker(NamedTuple):
    subject: str
    instrument_name: str
    last: str  # 最新成交价
    last_size: str  # 最新成交的数量
    ask_price: str  # 卖一价
    ask_size: str  # 卖一价对应的量
    bid_price: str  # 买一价
    bid_size: str  # 买一价对应的数量
    open_24h: str  # 24小时开盘价
    high_24h: str  # 24小时最高价
    low_24h: str  # 24小时最低价
    volume_currency_24h: str  # 24小时成交量，以币为单位
    volume_24h: str  # 24小时成交量，以张为单位，后续按需提供张数转换
    sod_utc0: str  # 0点成交量，以币为单位
    sod_utc8: str  # 8点成交量，以币为单位
    ms: int  # 数据产生时间，Unix时间戳的毫秒数格式
