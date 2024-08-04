import logging
from typing import Dict, List

from django.utils.dateparse import parse_datetime

from basis_alpha import config
from common.common import OrderInfo

logger = logging.getLogger(__name__)


class LocalFormatter:
    def _handle_ts(self, ts):
        try:
            if isinstance(ts, str):
                return int(parse_datetime(ts).timestamp() * 1000)
            else:
                return int(ts.timestamp() * 1000)
        except Exception as e:
            logger.error(e)
            return ""

    def _order(self, data):
        if data.subject in [config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name]:
            amount = round(float(data.size), 8)
            filled_amount = round(float(data.filled_size), 8)
        else:
            amount = round(float(data.size), 8)
            filled_amount = round(float(data.filled_size), 8)
        order = OrderInfo(
            exchange=data.exchange,
            exchange_order_id=data.exchange_order_id if hasattr(data, "exchange_order_id") else "",
            order_id=str(data.order_id),
            direction=config.SIDE.BUY if data.side > 0 else config.SIDE.SELL,
            state=data.state,
            amount=amount,
            filled_amount=filled_amount,
            price=round(float(data.price), 4),
            avg_price=round(float(data.avg_price), 4),
            fee=round(float(data.fee), 8),
            instrument_name=data.instrument_name,
            created_at=self._handle_ts(data.created_at),
            updated_at=self._handle_ts(data.updated_at),
            original_data=data.resp,
        )._asdict()
        if not data.resp:
            original_data = {"order_type": data.order_type}
            order.update({"original_data": original_data})
        order.update({"order_type": data.order_type})
        return order

    def order(self, dataset):
        logger.info(f"format order:{dataset}")
        if isinstance(dataset, list):
            result = []
            for data in dataset:
                result.append(self._order(data))
            return result
        else:
            return self._order(dataset)

    def summary(self, data: Dict):
        return {}

    def position(self, dataset: List):
        return []

    def trade(self, trades):
        return []
