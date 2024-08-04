import itertools
from typing import List


class PrefixObject:
    def __init__(self, name: str, prefix: int):
        """name:名称 prefix 前缀"""
        self.name = name
        self.prefix = prefix

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name

    def __call__(self):
        return self.name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return self.name == other

    def __ne__(self, other):
        return self.name != other


class SUBJECT_TYPE:
    SPOT = PrefixObject("SPOT", 10)
    SWAP_USD = PrefixObject("SWAP_USD", 20)
    SWAP_USDT = PrefixObject("SWAP_USDT", 30)
    FUTURE_USD = PrefixObject("FUTURE_USD", 40)
    FUTURE_USDT = PrefixObject("FUTURE_USDT", 50)
    FUTURE_USDC = PrefixObject("FUTURE_USDC", 51)  # 系统暂未支持，仅做类型匹配
    OPTION = PrefixObject("OPTION", 60)  # OPTION_USD
    OPTION_USDC = PrefixObject("OPTION_USDC", 61)
    OPTION_USDT = PrefixObject("OPTION_USDT", 62)
    SWAP_USDC = PrefixObject("SWAP_USDC", 70)
    MARGIN = PrefixObject("MARGIN", 80)  # 币币杠杆

    INSTRUMENT_SPOT = PrefixObject("INSTRUMENT_SPOT", 90)
    INSTRUMENT_SWAP_USD = PrefixObject("INSTRUMENT_SWAP_USD", 91)
    INSTRUMENT_SWAP_USDT = PrefixObject("INSTRUMENT_SWAP_USDT", 92)
    INSTRUMENT_FUTURE_USD = PrefixObject("INSTRUMENT_FUTURE_USD", 93)
    INSTRUMENT_FUTURE_USDT = PrefixObject("INSTRUMENT_FUTURE_USDT", 94)
    INSTRUMENT_OPTION = PrefixObject("INSTRUMENT_OPTION", 95)
    INSTRUMENT_SWAP_USDC = PrefixObject("INSTRUMENT_OPTION", 96)

    @classmethod
    def subjects_with_ttl(cls) -> List[str]:
        return [cls.OPTION.name, cls.FUTURE_USD.name, cls.FUTURE_USDT.name]

    @classmethod
    def str_to_cls_map(cls):
        return {value.name: value for value in cls.__dict__.values() if isinstance(value, PrefixObject)}

    @classmethod
    def from_str(cls, name):
        return cls.str_to_cls_map().get(name)

    @classmethod
    def option(cls) -> List[str]:
        return [cls.OPTION.name, cls.OPTION_USDC.name, cls.OPTION_USDT.name]

    @classmethod
    def swap(cls) -> List[str]:
        return [cls.SWAP_USD.name, cls.SWAP_USDC.name, cls.SWAP_USDT.name]

    @classmethod
    def future(cls) -> List[str]:
        return [cls.FUTURE_USD.name, cls.FUTURE_USDT.name]

    @classmethod
    def inverses(cls) -> List[str]:
        """币本位合约"""
        return [cls.FUTURE_USD.name, cls.SWAP_USD.name, cls.OPTION.name]

    @classmethod
    def expiration_date(cls) -> List[str]:
        """有到期日的subject"""
        return [cls.OPTION.name, cls.OPTION_USDC.name, cls.FUTURE_USDT.name, cls.FUTURE_USD.name]


class EXCHANGE:
    DERIBIT = PrefixObject("DERIBIT", 1)
    BITCOM = PrefixObject("BITCOM", 2)
    OKEX = PrefixObject("OKEX", 3)
    HUOBI = PrefixObject("HUOBI", 4)
    BINANCE = PrefixObject("BINANCE", 5)
    BITGET = PrefixObject("BITGET", 6)
    BITWELLEX = PrefixObject("BITWELLEX", 7)
    XDERI = PrefixObject("XDERI", 8)
    LOCAL = PrefixObject("LOCAL", 9)  # 兼容处理本地mysql数据
    BYBIT = PrefixObject("BYBIT", 0)
    KUCOIN = PrefixObject("KUCOIN", 10)

    @classmethod
    def all(cls) -> List[str]:
        return [value.name for value in cls.__dict__.values() if isinstance(value, PrefixObject)]

    @classmethod
    def all_available(cls) -> List[str]:
        return sorted(set(cls.all()) - {"HUOBI", "BITGET", "BITWELLEX", "XDERI", "LOCAL"})


