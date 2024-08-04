import asyncio
import logging

from django.core.management.base import BaseCommand

from strategy.spot_future_arbitrage.okx_strategy import SpotFutureArbitrage

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("-s", "--strategy_name", help="策略配置名称", required=True)
        parser.add_argument("-a", "--account_name", help="API账户名称", required=True)

    def handle(self, *args, **options):
        strategy_name = options.get("strategy_name")
        account_name = options.get("account_name")

        arb = SpotFutureArbitrage(strategy_name, account_name)
        asyncio.run(arb.start())
