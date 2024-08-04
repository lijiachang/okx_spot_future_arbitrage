import re
from datetime import datetime, timezone
from functools import lru_cache

YEAR_PREFIX = str(datetime.now().year)[:2]

# 识别多种格式的 instrument_id BTC-31JUL20, BTC-USD-31JUL20, BTC-USD-31JUL20-11700-C
# 识别多种格式的 instrument_id BTC-200731, BTC-USD-200731, BTC-USD-200731-11700-C
INSTRUMENT_RE_LIST = [
    re.compile('^(?P<currency>[^-]{3,4})(-(?P<quote>[^-]{3,4}))?-(?P<day>\\d{1,2})(?P<month>[A-Za-z]{3})(?P<year>\\d{1,2})(-(?P<price>\\d+)-(?P<call_or_put>[CP]))?'),  # noqa
    re.compile('^(?P<currency>[^-]{3,4})(-(?P<quote>[^-]{3,4}))?-(?P<year>\\d{2})(?P<month>\\d{2})(?P<day>\\d{2})(-(?P<price>\\d+)-(?P<call_or_put>[CP]))?$'),  # noqa
]

MONTH_MAPPING = {
    'JAN': 1,
    'FEB': 2,
    'MAR': 3,
    'APR': 4,
    'MAY': 5,
    'JUN': 6,
    'JUL': 7,
    'AUG': 8,
    'SEP': 9,
    'OCT': 10,
    'NOV': 11,
    'DEC': 12,
}

REVERSE_MONTH_MAPPING = {
    v: k for k, v in MONTH_MAPPING.items()
}


def parse_instrument_name(instrument_name):
    for instrument_re in INSTRUMENT_RE_LIST:
        result = instrument_re.search(instrument_name)
        if result:
            return result


@lru_cache(maxsize=40960)
def get_expired_from_instrument_name(instrument_name):
    result = parse_instrument_name(instrument_name)
    if not result:
        return None
    result = result.groupdict()
    if 'day' in result:
        day = result['day']
        month = result['month'].upper()
        year = result['year']
        year = YEAR_PREFIX + str(year)
        date = datetime(int(year), int(MONTH_MAPPING.get(month, month)), int(day))
        date = date.replace(hour=16)
        return date
    else:
        return None


def time_str_to_timestamp_13(time_str: str) -> int:
    """
    '2022-10-08 07:28:05.395195+00:00' -> 1665214085395
    """
    if time_str:
        date = datetime.fromisoformat(time_str)
        return int(round(date.timestamp() * 1000))
    return 0
