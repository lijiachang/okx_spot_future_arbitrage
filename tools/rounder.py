import re

from functools import partial

from basis_alpha.config import SUBJECT_TYPE, EXCHANGE, CURRENCY


def roundup(number, accuracy=0, round_func=round):
    _res = round_func(number, accuracy)
    if _res == number:
        return number
    return round_func(number + 1 / pow(10, accuracy), accuracy)


def roundint(how_to_round, number):
    return int(number)


class Rounder:
    """
    公共的Rounder，ROUND_INFO_MAP 中配置支持的subject以及subject下对应的交易对的信息
    目前支持size和price 的rounding
    rounder = Rounder(subject=SUBJECT_TYPE.SPOT.name, symbol_or_instrument='BTC-USDT')
    rounded_size = rounder.round_size(1.23456)
    rounded_price = rounder.round_price(41234.523456)
    """

    def __init__(self, subject=SUBJECT_TYPE.SPOT.name, symbol_or_instrument='BTC-USDT', exchange=""):
        """
        @param subject:
        @param symbol_or_instrument:  交易对的前缀，比如 'BTC-USDT-11NOV22' -> 'BTC-USDT'
        @param exchange:
        """
        self.ROUND_INFO_MAP = {
            SUBJECT_TYPE.SPOT.name: CURRENCY.spot_accuracy_map(),
            SUBJECT_TYPE.SWAP_USDT.name: CURRENCY.swap_usdt_accuracy_map(),
            SUBJECT_TYPE.SWAP_USDC.name: CURRENCY.swap_usdc_accuracy_map(),
            SUBJECT_TYPE.FUTURE_USDT.name: CURRENCY.future_usdt_accuracy_map()
        }
        self._round_info = self.ROUND_INFO_MAP[subject][symbol_or_instrument]
        self._exchange = exchange

    @property
    def size_step(self):
        """ return 0.0001 """
        return round(0.1 ** self._round_info['size']['accuracy'], 6)

    def _round(self, how_to_round, name, value):
        if self._exchange:
            exchagne_rounder = self._round_info[name].get(self._exchange)
            if exchagne_rounder:
                return exchagne_rounder(how_to_round, value)

        _accuracy = self._round_info[name]['accuracy']
        if _accuracy <= 0:
            _type = int
        else:
            _type = float
        _round_func = self._round_info[name].get('round_func', round)
        if how_to_round == 'round':
            min_number = 1 / (10 ** _accuracy)  # 当前精度下的最小值
            if value < min_number:
                return 0
            return _type(_round_func(value, _accuracy))
        if how_to_round == 'roundup':
            return _type(roundup(value, _accuracy, _round_func))
        raise AttributeError()

    def check_size_multiple(self, size):
        """
        检查size是size_multiple的倍数
        检查通过：return None
        检查失败：return size_multiple
        """
        size_multiple = self._round_info['size'].get('size_multiple')

        if size_multiple:
            if size < size_multiple:
                return size_multiple
            return None if size % size_multiple == 0 else size_multiple
        else:
            return None

    def check_size_min(self, size):
        """检查size最小合法数量。
        检查通过：return None
        检查失败：return size_min"""
        size_min = self._round_info['size'].get('size_min')

        if size_min:
            return size_min if size < size_min else None
        else:
            return None

    def __getattr__(self, method_name):
        match_result = re.match(r'^(\w+)_(\w+)', method_name)
        if match_result:
            how_to_round = match_result.group(1)
            name = match_result.group(2)
            return partial(self._round, how_to_round, name)
        raise AttributeError()


if __name__ == "__main__":
    rounder = Rounder(symbol_or_instrument='BTC-USDT')
    print('Test rounding')
    print(12.34567, ' -> ', rounder.round_size(12.34567))
    print(41234.523456, ' -> ', rounder.round_price(41234.523456))

    print('Test rounding up')
    print(12.34567, ' -> ', rounder.roundup_size(12.34567))
    print(41234.523456, ' -> ', rounder.roundup_price(41234.523456))