class SIDE:
    BUY = 1
    SELL = -1


SIDE_ITEMS = [
    [SIDE.BUY, "BUY"],
    [SIDE.SELL, "SELL"],
]


class STATE:
    INIT = 100
    POSTED = 101  # 已提交
    PARTIAL_FILLED = 102  # 部分成交
    FILLED = 103  # 成交
    CANCELLED = 104  # 已取消
    PARTIAL_CANCELLED = 105  # 部分取消
    PENDING_CANCELL = 106  # 取消中
    FAILED = -1  # 失败
    EXCEPTION = -2  # 异常
    LEGOUT = -3

    @classmethod
    def is_open_order(cls, order_state):
        """是否符合挂单定义"""
        if order_state in (cls.POSTED, cls.PARTIAL_FILLED):
            return True
        return False


STATE_ITEMS = [
    (STATE.INIT, "初始化"),
    (STATE.POSTED, "已下单"),
    (STATE.FILLED, "已成交"),
    (STATE.PARTIAL_FILLED, "已部分成交"),
    (STATE.CANCELLED, "已取消"),
    (STATE.PARTIAL_CANCELLED, "已部分取消"),
    (STATE.PENDING_CANCELL, "取消中"),
    (STATE.FAILED, "下单失败"),
    (STATE.EXCEPTION, "下单异常"),
]


class TimeInForce:
    IOC = "IOC"
    GTC = "GTC"
    FOK = "FOK"


class OrderType:
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    SUPER_MARKET = "SUPER_MARKET"
    TWAP = "TWAP"
    VWAP = "VWAP"
    TWAP_ALGO = "TWAP_ALGO"


class ASSETS_TYPE:
    OPTION = "OPTION"
    FUTURE = "FUTURE"


class Accuracy:
    def __init__(self, name: str, size: int, price: int, size_multiple: int = None, size_min: float = None):
        """
        instrument accuracy

        :param str name: instrument name defined by ee.
        :param int size: size accuracy. eg: size=2,  round(1.12345) == 1.12
        :param int price: price accuracy. eg: price=2, round(3.1415) == 3.14
        :param int size_multiple: must be integer multiple of [size_multiple]，注：填写size_multiple参数后，需要符合倍数关系才能下单成功
        :param float size_min: must be greater than [size_min]
        """
        self.name = name
        self.size = size
        self.price = price
        self.size_multiple = size_multiple
        self.size_min = size_min

        items = name.split("-")
        self.base = items[0]
        self.quote = items[1]

    @staticmethod
    def merge_to_min(accuracies: List):
        """
        合并多个交易所的精度数据到最粗(最小)精度: [Accuracy("BTC-USDT", 5, 2), Accuracy("BTC-USDT", 4, 1)] -> Accuracy("BTC-USDT", 4, 1)
        @param accuracies: List[Accuracy]
        @return: Accuracy
        """
        uni_name = set(a.name for a in accuracies)
        if len(uni_name) != 1:
            raise Exception("Cannot merge different name")

        name = accuracies[0].name
        size = min(a.size for a in accuracies)
        # print(accuracies[0].name, [a.price for a in accuracies])
        price = min(a.price for a in accuracies)
        size_multiple = (
            min(sequence) if (sequence := [m for a in accuracies if (m := a.size_multiple) is not None]) else None
        )
        size_min = min(sequence) if (sequence := [m for a in accuracies if (m := a.size_min) is not None]) else None

        return Accuracy(name, size, price, size_multiple, size_min)

    @staticmethod
    def merge_accuracy_list(accuracy_list: List) -> List:
        """
        合并accuracy_list列表中的同名Accuracy： [Accuracy("BTC-USDT"), Accuracy("BTC-USDT"), Accuracy("ETH-USDT")]  ->
        [Accuracy("BTC-USDT"), Accuracy("ETH-USDT")]
        @param accuracy_list:  List[Accuracy]
        @return:  List[Accuracy]
        """
        accuracy_list.sort(key=lambda a: a.name)
        all_accuracy_list = []
        for _, grouper in itertools.groupby(accuracy_list, key=lambda a: a.name):
            all_accuracy_list.append(Accuracy.merge_to_min(list(grouper)))
        return all_accuracy_list

    def to_dic(self) -> dict:
        return {
            f"{self.name}": {
                "size": {
                    "accuracy": self.size,
                    "multiple": self.size_multiple,
                    "min": self.size_min,
                },
                "price": {
                    "accuracy": self.price,
                },
                # "size_multiple": self.size_multiple,
                # "size_min": self.size_min
            }
        }


