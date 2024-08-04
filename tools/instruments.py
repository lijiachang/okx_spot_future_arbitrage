import datetime
import functools
import logging
import re
from typing import NamedTuple, Optional

from basis_alpha import config
from basis_alpha.config import SUBJECT_TYPE, CURRENCY
from tools.time_parse import MONTH_MAPPING

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=40960)
def get_subject_by(instrument_name):
    """通过EE内部的instrument_name提取出subject"""
    if re.match('.+-(USDT|BUSD)-PERPETUAL$', instrument_name):
        # BTC-USDT-PERPETUAL
        return SUBJECT_TYPE.SWAP_USDT.name
    elif re.match('.+(USDC-PERPETUAL)$', instrument_name):
        # BTC-USDC-PERPETUAL
        return SUBJECT_TYPE.SWAP_USDC.name
    elif re.match('.+-PERPETUAL$', instrument_name):
        # BTC-PERPETUAL
        return SUBJECT_TYPE.SWAP_USD.name
    elif re.match('.+-USDT-.+-[CP]$', instrument_name):
        # BTC-USDT-11MAR22-50000-P
        return SUBJECT_TYPE.OPTION_USDT.name
    elif re.match('.+-USDC-.+-[CP]$', instrument_name):
        # BTC-USDC-11MAR22-50000-P
        return SUBJECT_TYPE.OPTION_USDC.name
    elif re.match('.+-[CP]$', instrument_name):
        # BTC-11MAR22-50000-P
        return SUBJECT_TYPE.OPTION.name
    elif re.match(f'.+-({CURRENCY.spot_quote_exp()})$', instrument_name):
        # ETH-BTC
        return SUBJECT_TYPE.SPOT.name
    elif re.match(r'.+-USDC-\d{1,2}[A-Z]{3}\d{2}', instrument_name):
        # BTC-USDC-29APR22
        return SUBJECT_TYPE.FUTURE_USDC.name
    elif re.match(r'.+-USDT-\d{1,2}[A-Z]{3}\d{2}', instrument_name):
        # BTC-USDT-29APR22
        return SUBJECT_TYPE.FUTURE_USDT.name
    elif re.match(r'.+-\d{1,2}[A-Z]{3}\d{2}', instrument_name):
        # BTC-12MAY23
        return SUBJECT_TYPE.FUTURE_USD.name
    else:
        return None


def get_subject_by_v2(instrument_name):
    """用于检查 instrument name 和 subject 是否匹配"""
    subject = get_subject_by(instrument_name)
    if subject == SUBJECT_TYPE.FUTURE_USD.name and not re.match(r'[A-Z]{2,6}-\d{1,2}[A-Z]{3}\d{2}$', instrument_name):
        #     ok: BTC-27MAY22
        # not ok: BTC-CS-27MAY22 (from binance), BTC-27MAY22_ERROR
        return None
    return subject


class EEInstrument(NamedTuple):
    name: str  # ee instrument name  OR alias, eg: OPTION:BTC:BTC
    subject: str
    base: str
    quote: str

    price_tick: float = -1  # 下单价格精度
    size_tick: float = -1  # 下单数量精度(步长)
    size_min: float = -1  # 最小下单数量
    contract_value_currency: str = ""  # 计算合约价值时使用的币种
    #  contract  value = size * contract_value * contract_mult * contract_value_currency
    contract_value: float = -1  # 合约面值(一张的价值)
    contract_mult: float = -1  # 合约乘数
    settlement_currency: str = ""  # 结算货币

    def alias(self):
        return f'{self.subject}:{self.base}:{self.quote}'.upper()

    @classmethod
    def from_alias(cls, alias: str):
        items = alias.split(":")
        if len(items) != 3:
            return None
        if not getattr(config.SUBJECT_TYPE, items[0], None):
            return None
        return EEInstrument(alias, items[0], items[1], items[2])

    def complete_info(self, other):
        d = self._asdict()
        d['price_tick'] = other.price_tick
        d['size_tick'] = other.size_tick
        d['size_min'] = other.size_min
        d['contract_value_currency'] = other.contract_value_currency
        d['contract_value'] = other.contract_value
        d['contract_mult'] = other.contract_mult
        d['settlement_currency'] = other.settlement_currency
        return EEInstrument(**d)


