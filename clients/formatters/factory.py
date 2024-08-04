from basis_alpha import config
from clients.formatters.local import LocalFormatter
from clients.formatters.okex import OkexFormatter
from common.common import ACCOUNT_PROPERTY


class FormatMethod:
    POSITION = ACCOUNT_PROPERTY.POSITION
    SUMMARY = ACCOUNT_PROPERTY.SUMMARY
    ORDER = ACCOUNT_PROPERTY.ORDER
    TRADE = ACCOUNT_PROPERTY.TRADE
    PM_DETAIL = ACCOUNT_PROPERTY.PM_DETAIL
    SETTLEMENT = ACCOUNT_PROPERTY.SETTLEMENT
    DELIVERY_PRICE = ACCOUNT_PROPERTY.DELIVERY_PRICE
    FEE_RATE = ACCOUNT_PROPERTY.FEE_RATE
    TRADE_TO_ORDER = ACCOUNT_PROPERTY.TRADE_TO_ORDER
    BATCH_AMEND_ORDER = ACCOUNT_PROPERTY.BATCH_AMEND_ORDER


class FormatterFactory:
    FORMATTER_MAPPING = {
        f"{config.EXCHANGE.OKEX.name}.{config.SUBJECT_TYPE.OPTION.name}": OkexFormatter,
        f"{config.EXCHANGE.OKEX.name}.{config.SUBJECT_TYPE.OPTION_USDC.name}": OkexFormatter,
        f"{config.EXCHANGE.OKEX.name}.{config.SUBJECT_TYPE.FUTURE_USD.name}": OkexFormatter,
        f"{config.EXCHANGE.OKEX.name}.{config.SUBJECT_TYPE.FUTURE_USDT.name}": OkexFormatter,
        f"{config.EXCHANGE.OKEX.name}.{config.SUBJECT_TYPE.SWAP_USD.name}": OkexFormatter,
        f"{config.EXCHANGE.OKEX.name}.{config.SUBJECT_TYPE.SWAP_USDT.name}": OkexFormatter,
        f"{config.EXCHANGE.OKEX.name}.{config.SUBJECT_TYPE.SPOT.name}": OkexFormatter,
        f"{config.EXCHANGE.LOCAL.name}.{config.SUBJECT_TYPE.OPTION.name}": LocalFormatter,
        f"{config.EXCHANGE.LOCAL.name}.{config.SUBJECT_TYPE.OPTION_USDC.name}": LocalFormatter,
        f"{config.EXCHANGE.LOCAL.name}.{config.SUBJECT_TYPE.FUTURE_USD.name}": LocalFormatter,
        f"{config.EXCHANGE.LOCAL.name}.{config.SUBJECT_TYPE.SWAP_USD.name}": LocalFormatter,
        f"{config.EXCHANGE.LOCAL.name}.{config.SUBJECT_TYPE.SPOT.name}": LocalFormatter,
        f"{config.EXCHANGE.LOCAL.name}.{config.SUBJECT_TYPE.FUTURE_USDT.name}": LocalFormatter,
        f"{config.EXCHANGE.LOCAL.name}.{config.SUBJECT_TYPE.SWAP_USDT.name}": LocalFormatter,
    }

    @classmethod
    def get(cls, exchange, subject):
        key = f"{exchange}.{subject}"
        Formatter = cls.FORMATTER_MAPPING[key]
        return Formatter()

    @classmethod
    def format(cls, uid, exchange, subject, data, format_method):
        formatter = cls.get(exchange, subject)
        func = getattr(formatter, format_method)
        if not func:
            raise Exception(f"no formatter function, {format_method}")
        return func(data)
