import base64
import functools
import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, NamedTuple

from django.conf import settings
from pytz import utc

from basis_alpha import config
from basis_alpha.instrument_info import OKXInfoLoader
from tools.class_tools import StrongSingleton
from tools.instruments import get_subject_by
from tools.number import ExactFloat
from tools.time_parse import MONTH_MAPPING, REVERSE_MONTH_MAPPING

logger = logging.getLogger(__name__)


class Signer:
    def __init__(self, client_id, client_secret, client_passphrase):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client_passphrase = client_passphrase

    def get_signature(self, body="", method="GET", request_url="/users/self/verify", timestamp=None):
        if not timestamp:
            timestamp = f"{int(time.time())}"
        method = method.upper()
        request_url = request_url.replace("%2C", ",")  # 逗号不需要转义
        str_to_sign = f"{timestamp}{method}{request_url}{body}"
        logger.info(f"str_to_sign: {str_to_sign}")
        sign = base64.b64encode(
            hmac.new(self.client_secret.encode("utf-8"), str_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")

        return {"timestamp": timestamp, "sign": sign, "apiKey": self.client_id, "passphrase": self.client_passphrase}

    def get_signature_for_http(self, *args, **kwargs):
        timestamp = datetime.fromtimestamp(time.time()).astimezone(utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        data = self.get_signature(*args, timestamp=timestamp, **kwargs)
        return {
            "OK-ACCESS-KEY": data["apiKey"],
            "OK-ACCESS-SIGN": data["sign"],
            "OK-ACCESS-TIMESTAMP": data["timestamp"],
            "OK-ACCESS-PASSPHRASE": data["passphrase"],
        }


@functools.lru_cache(maxsize=1024)
def date_to_exchange(code_part):
    # 8APR22 -> 220408
    match_result = re.match(r"(?P<day>\d{1,2})(?P<month>[A-Z]{3})(?P<year>\d{2})", code_part)
    day = int(match_result["day"])
    month = match_result["month"]
    year = match_result["year"]
    month = MONTH_MAPPING[month]
    return f"{year}{month:02d}{day:02d}"


@functools.lru_cache(maxsize=1024)
def date_to_system(code_part):
    # 220408 -> 8APR22
    year = code_part[:2]
    month = int(code_part[2:4])
    day = int(code_part[4:6])
    month = REVERSE_MONTH_MAPPING[month]
    return f"{day}{month}{year}"


class InstrumentConverter:
    """
    由于Okex的instrument 格式和系统内不一致，会导致系统内其他模块对接有额外成本
    所以我们在这个类里面单独处理这种特殊的情况
    to_exchange: 转换成交易所支持的类型
    to_system: 转换成系统内的标准类型
    """

    # todo 一个优化点：增加subject参数，如果已知subject，可以做简单转换，避免正则匹配

    @classmethod
    @functools.lru_cache(maxsize=4096)
    def to_exchange(cls, system_code):
        """
        instType         system                 exchange
        perp            'ETC-USDT-PERPETUAL'    "ETC-USDT-SWAP"
        perp            'ETC-PERPETUAL'         "ETC-USD-SWAP"
        spot            'BCD-BTC'               "BCD-BTC"
        spot            'MDT-USDT'              "MDT-USDT"
        futures         'FIL-8APR22'            "FIL-USD-220408"
        futures         'FIL-USDT-8APR22'       "FIL-USDT-220408"
        futures         'FIL-USDC-8APR22'       "FIL-USDC-220408"
        option          "BTC-24JUN22-45000-C"   "BTC-USD-220624-45000-C"
        option          "SOL-24JUN22-95-C"      "SOL-USD-220624-95.0-C"              # add '.0' if price < 100
        option          "SOL-24JUN22-100-C"     "SOL-USD-220624-100-C"
        option          "BTC-USDC-24JUN22-45000-C"   "BTC-USDC-220624-45000-C"  # OPTION_USDC
        """
        # logger.info(f"convert_instrument: {system_code}")
        code_parts = system_code.split("-")
        if code_parts[1] in ["BTC"]:
            pass
        elif code_parts[-1] == "PERPETUAL":
            code_parts[-1] = "SWAP"
            if "USDT" not in system_code:
                # swap usd
                code_parts.insert(1, "USD")
        elif len(code_parts) == 4 and re.match(r"\d{1,2}[A-Z]{3}\d{2}", code_parts[1]):
            # for OPTION
            code_parts.insert(1, "USD")
            code_parts[2] = date_to_exchange(code_parts[2])
            # SOL add '.0' if price < 100
            code_parts[-2] = code_parts[-2] + ".0" if float(code_parts[-2]) < 100 else code_parts[-2]
        elif len(code_parts) == 5 and re.match(r"\d{1,2}[A-Z]{3}\d{2}", code_parts[2]):
            # for OPTION_USDC
            code_parts[2] = date_to_exchange(code_parts[2])
            # SOL add '.0' if price < 100
            code_parts[-2] = code_parts[-2] + ".0" if float(code_parts[-2]) < 100 else code_parts[-2]
        elif len(code_parts) == 2 and re.match(r"\d{1,2}[A-Z]{3}\d{2}", code_parts[-1]):
            # for future usd
            code_parts.insert(1, "USD")
            code_parts[-1] = date_to_exchange(code_parts[-1])
        elif len(code_parts) == 3 and re.match(r"\d{1,2}[A-Z]{3}\d{2}", code_parts[-1]):
            # for future usdt/future usdc
            code_parts[-1] = date_to_exchange(code_parts[-1])
        return "-".join(code_parts)

    @classmethod
    @functools.lru_cache(maxsize=4096)
    def to_system(cls, exchange_code):
        """
        面向 Deribit 的标准化
        instType    exchange                     system
        perp        "ETC-USDT-SWAP"             'ETC-USDT-PERPETUAL'
        perp        "ETC-USD-SWAP"              'ETC-PERPETUAL'
        spot        "BCD-BTC"                   'BCD-BTC'
        spot        "MDT-USDT"                  'MDT-USDT'
        futures     "FIL-USD-220408"            'FIL-8APR22'
        futures     "FIL-USDT-220408"           'FIL-USDT-8APR22'
        futures     "FIL-USDC-220408"           'FIL-USDC-8APR22'
        option      "BTC-USD-220624-45000-C"    "BTC-24JUN22-45000-C"
        option      "SOL-USD-220624-95.0-C"     "SOL-24JUN22-95-C"         # remove '.0'
        option      "SOL-USD-220624-100-C"     "SOL-24JUN22-100-C"
        option      "BTC-USDC-220624-45000-C"    "BTC-USDC-24JUN22-45000-C"  # OPTION_USDC
        """
        code_parts = exchange_code.split("-")
        if code_parts[1] == "USD":
            del code_parts[1]

        if code_parts[-1] == "SWAP":
            code_parts[-1] = "PERPETUAL"
        elif re.match(r"\d{6}", code_parts[-1]):
            # for FUTURE USD/FUTURE USDT/FUTURE USDC
            code_parts[-1] = date_to_system(code_parts[-1])
        elif len(code_parts) >= 4 and re.match(r"\d{6}", code_parts[-3]):
            # for OPTION
            code_parts[-3] = date_to_system(code_parts[-3])
            # format '95.0' to '95'
            code_parts[-2] = str(int(float(code_parts[-2])))
        return "-".join(code_parts)


class IndexConvertor:
    @classmethod
    @functools.lru_cache(maxsize=1024)
    def to_system(cls, exchange_code):
        """
        面向 Deribit 的标准化
        exchange     system
        ETH-USD      ETH_USD
        """
        return exchange_code.replace("-", "_")


class MultiplierMap(metaclass=StrongSingleton):
    """
    依据OKX 交易所instruments接口的ctVal字段:
    https://www.okx.com/api/v5/public/instruments?instType=SWAP
    https://www.okx.com/api/v5/public/instruments?instType=FUTURES
    依据OKX 交易所instruments接口的ctMult字段:
    https://www.okx.com/api/v5/public/instruments?instType=OPTION&uly=BTC-USD&instFamily=BTC-USD
    https://www.okx.com/api/v5/public/instruments?instType=OPTION&uly=BTC-USD&instFamily=BTC-USDC  USDC期权
    注意：请求以上接口是实盘的数据，模拟盘需要在请求头中添加x-simulated-trading: 1
    """

    # 模拟盘和实盘 币本位的张数面值相同
    MAP = {
        "FUTURE_USD": {"BTC": 100, "ETH": 10, "default": 10},  # 一张=100USD
        "SWAP_USD": {"BTC": 100, "ETH": 10, "default": 10},  # 一张=100USD
    }

    def __init__(self):
        logger.debug("begin to init okx MultiplierMap")
        # 从rest api中获取张数转换数据
        json_reader = OKXInfoLoader()

        # 模拟盘和实盘的期权和U本位合约存在差异
        if settings.TESTNET:  # 模拟盘
            self.MAP.update(
                {
                    # e.g. 'BTC': 0.001,  表示1张=0.001个BTC
                    "SWAP_USDT": json_reader.swap_usdt_contract_value_map,
                    # e.g. 'BTC': 0.01,  表示1张=0.01个BTC
                    "FUTURE_USDT": json_reader.future_usdt_contract_value_map,
                    "OPTION": {"BTC": 0.01, "ETH": 0.1, "default": 0.1},
                    "OPTION_USDC": {"BTC": 0.01, "ETH": 0.1, "default": 0.1},
                }
            )
        else:  # 实盘
            # 实盘的FUTURE_USDT和SWAP_USDT张数面值相同. PS:对于某些币种的FUTURE_USDT可能不支持
            # FUTURE_USDT or SWAP_USDT
            # e.g. 'BTC': 0.01 表示 1张=0.01个BTC
            future_or_swap = json_reader.swap_usdt_contract_value_map
            self.MAP.update(
                {
                    "SWAP_USDT": future_or_swap,
                    "FUTURE_USDT": future_or_swap,
                    "OPTION": {"BTC": 0.01, "ETH": 0.1, "default": 0.1},  # 1张=0.01个BTC
                    "OPTION_USDC": {"BTC": 0.01, "ETH": 0.1, "default": 0.1},  # 1张=0.01个BTC
                }
            )


class SizeConvertor:
    @classmethod
    @functools.lru_cache(maxsize=4096)
    def to_exchange(cls, size, system_instrument, subject=None):
        """okx 接收的单位只能是张数，且为整数。比如1.0是错误的"""
        size = ExactFloat(size)
        if subject is None:
            subject = get_subject_by(system_instrument)
        currency = system_instrument.split("-")[0]  # 'BTC'

        if subject in (config.SUBJECT_TYPE.OPTION.name, config.SUBJECT_TYPE.OPTION_USDC.name):
            # OPTION
            contract_value_map = MultiplierMap().MAP[subject]
            contract_value = contract_value_map.get(currency, contract_value_map["default"])  # 合约乘数
            return int(size / contract_value)
        elif subject in (config.SUBJECT_TYPE.FUTURE_USDT.name, config.SUBJECT_TYPE.SWAP_USDT.name):
            # FUTURE_USDT or SWAP_USDT
            contract_value_map = MultiplierMap().MAP[subject]
            contract_value = contract_value_map.get(currency)  # 合约面值
            if contract_value is None:
                raise Exception(f"OKX {subject} nonsupport currency={currency}")
            return int(size / contract_value)
        elif subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name):
            # FUTURE_USD or SWAP_USD
            # Order count should be the integer multiples of the lot size
            # Note: contract size 需要是 '整数'
            contract_value_map = MultiplierMap().MAP[subject]
            contract_value = contract_value_map.get(currency, contract_value_map["default"])  # 合约面值
            return int(size / contract_value)  # USD数量转换为OKX的张数
        else:
            # SPOT
            return size

    @classmethod
    @functools.lru_cache(maxsize=4096)
    def to_system(cls, size, system_instrument, subject=None, force_convert=False, avg_price=None):
        size = ExactFloat(size)  # 正确精度的float
        if subject is None:
            subject = get_subject_by(system_instrument)
        currency = system_instrument.split("-")[0]  # 'BTC'

        if subject in (config.SUBJECT_TYPE.OPTION.name, config.SUBJECT_TYPE.OPTION_USDC.name):
            # OPTION
            contract_value_map = MultiplierMap().MAP[subject]
            contract_value = contract_value_map.get(currency, contract_value_map["default"])  # 合约乘数
            return size * contract_value  # 币数
        elif subject in (config.SUBJECT_TYPE.FUTURE_USDT.name, config.SUBJECT_TYPE.SWAP_USDT.name):
            # FUTURE_USDT or SWAP_USDT
            contract_value_map = MultiplierMap().MAP[subject]
            contract_value = contract_value_map.get(currency, 0)  # 合约面值
            if contract_value == 0:
                logger.error("get okx contract_value error: subject=%s currency=%s", subject, currency)
            if force_convert:
                # size_usd 需要
                # todo 去掉force_convert这个参数，如果需要size_usd，就自己去乘avg_price
                return float(size * contract_value * avg_price)  # USD数量
            else:
                return size * contract_value  # 币数
        elif subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name):
            # FUTURE_USD or SWAP_USD
            contract_value_map = MultiplierMap().MAP[subject]
            contract_value = contract_value_map.get(currency, contract_value_map["default"])  # 合约面值
            return size * contract_value  # USD数量
        else:
            # SPOT
            return size


class LendingRateHistory(NamedTuple):
    currency: str
    lending_amount: float  # 市场总接触量
    lending_rate: float  # 出借年利率
    lending_time: int  # Unix时间戳的毫秒数


class PurchaseRedempt(NamedTuple):
    currency: str
    amount: float  # 申购(赎回）数量
    side: str  # 操作类型
    rate: float  # 申购利率


class CurrencyItem(NamedTuple):
    currency: str
    can_deposit: bool  # 是否可充值, false表示不可链上充值，true表示可以链上充值
    can_internal: bool  # 是否可内部转账，false表示不可内部转账，true表示可以内部转账
    can_withdraw: bool  # 是否可提币，false表示不可链上提币，true表示可以链上提币
    chain: str  # 币种链信息
    deposit_quota_fixed: float  # 充币固定限额，单位为BTC 没有充币限制则返回""
    main_net: str  # 是否主网
    max_fee: float  # 最大提币手续费数量
    max_withdraw: float  # 币种单笔最大提币量
    min_deposit: float  # 币种单笔最小充值量
    min_deposit_arrival_confirm: float  # 充值到账最小网络确认数。币已到账但不可提。
    min_fee: float  # 最小提币手续费数量
    min_withdraw: float  # 币种单笔最小提币量
    min_withdraw_unlock_confirm: float  # 提现解锁最小网络确认数
    name: str  # 中文名
    need_tag: str  # 链是否需要tag/memo
    used_deposit_quota_fixed: float  # 已用充币固定额度，单位为BTC 没有充币限制则返回""
    used_withdraw_quota: float  # 过去24小时已用提币额度, 单位BTC
    withdraw_quota: float  # 过去24小时内提币额度, 单位BTC
    withdraw_tick_sz: int  # 提币精度,表示小数点后的位数


class SavingBalanceItem(NamedTuple):
    currency: str
    amount: float  # 币种数量
    loan_amount: float  # 已出借数量
    rate: float  # 最新出借利率
    redempt_amount: float  # 赎回数量
    pending_amount: float  # 未出借数量
    earnings: float  # 币种持仓收益


class BalanceItem(NamedTuple):
    currency: str
    available: float  # 可用余额
    balance: float  # 余额
    frozen: float  # 冻结（不可用）


class TransferItem(NamedTuple):
    currency: str  # 划转币种
    trans_id: str  # 划转 ID
    from_: str  # 转出账户
    to: str  # 转入账户
    amount: float  # 划转量
    client_id: str  # 客户自定义ID


@dataclass
class PositionMode:
    """持仓模式"""

    position_mode: str  # 持仓方式


@dataclass
class AccountConfig(PositionMode):
    """账户配置"""

    account_level: str  # 账户层级
    auto_loan: bool  # 是否自动借币
    contract_isolated_mode: str  # 衍生品的逐仓保证金划转模式
    greeks_type: str  # 希腊字母展示方式
    level: str  # 账户等级
    level_temporary: str  # 临时账户等级
    liquidation_gear: str  # 强平档位
    margin_isolated_mode: str  # 币币杠杆的逐仓保证金划转模式
    spot_offset_type: str  # 现货对冲类型
    # uid: str


@dataclass
class Leverage:
    """杠杆倍数"""

    instrument_name: str
    lever: str  # 杠杆倍数
    margin_mode: str  # 保证金模式
    position_side: str  # 持仓方向


@dataclass
class TradeFee:
    """交易费率"""

    # category: str # okx: 币种类别，此参数已废弃
    delivery: str  # 交割费率
    exercise: str  # 行权费率
    subject: str
    # is_special: str
    level: str  # 手续费等级
    maker: str  # USDT&USDⓈ&Crypto 交易区挂单手续费率，永续和交割合约时，为币本位合约费率
    maker_u: str  # USDT 合约挂单手续费率，仅适用于交割/永续
    maker_usdc: str  # USDC 交易区的挂单手续费率
    taker: str  # USDT&USDⓈ&Crypto 交易区的吃单手续费率，永续和交割合约时，为币本位合约费率
    taker_u: str  # USDT 合约吃单手续费率，仅适用于交割/永续
    taker_usdc: str  # USDC 交易区的吃单手续费率
    timestamp: str  # 数据返回时间


@dataclass
class Limits:
    """限额"""

    currency: str
    loan_quota: str  # 借币限额
    available_loan: str  # 当前账户剩余可用（锁定额度内）
    possess_loan: str  # 当前账户负债占用（锁定额度内）
    used_loan: str  # 当前账户已借额度


@dataclass
class BorrowRepay(Limits):
    """借币还币"""

    amount: str  # 借/还币的数量
    side: str  # 借币还币方向,borrow：借币，repay：还币


@dataclass
class InterestLimitsRecords(Limits):
    """借币利率与限额, 各币种详细信息"""

    interest: str  # 已计未扣利息
    rate: str  # 日利率
    surplus_limit: str  # 剩余可借
    used_limit: str  # 已借额度


@dataclass
class InterestLimits:
    """借币利率与限额"""

    debt: str  # 当前负债，单位为USDT
    interest: str  # 当前记息，单位为USDT
    next_discount_time: str  # 下次扣息时间
    next_interest_time: str  # 下次计息时间
    records: List[InterestLimitsRecords]  # 各币种详细信息


@dataclass
class MaxLoan:
    """最大借币"""

    instrument_name: str
    margin_mode: str  # 仓位类型
    margin_currency: str  # 保证金币种
    max_loan: str  # 最大可借
    currency: str  # 币种
    side: str  # 订单方向


@dataclass
class FundingBill:
    """资金费账单"""

    bill_id: str  # 账单ID
    currency: str  # 币种
    instrument_name: str
    pnl: float  # 资金费
    size: float  # 仓位数量
    price: float  # 标记价格
    ts: int  # 创建时间戳


TransType = {
    "inner": 0,  # 账户间划转
    "only_son": {  # 只能用在子账户APIKey
        "s2m": 3,
        "s2s": 4,
    },
    "only_mom": {"m2s": 1, "s2m": 2},  # 只适用母账户API KEY
}

AccountType = {
    "asset": 6,
    "trading": 18,
}