@functools.lru_cache(maxsize=4096)
def parse_ee_instrument(name: str, exchange=None) -> Optional[EEInstrument]:
    """
        subject       system
        SWAP_USDT     'ETC-USDT-PERPETUAL'
        SWAP_USDC     'ETC-USDC-PERPETUAL'
        SWAP_USD      'ETC-PERPETUAL'
        SPOT          'BCD-BTC'
        FUTURE_USD    'FIL-8APR22'
        FUTURE_USDT   'FIL-USDT-8APR22'
        OPTION        "BTC-24JUN22-45000-C"
        OPTION_USDT        "BTC-USDT-24JUN22-45000-C"
        OPTION_USDC        "BTC-USDC-24JUN22-45000-C"
        @param name: EE内部的instrument_name
        @param exchange: 交易所名称, 注意对option quote_currency有特殊的适配
    """
    subject = ""
    base = ""
    quote = ""

    items = name.split("-")
    if len(items) < 2:
        logger.error('parse ee instrument_name "%s" error', name)
        return None

    if items[-1] == 'PERPETUAL':
        if len(items) == 2:
            subject = config.SUBJECT_TYPE.SWAP_USD.name
            base = items[0]
            quote = "USD"
            return EEInstrument(name, subject, base, quote)
        if len(items) != 3:
            return None
        base = items[0]
        quote = items[1]
        if items[1] == "USDC":
            subject = config.SUBJECT_TYPE.SWAP_USDC.name
        else:
            subject = config.SUBJECT_TYPE.SWAP_USDT.name
        return EEInstrument(name, subject, base, quote)

    if len(items) == 2:
        if re.match(r'\d{1,2}[A-Z]{3}\d{2}', items[1]):
            subject = config.SUBJECT_TYPE.FUTURE_USD.name
            base = items[0]
            quote = 'USD'
            return EEInstrument(name, subject, base, quote)
        subject = config.SUBJECT_TYPE.SPOT.name
        base = items[0]
        quote = items[1]
        return EEInstrument(name, subject, base, quote)
    if len(items) == 3:
        subject = config.SUBJECT_TYPE.FUTURE_USDT.name
        base = items[0]
        quote = items[1]
        return EEInstrument(name, subject, base, quote)

    # OPTION_USDT/OPTION_USDC
    if len(items) == 5:
        base = items[0]
        quote = items[1]
        if quote == 'USDT':
            subject = config.SUBJECT_TYPE.OPTION_USDT.name
        elif quote == 'USDC':
            subject = config.SUBJECT_TYPE.OPTION_USDC.name
        else:
            logger.error('parse ee instrument_name "%s" base currency error', name)
            return None
        return EEInstrument(name, subject, base, quote)

    # OPTION
    if len(items) == 4:
        subject = config.SUBJECT_TYPE.OPTION.name
        base = items[0]
        quote = base  # 对于deribit/bitcom 等交易所，期权的结算是币
        return EEInstrument(name, subject, base, quote)

    logger.error('parse ee instrument_name "%s" error: unknown subject!', name)
    return None


def get_quote_currency(instrument_name, exchange=None):
    instrument = parse_ee_instrument(instrument_name, exchange=exchange)
    if instrument:
        return instrument.quote
    return 'USD'


def check_expired(subject, instrument_name) -> bool:
    """检查该instrument_name是否已到期"""
    # 只有以下subject需要进一步检查日期
    subjects_to_check = config.SUBJECT_TYPE.expiration_date()

    # 如果subject不在需要检查的列表中，直接返回False
    if subject not in subjects_to_check:
        return False

    # 提取instrument_name中的日期部分
    year_s = mon_s = day_s = ''
    for part in instrument_name.split('-'):
        month_abbreviation = part[-5:-2]
        if month_abbreviation in MONTH_MAPPING:
            year_s = part[-2:]
            mon_s = MONTH_MAPPING[month_abbreviation]
            day_s = part[:-5]
            break

    if not year_s:
        return False

    instrument_date = datetime.datetime(int('20' + year_s), mon_s, int(day_s))
    # 将instrument_date与当前日期进行比较，如果instrument_date早于当前日期，则认为已到期
    return instrument_date.date() < datetime.date.today()



# if __name__ == '__main__':
#     print(parse_ee_instrument('BTC-BUSD-PERPETUAL'))
    print(get_subject_by('BTC-USDC-20JAN23-16000-C'))
#     print(parse_ee_instrument('BTC-DOMUSDT-PERPETUAL'))