class CURRENCY:
    """
    Reference:
    Bybit: https://api.bybit.com/spot/v1/symbols
    """

    BTC = "BTC"  # 1
    ETH = "ETH"  # 2
    USDT = "USDT"  # 3
    USDC = "USDC"  # 4
    BNB = "BNB"  # 5

    XRP = "XRP"  # 6
    ADA = "ADA"  # 7
    SOL = "SOL"  # 9
    DOGE = "DOGE"  # 10
    DOT = "DOT"  # 11
    AVAX = "AVAX"  # 12
    MATIC = "MATIC"  # 17
    LTC = "LTC"  # 20
    BCH = "BCH"  # 23
    LINK = "LINK"  # 25
    ALGO = "ALGO"  # 27
    ATOM = "ATOM"  # 28
    ETC = "ETC"  # 30
    SAND = "SAND"  # 40
    EOS = "EOS"  # 44
    CAKE = "CAKE"  # 47
    LUNA = "LUNA"  # Soon to be removed
    KCS = "KCS"

    ALT_CURRENCY = "ALT_CURRENCY"  # 未识别的其他币种统称

    SPOT_ACCURACY = [
        # instrument_name, size_accuracy, price_accuracy
        Accuracy("BTC-USDT", 4, 1),  # Bybit(size=6, price=2) kucoin(size=8, price=1)
        Accuracy("ETH-USDT", 3, 2),  # OKX(size=6, price=2)  Binance(size=4, price=2) Bybit(size=5, price=2)
        Accuracy("USDC-USDT", 0, 4),  # Bybit(size=2, price=4)
        Accuracy("BNB-USDT", 3, 1),  # Bybit(size=3, price=4)
        Accuracy("MATIC-USDT", 1, 3),  # OKX(size=6, price=4)  Binance(size=1, price=3) Bybit(size=2, price=4)
        Accuracy("CAKE-USDT", 2, 2),  # OKX not support  Binance(size=2, price=3) Bybit(size=3, price=4)
        Accuracy("AVAX-USDT", 2, 2),  # OKX(size=6, price=2)  Binance(size=2, price=2) Bybit(size=3, price=4)
        Accuracy("LUNA-USDT", 2, 2),  # OKX(size=6, price=3)  Binance(size=2, price=2) Bybit(size=3, price=8)
        Accuracy("LTC-USDT", 3, 1),  # OKX(size=6, price=2)  Binance(size=3, price=1) Bybit(size=5, price=2)
        Accuracy("BCH-USDT", 3, 1),  # OKX(size=6, price=2)  Binance(size=3, price=1) Bybit(size=3, price=1)
        Accuracy("ETC-USDT", 2, 2),  # OKX(size=6, price=3)  Binance(size=2, price=2) Bybit not support
        Accuracy("EOS-USDT", 1, 3),  # OKX(size=6, price=4)  Binance(size=1, price=3) Bybit(size=2, price=4)
        Accuracy("XRP-USDT", 0, 4),  # OKX(size=6, price=5)  Binance(size=0, price=4) Bybit(size=2, price=4)
        Accuracy("SOL-USDT", 2, 2),  # OKX(size=6, price=3)  Binance(size=2, price=2) Bybit(size=3, price=2)
        Accuracy("ADA-USDT", 1, 3),  # OKX(size=4, price=4)  Binance(size=1, price=4) Bybit(size=2, price=3)
        Accuracy("DOGE-USDT", 0, 4),  # OKX(size=6, price=6)  Binance(size=0, price=4) Bybit(size=1, price=5)
        Accuracy("DOT-USDT", 2, 2),  # OKX(size=6, price=3)  Binance(size=2, price=2) Bybit(size=3, price=3)
        Accuracy("LINK-USDT", 2, 2),  # OKX(size=6, price=3)  Binance(size=2, price=2) Bybit(size=3, price=2)
        Accuracy("ALGO-USDT", 0, 4),  # OKX(size=6, price=4)  Binance(size=0, price=4) Bybit(size=2, price=5)
        Accuracy("ATOM-USDT", 2, 2),  # OKX(size=6, price=3)  Binance(size=2, price=2) Bybit(size=3, price=4)
        Accuracy("SAND-USDT", 1, 4),  # OKX(size=6, price=4) Binance(size=1, price=4) Bybit(size=2, price=5)
        Accuracy("KCS-USDT", 4, 3),  # just kucoin
        # instrument_name, size_accuracy, price_accuracy
        Accuracy("ETH-BTC", 3, 5),  # Bybit(size=3, price=6)
        Accuracy("MATIC-BTC", 1, 8),  # Binance(size=1, price=8) ;  OKX not support Bybit(size=1, price=8)
        Accuracy("CAKE-BTC", 2, 7),  # Binance(size=2, price=7) ; OKX not support Bybit not support
        Accuracy("AVAX-BTC", 2, 7),  # Binance(size=2, price=7) ; OKX(size=4, price=7) Bybit not support
        Accuracy("LUNA-BTC", 2, 7),  # Binance(size=2, price=7) ; OKX(size=4, price=7) Bybit not support
        Accuracy("LTC-BTC", 2, 6),  # Binance(size=3, price=6) ; OKX(size=6, price=6) Bybit(size=2, price=6)
        Accuracy("ADA-BTC", 1, 8),  # OKX(size=3, price=8) Binance(size=1, price=8) Bybit not support
        Accuracy("SOL-BTC", 2, 6),  # OKX(size=4, price=7)  Binance(size=2, price=7) Bybit(size=2, price=6)
        Accuracy("DOT-BTC", 2, 7),  # OKX(size=4, price=7)  Binance(size=2, price=7) Bybit(size=2, price=8)
        Accuracy("ATOM-BTC", 2, 7),  # OKX(size=4, price=8)  Binance(size=2, price=7) Bybit not support
        Accuracy("LINK-BTC", 2, 6),  # OKX(size=3, price=6)  Binance(size=2, price=7) Bybit not support
        Accuracy("ALGO-BTC", 0, 7),  # OKX(size=4, price=7)  Binance(size=0, price=8) Bybit(size=1, price=8)
        Accuracy("DOGE-BTC", 0, 8),  # OKX(size=3, price=8)  Binance(size=0, price=8) Bybit not support
        Accuracy("BNB-BTC", 3, 6),  # OKX not support  Binance(size=3, price=6) Bybit not support
        Accuracy("BCH-BTC", 3, 5),  # OKX(size=4, price=5)  Binance(size=3, price=5) Bybit not support
        Accuracy("ETC-BTC", 2, 6),  # OKX(size=5, price=7)  Binance(size=2, price=6) Bybit not support
        Accuracy("EOS-BTC", 1, 7),  # OKX(size=4, price=7)  Binance(size=1, price=7) Bybit not support
        Accuracy("XRP-BTC", 0, 8),  # OKX(size=3, price=8)  Binance(size=0, price=8) Bybit(size=1, price=8)
        Accuracy("SAND-BTC", 1, 8),  # OKX not support Binance(size=1, price=8) Bybit(size=1, price=8)
        # instrument_name, size_accuracy, price_accuracy
        Accuracy("BTC-USDC", 5, 1),  # OKX(size=8, price=1) Binance(size=5, price=2)  # Bybit(size=6, price=2)
        Accuracy("ETH-USDC", 4, 2),  # OKX(size=6, price=2) Binance(size=4, price=2)  # Bybit(size=5, price=2)
        Accuracy("XRP-USDC", 2, 4),  # Bybit(size=2, price=4)
        Accuracy("SOL-USDC", 3, 2),  # Bybit(size=3, price=2)
        Accuracy("DOT-USDC", 3, 3),  # Bybit(size=3, price=3)
        Accuracy("MATIC-USDC", 2, 4),  # Bybit(size=2, price=4)
        Accuracy("LTC-USDC", 4, 2),  # Bybit(size=5, price=2) Kucoin(size=4, price=2)
        Accuracy("SAND-USDC", 2, 5),  # Bybit(size=2, price=5)
        Accuracy("LUNA-USDC", 3, 8),  # Bybit(size=3, price=8)
    ]
    SWAP_USDT_ACCURACY = [
        Accuracy("BTC-USDT", 3, 1),  # Bybit(size=3, price=1), OKX(size=3, price=1)
        Accuracy("ETH-USDT", 2, 2),  # Bybit(size=2, price=2), OKX(size=2, price=2)
        Accuracy("BNB-USDT", 2, 2),
        # OKX not support  Binance(size=2, price=2) Bybit(size=2, price=2)
        Accuracy("MATIC-USDT", 0, 4, size_multiple=10),
        # OKX(size=0, price=4) size需为10的整数倍  Binance(size=0, price=4) Bybit(size=0, price=4)
        # Accuracy("CAKE-USDT", 0, 4), # OKX not support   Binance not support   Bybit not support
        Accuracy("AVAX-USDT", 0, 2, size_multiple=1),
        # OKX(size=0, price=3) size需为1的整数倍  Binance(size=0, price=2) Bybit(size=1, price=3)
        Accuracy("LUNA-USDT", 0, 3, size_multiple=1),
        # OKX(size=1, price=3) size需为1的整数倍  Binance(size=0, price=3)  Bybit not support
        Accuracy("LTC-USDT", 0, 2, size_multiple=1),
        # OKX(size=0, price=2) size需为1的整数倍  Binance(size=3, price=2) Bybit(size=1, price=2)
        Accuracy("BCH-USDT", 1, 2),  # OKX(size=1, price=2)  Binance(size=3, price=2) Bybit(size=2, price=2)
        Accuracy("ETC-USDT", 0, 3, size_multiple=10),
        # OKX(size=0, price=3) size需为10的整数倍  Binance(size=2, price=3) Bybit(size=1, price=3)
        Accuracy("EOS-USDT", 0, 3, size_multiple=10),
        # OKX(size=0, price=4) size需为10的整数倍  Binance(size=1, price=3) Bybit(size=1, price=3)
        Accuracy("SOL-USDT", 0, 2, size_multiple=1),
        # OKX(size=0, price=3) size需为1的整数倍  Binance(size=0, price=2) Bybit(size=1, price=3)
        Accuracy("DOT-USDT", 0, 3, size_multiple=1),
        # OKX(size=0, price=3) size需为1的整数倍  Binance(size=1, price=3) Bybit(size=1, price=3)
        Accuracy("XRP-USDT", 0, 4, size_multiple=100),
        # OKX(size=0, price=5) size需为100的整数倍  Binance(size=0, price=4) Bybit(size=0, price=4)
        Accuracy("ADA-USDT", 0, 4, size_multiple=100),
        # OKX(size=0, price=5) size需为100的整数倍  Binance(size=0, price=4) Bybit(size=0, price=4)
        Accuracy("DOGE-USDT", 0, 5, size_multiple=1000),
        # OKX(size=0, price=6) size需为1000的整数倍  Binance(size=0, price=5) Bybit(size=0, price=4)
        Accuracy("ATOM-USDT", 0, 3, size_multiple=1),
        # OKX(size=0, price=3) size需为1的整数倍   Binance(size=2, price=3) Bybit(size=1, price=3)
        Accuracy("LINK-USDT", 0, 3, size_multiple=1),
        # OKX(size=0, price=3) size需为1的整数倍   Binance(size=2, price=3) Bybit(size=1, price=3)
        Accuracy("ALGO-USDT", 0, 4, size_multiple=10),
        # OKX(size=0, price=4) size需为10的整数倍  Binance(size=0, price=4) Bybit(size=1, price=4)
        Accuracy("SAND-USDT", 0, 4, size_multiple=10),
        # OKX(size=0, price=4) size需为10的整数倍 Binance(size=0, price=4) Bybit(size=0, price=4)
    ]

    SWAP_USDC_ACCURACY = [
        Accuracy("BTC-USDT", 3, 1),
    ]
    FUTURE_USDT_ACCURACY = [
        Accuracy("BTC-USDT", 3, 1),
        Accuracy("ETH-USDT", 2, 2),
    ]

    # FUTURE_USD/SWAP_USD round size
    FUTURE_USD_ROUND_SIZE_MAP = {
        BTC: 1,
        ETH: 2,
    }

    __all_instrument_currency = None
    __spot_accuracy_map = None
    __swap_usdt_accuracy_map = None
    __swap_usdc_accuracy_map = None
    __future_usdt_accuracy_map = None
    __spot_quote = []

    @classmethod
    def all_instrument_currency(cls) -> List[str]:
        """获取类属性的所有币种"""
        if not cls.__all_instrument_currency:
            cls.__all_instrument_currency = [
                key
                for key in cls.__dict__
                if not key.startswith("__") and not callable(getattr(cls, key)) and isinstance(getattr(cls, key), str)
            ]
        return cls.__all_instrument_currency

    @classmethod
    def spot_accuracy_map(cls) -> dict:
        """现货精度map"""
        if not cls.__spot_accuracy_map:
            from basis_alpha.instrument_info import OKXInfoLoader

            okx_spot_accuracy = OKXInfoLoader().spot_accuracy_map
            all_accuracy_list = Accuracy.merge_accuracy_list(cls.SPOT_ACCURACY + okx_spot_accuracy)
            cls.__spot_accuracy_map = {
                key: value for accuracy in all_accuracy_list for key, value in accuracy.to_dic().items()
            }
        return cls.__spot_accuracy_map

    @classmethod
    def swap_usdt_accuracy_map(cls) -> dict:
        """U本位永续精度map"""
        if not cls.__swap_usdt_accuracy_map:
            from basis_alpha.instrument_info import OKXInfoLoader

            okx_swap_usdt_accuracy = OKXInfoLoader().swap_usdt_accuracy_map
            all_accuracy_list = Accuracy.merge_accuracy_list(cls.SWAP_USDT_ACCURACY + okx_swap_usdt_accuracy)
            cls.__swap_usdt_accuracy_map = {
                key: value for accuracy in all_accuracy_list for key, value in accuracy.to_dic().items()
            }
        return cls.__swap_usdt_accuracy_map

    @classmethod
    def swap_usdc_accuracy_map(cls) -> dict:
        """USDC本位永续精度map"""
        if not cls.__swap_usdc_accuracy_map:
            cls.__swap_usdc_accuracy_map = {
                key: value for accuracy in cls.SWAP_USDC_ACCURACY for key, value in accuracy.to_dic().items()
            }
        return cls.__swap_usdc_accuracy_map

    @classmethod
    def future_usdt_accuracy_map(cls) -> dict:
        """U本位交割精度map"""
        if not cls.__future_usdt_accuracy_map:
            from basis_alpha.instrument_info import OKXInfoLoader

            okx_future_usdt_accuracy = OKXInfoLoader().future_usdt_accuracy_map
            all_accuracy_list = Accuracy.merge_accuracy_list(cls.FUTURE_USDT_ACCURACY + okx_future_usdt_accuracy)
            cls.__future_usdt_accuracy_map = {
                key: value for accuracy in all_accuracy_list for key, value in accuracy.to_dic().items()
            }
        return cls.__future_usdt_accuracy_map

    @classmethod
    def is_supported_spot(cls, base: str, quote: str) -> bool:
        return f"{base}-{quote}".upper() in cls.spot_accuracy_map()

    @classmethod
    def spot_quote(cls) -> List[str]:
        if not cls.__spot_quote:
            quote_set = set()
            for a in cls.SPOT_ACCURACY:
                quote_set.add(a.name.split("-")[1])
            cls.__spot_quote = list(quote_set)
        return cls.__spot_quote

    @classmethod
    def spot_quote_exp(cls) -> str:
        return "|".join(cls.spot_quote())


ORDER_FINISHED_STATES = [
    STATE.FAILED,
    STATE.FILLED,
    STATE.EXCEPTION,
    STATE.CANCELLED,
    STATE.PARTIAL_CANCELLED,
    STATE.LEGOUT,
]

ORDER_HAS_FILLED_STATES = [STATE.FILLED, STATE.PARTIAL_FILLED, STATE.PARTIAL_CANCELLED]


# 用于指定 get_open_orders 的 currency 类型, base currency or quote currency
class CURRENCY_TYPE:
    BASE = "BASE"
    QUOTE = "QUOTE"
    BOTH = "BOTH"


FEE_ASSET_UNKNOWN = "UNKNOWN"


class ORDERING_CHANNEL:
    """下单渠道
    0: 内部下单
    1: 交易所物理下单
    """

    INNER = 0
    OUTER = 1
