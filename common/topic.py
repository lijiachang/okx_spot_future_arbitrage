"""
生成topic的位置，针对三个模块：
1. data souce
2. exchange hub
3. order manager
"""
import functools


def generate_exchange_hub_topic(account_id, exchange, method, currency, subject=None):
    """
    method:
    - SUMMARY/SPOT_SUMMARY 账户概况
    - ORDER 订单变更数据
    - POSITION  持仓
    currency: 币种
    subject: 交易品种
    """
    topic = f"EXECUTE_ENGINE.ACCOUNT.{exchange}.{account_id}.{method}.{currency}"
    if subject:
        topic = f"{topic}.{subject}"
    return topic.upper()


def generate_data_source_topic(exchange, subject, currency, instrument_name, data_type="book"):
    """
    统一管理生成要存储盘口数据的key
    data_type:
    - book  盘口数据
    - trade 成交数据
    """
    return f"EXECUTE_ENGINE.SPIDER.{exchange}.{subject}.{currency}.{instrument_name}.{data_type}".upper()


def generate_order_manager_topic(account_id, exchange, subject, currency, order_id, method):
    """
    method: PARENT/CHILDREN/TRADE
    """
    return f"EXECUTE_ENGINE.ORDER_MANAGER.{method}.{exchange}.{subject}.{currency}.{account_id}.{order_id}".upper()


def generate_raw_order_manager_topic(account_id, exchange, subject, currency, order_id, method):
    """非EE系统内下单，产生新的推送topic
    method: PARENT/CHILDREN/TRADE
    """
    return f"EXECUTE_ENGINE.RAW_ORDER_MANAGER.{method}.{exchange}.{subject}.{currency}.{account_id}.{order_id}".upper()


def generate_book_order_manager_topic(bid, exchange, subject, currency, order_id, method):
    """
    method: PARENT/CHILDREN/TRADE
    """
    return f"EXECUTE_ENGINE.BOOK_ORDER_MANAGER.{method}.{exchange}.{subject}.{currency}.{bid}.{order_id}".upper()


def generate_fee_rate_topic(account_id, exchange, subject="All"):
    """
    exchange: DERIBIT/BINANCE/BITCOM
    """
    topic = f"EXECUTE_ENGINE.ACCOUNT.{exchange}.{subject}.{account_id}.FEE_RATE"
    return topic.upper()


@functools.lru_cache(maxsize=4096)
def parse_topic_type(topic: str):
    if topic.startswith("EXECUTE_ENGINE.ACCOUNT"):
        return topic.split(".")[4]
    if topic.startswith("EXECUTE_ENGINE.SPIDER"):
        return topic.split(".")[6]
    if topic.startswith("EXECUTE_ENGINE.ORDER_MANAGER"):
        return topic.split(".")[2]
    if topic.startswith("EXECUTE_ENGINE.RAW_ORDER_MANAGER"):
        return topic.split(".")[2]
    return "-"


def parse_topic_get_exchange(topic: str):
    if topic.startswith("EXECUTE_ENGINE.ACCOUNT"):
        return topic.split(".")[2]
    return "-"


def parse_topic_get_currency(topic: str):
    if topic.startswith("EXECUTE_ENGINE.ACCOUNT"):
        return topic.split(".")[-1]
    return "-"


def generate_open_order_manager_topic(bid, subject, currency, order_id, action):
    """
    bid open order
    """
    return f"EXECUTE_ENGINE.OPEN_ORDER_MANAGER.{subject}.{currency}.{bid}.{order_id}.{action}".upper()
