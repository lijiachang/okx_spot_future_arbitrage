import asyncio
import logging

import uvloop
from django.core.management.base import BaseCommand

from data_source.spiders import OkexWSClient
from data_source.spiders.okex_spider import OkexFutureHTTPClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    consumer_mapping = {
        "okex": OkexWSClient,
    }

    @classmethod
    def get_client(cls, exchange, kind, data_type):
        if exchange == "okex":
            return OkexWSClient
        consumer_name = f"{exchange}__{kind}" if kind else exchange
        consumer_name = f"{consumer_name}__{data_type}" if data_type else consumer_name
        return cls.consumer_mapping[consumer_name]

    def add_arguments(self, parser):
        parser.add_argument("-s", "--symbols", nargs="+", help="support symbols", default=None)

    def handle(self, *args, **options):
        uvloop.install()

        symbols = options.get("symbols")
        if symbols is None:
            symbols = OkexFutureHTTPClient().get_inverse_symbols()

        kind = "spot,future_usd"

        loop = asyncio.get_event_loop()
        loop.run_until_complete(OkexWSClient.run(symbols, kind=kind))
