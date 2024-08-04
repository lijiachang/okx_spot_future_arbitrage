import functools
import logging
from typing import Sequence, List, Optional

from basis_alpha import config
# from basis_alpha.capacity import ExchangeBrokerCap

logger = logging.getLogger(__name__)

SPOT_CURRENCIES = config.CURRENCY.all_instrument_currency()[:]  # SPOT/SWAP_USDT
SPOT_CURRENCIES.remove('CAKE')
SPOT_CURRENCIES.remove('BNB')

SPOT_QUOTE_CURRENCIES = ('USDT', 'BTC')
OPTION_CURRENCIES = ("BTC", "ETH", "SOL")
FUTURE_USDT_CURRENCIES = [name.split('-')[0] for name in config.CURRENCY.future_usdt_accuracy_map().keys()]
FUTURE_USD_CURRENCIES = config.CURRENCY.FUTURE_USD_ROUND_SIZE_MAP.keys()  # FUTURE_USD/SWAP_USD

# 订阅账户余额的币种
ACCOUNT_SUMMARY_CURRENCIES = ('BTC', 'ETH')

OK_OPTION, OK_FUTURES, OK_SPOT, OK_SWAP = 'OPTION', 'FUTURES', 'SPOT', 'SWAP'

SUBJECT_MAP = {
    config.SUBJECT_TYPE.OPTION.name: OK_OPTION,
    config.SUBJECT_TYPE.OPTION_USDC.name: OK_OPTION,
    config.SUBJECT_TYPE.FUTURE_USD.name: OK_FUTURES,
    config.SUBJECT_TYPE.SWAP_USD.name: OK_SWAP,
    config.SUBJECT_TYPE.SPOT.name: OK_SPOT,
    config.SUBJECT_TYPE.FUTURE_USDT.name: OK_FUTURES,
    config.SUBJECT_TYPE.SWAP_USDT.name: OK_SWAP,
}


def get_uly(currency: str, subject: str) -> Optional[str]:
    """获取 uly（标的指数），仅适用于OKX的交割/永续/期权，不适用于现货"""
    currency = currency.upper()
    map_ = {
        config.SUBJECT_TYPE.FUTURE_USD.name: f'{currency}-USD',
        config.SUBJECT_TYPE.FUTURE_USDT.name: f'{currency}-USDT',
        config.SUBJECT_TYPE.SWAP_USD.name: f'{currency}-USD',
        config.SUBJECT_TYPE.SWAP_USDT.name: f'{currency}-USDT',
        config.SUBJECT_TYPE.SWAP_USDC.name: f'{currency}-USD',  # SWAP_USDC的uly也是{currency}-USD
        config.SUBJECT_TYPE.OPTION.name: f'{currency}-USD',
        config.SUBJECT_TYPE.OPTION_USDC.name: f'{currency}-USD',
    }
    return map_.get(subject)


def get_inst_family(currency, subject):
    """获取 instFamily(交易品种), 仅适用于OKX的交割/永续/期权，不适用于现货
    为支持USDC合约查询和订阅，OKX新增了参数instFamily（交易品种）"""
    map_ = {
        config.SUBJECT_TYPE.SWAP_USD.name: f'{currency}-USD',
        config.SUBJECT_TYPE.SWAP_USDT.name: f'{currency}-USDT',
        config.SUBJECT_TYPE.SWAP_USDC.name: f'{currency}-USDC',
        config.SUBJECT_TYPE.OPTION.name: f'{currency}-USD',
        config.SUBJECT_TYPE.OPTION_USDC.name: f'{currency}-USDC',
    }
    return map_[subject]


# class OkexCap(ExchangeBrokerCap):
#
#     @functools.lru_cache(maxsize=None)
#     def base_currencies(self, subject: str) -> Sequence[str]:
#         if subject == config.SUBJECT_TYPE.OPTION.name:
#             return OPTION_CURRENCIES
#         elif subject == config.SUBJECT_TYPE.OPTION_USDC.name:
#             return OPTION_CURRENCIES
#         elif subject in (config.SUBJECT_TYPE.SPOT.name, config.SUBJECT_TYPE.SWAP_USDT.name):  # okx U本位永续支持能力和现货相同
#             return SPOT_CURRENCIES
#         elif subject == config.SUBJECT_TYPE.FUTURE_USDT.name:
#             return FUTURE_USDT_CURRENCIES
#         elif subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name):
#             return FUTURE_USD_CURRENCIES
#
#     @functools.lru_cache(maxsize=None)
#     def quote_currencies(self, subject: str) -> Sequence[str]:
#         if subject == config.SUBJECT_TYPE.OPTION.name:
#             return OPTION_CURRENCIES
#         elif subject == config.SUBJECT_TYPE.OPTION_USDC.name:
#             return ['USDC']
#         elif subject == config.SUBJECT_TYPE.SPOT.name:
#             return SPOT_QUOTE_CURRENCIES
#         elif subject in (config.SUBJECT_TYPE.FUTURE_USDT.name, config.SUBJECT_TYPE.SWAP_USDT.name):
#             return ['USDT']
#         elif subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name):
#             return ["USD"]
#
#     @functools.lru_cache(maxsize=None)
#     def subjects(self) -> List[str]:
#         return list(SUBJECT_MAP.keys())
