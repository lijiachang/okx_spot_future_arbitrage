import asyncio
import logging
import time
from decimal import Decimal
from functools import partialmethod
from operator import itemgetter
from typing import Optional

import aiohttp
import aioredis
import ujson as json
from aiohttp.web import HTTPException
from django.conf import settings
from django.utils.http import urlencode

from basis_alpha import config
from clients.formatters.factory import FormatMethod, FormatterFactory
from clients.formatters.okex import OkexFormatter
from common.common import AccountMaxSize, ErrorResp, OrderExchangeStatus
from data_source.exchange_info import CurrencyGetter
from tools.account import make_printable_account
from tools.instruments import get_subject_by_v2

# from tools.performance import trace_time_spend
from tools.redis_cache import redis_cache

from .common import InstrumentConverter, Signer, SizeConvertor
from .config import OK_FUTURES, OK_SWAP, SPOT_QUOTE_CURRENCIES, SUBJECT_MAP, OkexCap, get_uly

capability = OkexCap()

SIDE_BUY = "buy"
SIDE_SELL = "sell"

TIMEOUT = 5
logger = logging.getLogger(__name__)


class TIME_IN_FORCE_ITEM:
    GOOD_TIL_CANCELLED = "gtc"
    FILL_OR_KILL = "fok"
    IMMEDIATE_OR_CANCEL = "ioc"


class OkexHttpClient:
    PRIVATE_URL_LIST = [
        "/api/v5/trade/fills",
        "/api/v5/trade/order",
        "/api/v5/trade/orders-history",
        "/api/v5/trade/orders-pending",
        "/api/v5/trade/cancel-order",
        "/api/v5/trade/cancel-batch-orders",
        "/api/v5/account/positions",
        "/api/v5/account/balance",
        "/api/v5/account/greeks",
        "/api/v5/trade/batch-orders",
        "/api/v5/trade/amend-batch-orders",
        "/api/v5/account/bills",
    ]
    # '0': cancel succeed, '51401': order already canceled
    S_CODE_ORDER_CANCELED = (
        "0",
        #  '51401',
        # NOTE: 由于合作方不喜欢我们将重复取消判定为成功的特性,
        # 故此处我们将这个情况的特殊处理去掉。
        # 此改动可能会影响TE里面的一些逻辑，比如LA。
    )

    @classmethod
    def is_private_api(cls, url: str) -> bool:
        if url in cls.PRIVATE_URL_LIST:
            return True
        if url.startswith("/api/v5/trade"):
            return True
        if url.startswith("/api/v5/account"):
            return True
        if url.startswith("/api/v5/asset") and url not in (
            "/api/v5/asset/lending-rate-summary",
            "/api/v5/asset/lending-rate-history",
        ):
            return True
        return False

    def __init__(self, auth, account_id=None, **kwargs):
        super().__init__()
        if not auth:
            auth = settings.OKEX_ACCESS_AUTHKEY
        self.exchange_name = config.EXCHANGE.OKEX.name
        self.client_id, self.client_secret, self.client_passphrase = auth
        self.signer = Signer(*auth)
        self.base_url = self.get_url()
        self.account_id = account_id
        self.redis = None
        self.printable_account_id = make_printable_account(account_id)
        self.formatter: OkexFormatter = FormatterFactory.get(
            self.exchange_name,
            subject=config.SUBJECT_TYPE.OPTION.name,  # okx的formatter不做subject区分
        )

    def get_url(self):
        url = settings.OKEX_REST_URL
        if not url:
            url = "https://www.okx.com"
        return url

    async def execute_api_call(self, url, *args, method="get", **kwargs):
        # TODO: 增加增加耗时打点统计，装饰器
        if method == "post":
            param_map = kwargs.get("json", {})
            if isinstance(param_map, dict):
                param_map = {k: v for k, v in param_map.items() if v is not None}
            if "json" in kwargs:
                kwargs["data"] = json.dumps(kwargs["json"])
                del kwargs["json"]
            else:
                kwargs.setdefault("data", "")
            params = ""
        else:
            param_map = kwargs.pop("params", {})
            param_map = {k: v for k, v in param_map.items() if v is not None}
            params = urlencode(param_map)

        if params:
            full_url = f"{url}?{params}"
        else:
            full_url = url
        headers = kwargs.get("headers", {})
        headers["Content-Type"] = "application/json"
        if settings.TESTNET:
            headers["X-SIMULATED-TRADING"] = "1"
        if type(self).is_private_api(url):
            signature = self.signer.get_signature_for_http(
                kwargs.get("data", ""), method=method.upper(), request_url=full_url
            )
            headers.update(signature)
        kwargs["headers"] = headers
        logger.info(f"<= {self.base_url}{full_url} {args=}, {kwargs=}")
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with getattr(session, method)(
                    f"{self.base_url}{full_url}", *args, timeout=TIMEOUT, **kwargs
                ) as resp:
                    resp_text = await resp.text()
                    logger.info(f"=> {resp_text}")
                    #  resp_json = await resp.json()
                    resp_json = json.loads(resp_text)
                    # 交易失败的情况返回的data为空{}
                    return resp_json
        except HTTPException as e:
            logger.error("execute_api_call %s error:%s", method, e)
            return {"http_error": str(e)}

    post = partialmethod(execute_api_call, method="post")
    get = partialmethod(execute_api_call, method="get")

    async def full_get(self, url, *args, limit=100, page_by="ordId", **kwargs) -> dict:
        """存在分页数据的接口，多次请求接口，获取完整的数据组合
        @param url:
        @param limit: 默认分页大小
        @param page_by: 分页数据依据的字段：如orders-pending为ordId
        @return:
        """
        full_data = []
        while True:
            resp = await self.get(url, *args, **kwargs)
            result_data = resp.get("data", [])
            code = resp.get("code")
            if code != "0":
                return resp
            full_data.extend(result_data)
            # okx对于分页数据：最大为100，默认100条。所有=100条的时候，就可能存在分页
            if len(result_data) >= limit:
                lasted = result_data[-1][page_by]
                kwargs["params"]["after"] = lasted  # 请求此ID之前（更旧的数据）的分页内容
            else:
                break
        result = {"code": "0", "data": full_data}
        return result

    async def _batch_post(self, url: str, param_list: list, count=20):
        """
        批量下单/改单/撤单接口 分派订单组，并行请求
        @param url:
        @param param_list: 参数列表
        @param count: 单次请求最大订单数，最大为20
        """
        params_group = [param_list[i : i + count] for i in range(0, len(param_list), count)]
        resp_list = await asyncio.gather(*[self.post(url, json=params) for params in params_group])
        return resp_list

    async def get_redis(self):
        if self.redis:
            return self.redis
        self.redis = await aioredis.from_url(f"{settings.REDIS_URL}")

    @capability.register
    async def take_order(
        self,
        instrument_name,
        side,
        amount,
        price,
        order_type="limit",
        post_only=False,
        reduce_only=False,
        time_in_force=TIME_IN_FORCE_ITEM.GOOD_TIL_CANCELLED,
        client_order_id=None,
        advanced=None,
    ):
        # NOTE OKEX 下单只返回OrdId，为了兼容上层，我们需要返回完整的Order信息
        sz = SizeConvertor.to_exchange(size=amount, system_instrument=instrument_name)
        instrument_name = InstrumentConverter.to_exchange(instrument_name)

        mode = "cross"
        if time_in_force and time_in_force != "gtc":
            order_type = time_in_force
        if post_only:
            order_type = "post_only"
        params = dict(
            instId=instrument_name,
            tdMode=mode,
            sz=sz,
            side=side,
            px="{:f}".format(Decimal(str(price))),  # 避免科学计数法。临时解决，未来考虑EE数字统一使用Decimal类型,
            ordType=order_type,
            reduce_only=reduce_only,
            clOrdId=client_order_id,
        )
        if instrument_name.endswith(SPOT_QUOTE_CURRENCIES) and order_type == "market":
            # 现货市价单默认使用 usdt 作为数量, 需要设置为 btc
            params["tgtCcy"] = "base_ccy"
        logger.info(f"take order:{params}")
        resp = await self.post("/api/v5/trade/order", json=params)
        data = resp.get("data", None)
        code = resp.get("code")
        if code == "0":
            ordId = data[0]["ordId"]
            return await self.check_order_status(ordId, instrument_name=instrument_name)
        else:
            # 失败返回报错信息code和msg
            msg = data[0]["sMsg"] if data else resp["msg"]
            code = data[0]["sCode"] if data else code
            return False, ErrorResp(code, msg)

    @capability.register
    async def batch_take_order(self, payload_list):
        params_list = []
        params_dict = {}
        for payload in payload_list:
            # 注意，参数类型只应该是str或者bool
            price = payload["price"]
            amount = SizeConvertor.to_exchange(size=payload["amount"], system_instrument=payload["instrument_name"])
            instrument_name = InstrumentConverter.to_exchange(payload["instrument_name"])

            mode = "cross"
            order_type = payload["order_type"]
            time_in_force = payload.get("time_in_force", None)
            post_only = payload.get("post_only", False)
            if time_in_force and time_in_force != "gtc":
                order_type = time_in_force
            if post_only:
                order_type = "post_only"
            params = dict(
                instId=instrument_name,
                sz=amount,
                side=payload["side"],
                px="{:f}".format(Decimal(str(price))),  # 避免科学计数法。临时解决，未来考虑EE数字统一使用Decimal类型,
                ordType=order_type,
                #  time_in_force=payload['time_in_force'],
                #  post_only=payload['post_only'],
                reduceOnly=payload["reduce_only"],
                clOrdId=payload["client_order_id"],
                tdMode=mode,
            )
            if instrument_name.endswith(SPOT_QUOTE_CURRENCIES) and order_type == "market":
                params["tgtCcy"] = "base_ccy"
            params_list.append(params)
            params_dict[params["clOrdId"]] = params

        orders = []
        final_msg = ""
        resp_list = await self._batch_post("/api/v5/trade/batch-orders", params_list)
        for resp in resp_list:
            sub_orders = resp.get("data", [])
            orders.extend(sub_orders)
            final_msg = resp.get("msg")

        result = {}
        if orders:
            # 处理异常的部分，错误的订单只返回错误提示
            retrive_order_status_tasks = []
            for item in orders:
                if item["sCode"] == "0":
                    result[item["clOrdId"]] = item
                    retrive_order_status_tasks.append(
                        self.check_order_status(
                            item["ordId"],
                            instrument_name=params_dict[item["clOrdId"]]["instId"],
                        )
                    )
                else:
                    result[item["clOrdId"]] = ErrorResp(item["sCode"], item["sMsg"])
            orders_status = await asyncio.gather(*retrive_order_status_tasks)
            for succeed, order in orders_status:
                if succeed:
                    result[order["clOrdId"]] = order
            total_success = any([isinstance(v, dict) for v in result.values()])  # 全部成功或者部分成功
            return total_success, result
        return False, final_msg  # 服务端错误： 500

    def gen_user_trade_key(self, order_id):
        """order_id为订单ID，即trade的所属order"""
        return f"{self.exchange_name}:USER_TRADE:{order_id}"

    @capability.register
    async def get_trades(self, order_id, currency=None, **kwargs):
        """先获取缓存数据，如果有则返回"""
        await self.get_redis()
        cache_key = self.gen_user_trade_key(order_id)
        cache_data = await self.redis.smembers(cache_key)
        if cache_data:
            load_data = []
            for item in cache_data:
                load_data.append(json.loads(item))
            return True, load_data

        params = dict(
            ordId=order_id,
        )
        if kwargs.get("instrument_name"):
            params.update({"instId": InstrumentConverter.to_exchange(kwargs.get("instrument_name"))})

        resp = await self.get("/api/v5/trade/fills", params=params)
        result = resp.get("data", None)
        code = resp.get("code")
        if code == "0":
            return True, result
        return False, resp

    @capability.register
    async def check_order_status(self, order_id, client_order_id=None, instrument_name=None, after_than=None):
        params = {}
        if client_order_id:
            params["clOrdId"] = client_order_id
        else:
            params["ordId"] = order_id
        if instrument_name:
            instrument_name = InstrumentConverter.to_exchange(instrument_name)
            params["instId"] = instrument_name
        """
        {
          "code": "0",
          "msg": "",
          "data": [
            {
              "instType": "FUTURES",
              "instId": "BTC-USD-200329",
              "ccy": "",
              "ordId": "312269865356374016",
              "clOrdId": "b1",
              "tag": "",
              "px": "999",
              "sz": "3",
              "pnl": "5",
              "ordType": "limit",
              "side": "buy",
              "posSide": "long",
              "tdMode": "isolated",
              "accFillSz": "0",
              "fillPx": "0",
              "tradeId": "0",
              "fillSz": "0",
              "fillTime": "0",
              "state": "live",
              "avgPx": "0",
              "lever": "20",
              "tpTriggerPx": "",
              "tpOrdPx": "",
              "slTriggerPx": "",
              "slOrdPx": "",
              "feeCcy": "",
              "fee": "",
              "rebateCcy": "",
              "rebate": "",
              "tgtCcy":"",
              "category": "",
              "uTime": "1597026383085",
              "cTime": "1597026383085"
            }
          ]
        }
        """
        resp = await self.get("/api/v5/trade/order", params=params)
        data = resp["data"]
        code = resp["code"]
        if code == "0" and len(data) > 0:
            return True, data[0]
        else:
            # {'code': '51603', 'data': [], 'msg': 'Order does not exist'}
            if isinstance(resp, dict):
                resp["order_exchange_status"] = OrderExchangeStatus(order_id_not_exist=code == "51603")._asdict()
            return False, resp

    @capability.register
    async def get_orders(
        self,
        currency=None,
        subject=None,
        instrument_name=None,
        order_id=None,
        label=None,
        start_time=None,
        end_time=None,
        include_open=None,
        offset=None,
        limit=None,
    ):
        """
        ret:
            {
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "accFillSz": "0",
                        "avgPx": "",
                        "cTime": "1618235248028",
                        "category": "normal",
                        "ccy": "",
                        "clOrdId": "",
                        "fee": "0",
                        "feeCcy": "BTC",
                        "fillPx": "",
                        "fillSz": "0",
                        "fillTime": "",
                        "instId": "BTC-USDT",
                        "instType": "SPOT",
                        "lever": "5.6",
                        "ordId": "301835739059335168",
                        "ordType": "limit",
                        "pnl": "0",
                        "posSide": "net",
                        "px": "59200",
                        "rebate": "0",
                        "rebateCcy": "USDT",
                        "side": "buy",
                        "slOrdPx": "",
                        "slTriggerPx": "",
                        "state": "live",
                        "sz": "1",
                        "tag": "",
                        "tgtCcy": "",
                        "tdMode": "cross",
                        "tpOrdPx": "",
                        "tpTriggerPx": "",
                        "tradeId": "",
                        "uTime": "1618235248028"
                    }
                ]
            }
        """
        resp = await self.get(
            "/api/v5/trade/orders-history",
            params=dict(
                instType=SUBJECT_MAP[subject],
                instId=instrument_name,
            ),
        )
        result = resp.get("data")
        code = resp.get("code")
        if code == "0":
            return True, result
        return False, resp

    @capability.register
    async def get_order_history(self, currency=None, subject=None, start_time=None, end_time=None, **kwargs):
        return await self.get_orders(currency=currency, subject=subject, start_time=start_time, end_time=end_time)

    @capability.register
    async def get_trade_history(self, currency, subject, start_ms, end_ms, ignore_original=True, **kwargs):
        """
        https://www.okx.com/docs-v5/zh/#rest-api-trade-get-transaction-details-last-3-days
        获取交易所的 trade 记录，用于 vpos 每日对账，默认不需要 original_data
        spot 需要通过 instId 查询，其他的用 uly
        返回的订单顺序新订单在前，因此需要手动调转顺序
        由于 okex 区分 future/swap，其他交易所不区分，为了兼容，如果 subject 参数为 future/swap，则需要手动聚合
        """
        logger.info("okex get trade history start")
        method = "/api/v5/trade/fills"
        count = "100"  # 最多每次只能请求 100 条
        result = []
        existed_ids = []
        if subject == config.SUBJECT_TYPE.SPOT.name:
            keys = redis_cache.keys(f"EXECUTE_ENGINE.SPIDER.{self.exchange_name}.{subject}.{currency}.*.BOOK")
            currency_list = []
            for key in keys:
                (
                    _,
                    _,
                    exchange,
                    subject,
                    currency,
                    instrument_name,
                    _,
                ) = key.decode().split(".")
                currency_list.append(instrument_name)
        else:
            uly = get_uly(currency, subject)
            currency_list = [uly]
        if subject in [
            config.SUBJECT_TYPE.SWAP_USD.name,
            config.SUBJECT_TYPE.FUTURE_USD.name,
            config.SUBJECT_TYPE.SWAP_USDT.name,
            config.SUBJECT_TYPE.FUTURE_USDT.name,
        ]:
            subject_list = [OK_FUTURES, OK_SWAP]
        else:
            subject_list = [SUBJECT_MAP[subject]]
        for inst_type in subject_list:
            for curr in currency_list:
                for _ in range(1000):
                    params = dict(
                        instType=inst_type,
                        begin=start_ms,
                        end=end_ms,
                        limit=count,
                    )
                    if subject == config.SUBJECT_TYPE.SPOT.name:
                        params["instId"] = curr
                    else:
                        params["uly"] = curr

                    resp = await self.get(method, params=params)
                    data = resp.get("data", None)
                    code = resp.get("code")
                    if code == "0":
                        if not data:
                            break
                        for item in data:
                            # 传入 dict 进行 format，返回 list of dict，len == 1
                            formatted_d = FormatterFactory.format(
                                self.account_id,
                                self.exchange_name,
                                subject,
                                item,
                                FormatMethod.TRADE,
                            )
                            formatted_d = formatted_d[0]
                            if f"{formatted_d['order_id']}{formatted_d['trade_id']}" in existed_ids:
                                continue
                            else:
                                existed_ids.append(f"{formatted_d['order_id']}{formatted_d['trade_id']}")
                                # 对数据进行特殊处理
                                # fee 与 fee_asset 特殊处理（formatter 使用的字段跟此接口字段不一致）
                                formatted_d["fee"] = -1 * float(formatted_d["original_data"].get("fee", "0.0") or "0.0")
                                formatted_d["fee_asset"] = formatted_d["original_data"].get("feeCcy", "").upper()
                                if ignore_original:
                                    formatted_d.pop("original_data")
                                result.append(formatted_d)
                        if len(data) >= int(count):
                            end_ms = data[-1]["ts"]
                        else:
                            break
                        await asyncio.sleep(0.1)
                    elif code in ["51014", "51001"]:
                        # 如果指数不存在或交易产品 id 不存在，则跳过
                        logger.error(f"okex get trade history with invalid curr: {curr}")
                        break
                    else:
                        return False, resp
        sorted_result = sorted(result, key=itemgetter("created_at"))
        return True, {"trades": sorted_result}

    @capability.register
    async def get_open_orders(
        self,
        currency=None,
        subject=None,
        currency_type=config.CURRENCY_TYPE.BASE,
        **kwargs,
    ):
        """获取挂单
        uly是标的指数，只有合约才有
        币币的话，需要传入instId 也就是产品ID
        """
        inst_type = SUBJECT_MAP[subject]
        resp = await self.full_get("/api/v5/trade/orders-pending", params=dict(instType=inst_type))
        result_data = resp.get("data", [])
        code = resp.get("code")
        logger.debug(f"okex_get_open_orders:{resp}")
        if code != "0":
            return False, resp
        if currency_type == config.CURRENCY_TYPE.BASE:

            def starts_with_currency_usd(x):
                return x.startswith(f"{currency}-USD-")

            def starts_with_currency_usdt(x):
                return x.startswith(f"{currency}-USDT-")

            def starts_with_currency(x):
                return x.startswith(currency)

            if subject in (
                config.SUBJECT_TYPE.SWAP_USD.name,
                config.SUBJECT_TYPE.FUTURE_USD.name,
            ):  # 币本位
                result_data = [item for item in result_data if starts_with_currency_usd(item["instId"])]
            elif subject in (
                config.SUBJECT_TYPE.SWAP_USDT.name,
                config.SUBJECT_TYPE.FUTURE_USDT.name,
            ):  # U本位
                result_data = [item for item in result_data if starts_with_currency_usdt(item["instId"])]
            else:  # 现货和期权
                result_data = [item for item in result_data if starts_with_currency(item["instId"])]

        elif currency_type == config.CURRENCY_TYPE.QUOTE:
            result_data = [item for item in result_data if f"-{currency}" in item["instId"]]
        elif currency_type == config.CURRENCY_TYPE.BOTH:
            result_data = [item for item in result_data if currency in item["instId"]]
        return True, result_data

    @capability.register
    async def cancel_order(self, order_id=None, client_id=None, currency=None, instrument_name=None):
        instrument_name = InstrumentConverter.to_exchange(instrument_name)
        data = dict(instId=instrument_name)
        if order_id:
            data["ordId"] = order_id
        if client_id:
            data["clOrdId"] = client_id
        resp = await self.post("/api/v5/trade/cancel-order", json=data)
        data = resp.get("data")
        code = resp.get("code")
        if code == "0" and len(data) > 0:
            ordId = data[0]["ordId"]
            return await self.check_order_status(ordId, instrument_name=instrument_name)
        else:
            return False, resp

    @capability.register
    async def batch_cancel_order_dict(self, orders=None, **kwargs):
        """
        order_id_dict_list = [{"instrument_name": "BTC-USDT", "order_id": "", "client_id": ""}]
        """
        post_data = []
        for item in orders:
            obj = {"instId": InstrumentConverter.to_exchange(item["instrument_name"])}
            if item.get("exchange_id", None):
                obj["ordId"] = item["exchange_id"]
            elif item.get("order_id", None):
                obj["clOrdId"] = item["order_id"]
            post_data.append(obj)
        resp_list = await self._batch_post("/api/v5/trade/cancel-batch-orders", param_list=post_data)

        has_succeed = False
        total_result = {}
        for resp in resp_list:
            # resp e.g.
            # code=2 部分成功: {"code":"2","data":[
            # {"clOrdId":"","ordId":"492312446556811264","sCode":"51401",
            # "sMsg":"Cancellation failed as the order is already cancelled."},
            # {"clOrdId":"1663658017160716110","ordId":"492352643973623816","sCode":"0","sMsg":""}],
            # "msg":"Bulk operation partially succeeded."}
            # code=1 全部失败:{'code': '1', 'data': [{'clOrdId': '', 'ordId': '434672381708349440',
            # 'sCode': '51401', 'sMsg':
            # 'Cancellation failed as the order is already cancelled.'}], 'msg': 'Operation failed.'}
            # code=0 全部成功：{"code":"0","data":[{"clOrdId":"1649956513","ordId":"434674657910992896",
            # "sCode":"0","sMsg":""}],"msg":""}
            data = resp.get("data")
            code = resp.get("code")
            has_succeed = has_succeed or (code == "0")
            for r in data:
                is_succeed_scode = r["sCode"] in self.S_CODE_ORDER_CANCELED  # 成功取消的状态码
                if is_succeed_scode:
                    has_succeed = True
                total_result[r["ordId"]] = {
                    "succeed": is_succeed_scode,
                    "result": r["sMsg"],
                    "error_code": r["sCode"],
                    "exchange_order_id": r["ordId"],
                }

        return has_succeed, total_result

    async def batch_cancel_order(self, order_id_list=None, currency=None, instrument_name=None, **kwargs):
        """
        @param order_id_list: 这里是交易所的订单ID，因为在om strategy统一转换为了exchange_order_id
        """
        if not instrument_name:
            return False, "instrument_name is needed!"
        orders = [
            dict(
                instrument_name=instrument_name,
                exchange_id=e_id,
            )
            for e_id in order_id_list
        ]
        return await self.batch_cancel_order_dict(orders=orders)

    @capability.register
    async def batch_amend_order(self, orders=None, currency=None):
        """
        orders=[{ "exchange_id": , "price": , "instrument_name": , "side": , "size": , }]
        """
        exchange_orders = []
        logger.info(f"batch_amend_order raw orders: {orders}")
        for order in orders:
            exchange_order = {
                "ordId": order["exchange_id"],
                "instId": InstrumentConverter.to_exchange(order["instrument_name"]),
            }
            if size := order.get("size"):
                exchange_order.update(
                    {"newSz": SizeConvertor.to_exchange(size=float(size), system_instrument=order["instrument_name"])}
                )
            if price := order.get("price"):
                exchange_order.update({"newPx": "{:f}".format(Decimal(str(price)))})
            exchange_orders.append(exchange_order)
        logger.info(f"exchange_orders: {exchange_orders}")

        resp_list = await self._batch_post("/api/v5/trade/amend-batch-orders", param_list=exchange_orders)

        # note: 得到的clOrdId是EE内部的子订单ID，不能作为order_id使用。因为通常认为order_id是父订单ID
        exchange_order_id_list = [order["ordId"] for order in exchange_orders]
        total_result = {}
        for resp in resp_list:
            if resp.get("code") != "0" and not resp.get("data"):
                # 服务类错误：500
                total_result.update(
                    {
                        exchange_order_id: dict(
                            succeed=False,
                            result=resp.get("msg"),
                            error_code=resp.get("code"),
                            exchange_order_id=exchange_order_id,
                        )
                        for exchange_order_id in exchange_order_id_list
                    }
                )
            else:
                for r in resp["data"]:
                    exchange_order_id = r["ordId"]
                    total_result[exchange_order_id] = dict(
                        succeed=r["sCode"] == "0",
                        result=r["sMsg"],
                        error_code=r["sCode"],
                        exchange_order_id=exchange_order_id,
                    )
        total_has_succeed = any(x["succeed"] for x in total_result.values())  # 全部成功或部分成功
        return total_has_succeed, total_result

    @capability.register
    async def cancel_all_order(self, subject, currency, instrument_name=None):
        get_open_orders_succeed, get_open_orders_result = await self.get_open_orders(currency=currency, subject=subject)
        # TODO 处理get失败的情况: {'code': '50004', 'data': [], 'msg': 'Endpoint request timeout. '} ->
        #  (656)TypeError: string indices must be integers
        logger.info(f"get_open_orders: {get_open_orders_succeed}, {get_open_orders_result}")
        orders = [
            dict(
                instrument_name=InstrumentConverter.to_system(item["instId"]),
                exchange_id=item["ordId"],
            )
            for item in get_open_orders_result
        ]
        if instrument_name:
            orders = [order for order in orders if order["instrument_name"] == instrument_name]
        if orders:
            return await self.batch_cancel_order_dict(orders=orders)
        else:
            # no open orders
            return True, {}

    @capability.register
    async def get_account_summary(self, currency):
        """
        需要整合汇集 balance / greeks 2个API 才可以获得内部满意的 account_summary
        {
           "code":"0",
           "data":[
              {
                 "adjEq":"222392.75758924644",
                 "details":[
                    {
                       "availBal":"2.2944852751706297",
                       "availEq":"2.2944852751706297",
                       "cashBal":"2.7907893228813214",
                       "ccy":"BTC",
                       "crossLiab":"0",
                       "disEq":"107914.54784893343",
                       "eq":"2.689539571250316",
                       "eqUsd":"107914.54784893343",
                       "frozenBal":"0.3950542960796865",
                       "interest":"0",
                       "isoEq":"0",
                       "isoLiab":"0",
                       "isoUpl":"0",
                       "liab":"0",
                       "maxLoan":"25.738050486768987",
                       "mgnRatio":"",
                       "notionalLever":"",
                       "ordFrozen":"0",
                       "stgyEq":"0",
                       "twap":"0",
                       "uTime":"1649738159478",
                       "upl":"-0.0082497516310054",
                       "uplLiab":"0.1094995032620108"
                    }
                 ],
                 "imr":"15851.079565042124",
                 "isoEq":"0",
                 "mgnRatio":"18.239173154087442",
                 "mmr":"12193.13812695548",
                 "notionalUsd":"320990.4",
                 "ordFroz":"",
                 "totalEq":"249185.2915676087",
                 "uTime":"1649744634003"
              }
           ],
           "msg":""
        }

        {
           "code":"0",
           "data":[
              {
                 "ccy":"BTC",
                 "deltaBS":"1.8392663005013763",
                 "deltaPA":"-0.8498518403817220",
                 "gammaBS":"-0.0001550207648006",
                 "gammaPA":"-4.5202941953562240",
                 "thetaBS":"293.5830142709390000",
                 "thetaPA":"0.0071027524871177",
                 "ts":"1649744634154",
                 "vegaBS":"-107.0091467147174000",
                 "vegaPA":"-0.0026669847956160"
              }
           ],
           "msg":""
        }
        """
        params = dict(ccy=currency) if currency else {}
        resp_balance, resp_greeks = await asyncio.gather(
            self.get("/api/v5/account/balance", params=params),
            self.get("/api/v5/account/greeks", params=params),
        )
        if resp_balance.get("code") != "0":
            return False, resp_balance
        if resp_greeks.get("code") != "0":
            return False, resp_greeks

        result = {}
        for item in resp_balance.get("data", []):
            details = item.pop("details", [])
            for detail in details:
                result[detail["ccy"]] = item.copy()  # 保留全局信息
                # Note: 舍去针对每个coin 的'mgnRatio' = 0，而保留整个账户的 'mgnRatio'。 背景：默认使用全局 cross margin type
                del detail["mgnRatio"]
                result[detail["ccy"]].update(detail)  # 每个coin信息
        # 补充greeks
        for greek in resp_greeks.get("data", []):
            result.setdefault(greek["ccy"], {}).update(greek)

        return True, result

    @capability.register
    async def get_positions(
        self,
        subject=None,
        instrument_name=None,
        currency=None,
        currency_type=config.CURRENCY_TYPE.BASE,
    ):
        """
        ret:
        {
            "code": "0",
            "msg": "",
            "data": [{
                "adl":"1",
                "availPos":"1",
                "avgPx":"2566.31",
                "cTime":"1619507758793",
                "ccy":"ETH",
                "deltaBS":"",
                "deltaPA":"",
                "gammaBS":"",
                "gammaPA":"",
                "imr":"",
                "instId":"ETH-USD-210430",
                "instType":"FUTURES",
                "interest":"0",
                "last":"2566.22",
                "lever":"10",
                "liab":"",
                "liabCcy":"",
                "liqPx":"2352.8496681818233",
                "margin":"0.0003896645377994",
                "mgnMode":"isolated",
                "mgnRatio":"11.731726509588816",
                "mmr":"0.0000311811092368",
                "notionalUsd":"2276.2546609009605",
                "optVal":"",
                "pTime":"1619507761462",
                "pos":"1",
                "posCcy":"",
                "posId":"307173036051017730",
                "posSide":"long",
                "thetaBS":"",
                "thetaPA":"",
                "tradeId":"109844",
                "uTime":"1619507761462",
                "upl":"-0.0000009932766034",
                "uplRatio":"-0.0025490556801078",
                "vegaBS":"",
                "vegaPA":""
            }]
        }
        """
        params = dict()
        if subject:
            params["instType"] = SUBJECT_MAP[subject]
        if instrument_name:
            params["instId"] = InstrumentConverter.to_exchange(instrument_name)
        resp = await self.get("/api/v5/account/positions", params=params)
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            if currency:
                # currency 默认为 base currency, 也可以设置为 quote currency
                if currency_type == config.CURRENCY_TYPE.BASE:
                    data = list(filter(lambda x: x["instId"].startswith(currency), data))
                else:
                    data = list(filter(lambda x: f"-{currency}" in x["instId"], data))
                if subject:
                    data = list(
                        filter(
                            lambda x: get_subject_by_v2(InstrumentConverter.to_system(x["instId"])) == subject,
                            data,
                        )
                    )

            data = FormatterFactory.format(self.account_id, self.exchange_name, subject, data, FormatMethod.POSITION)

            return True, data
        return False, resp

    @capability.register
    async def get_instruments(self, currency=None, subject=None, instrument_name=None):
        uly = get_uly(currency, subject)
        params = {"uly": uly}
        if subject:
            params["instType"] = SUBJECT_MAP[subject]
        if instrument_name:
            params["instId"] = InstrumentConverter.to_exchange(instrument_name)
        return await self.get("/api/v5/public/instruments", params=params)

    @capability.register
    async def get_delivery_prices(self, currency, latest=False, subject=None):
        """获取交割价，由于 FUTURE_USD, FUTURE_USDT, OPTION 交割价可能不同，因此需要 subject 来确定 uly
        {
          "code": "0",
          "data": [
            {
              "details": [
                {
                  "insId": "BTC-USD-220429",
                  "px": "39521.0773885913106849",
                  "type": "Delivery"
                }
              ],
              "ts": "1651219200000"
            },
            {
              "details": [
                {
                  "insId": "BTC-USD-220422",
                  "px": "40609.6457379648313515",
                  "type": "Delivery"
                }
              ],
              "ts": "1650614400000"
            }
          ],
          "msg": ""
        }
        当 subject 为 OPTION 时，details 里边会有所有同一天的 option，交割价目前看来是一样的，所以只取一条即可,
        当 subject 为 FUTURE 时，details 里边默认只有一条
        由于入参 currency 是由调用方 VPOS 的 config.CURRENCY 指定，有可能存在 okex 没有的 currency
        返回 {"code": "50014","data": [],"msg": "必填参数uly不能为空"}。如果不存在，则跳过。
        返回的结构体为：
        [{"insId": "BTC-USD-220508-37000-C","px": "34733.4898746376243547","type": "exercised","ts": "1651996800000"}]
        """
        # https://www.okx.com/docs-v5/en/#rest-api-public-data-get-delivery-exercise-history
        logger.info("get_delivery_prices start")
        if not subject:
            logger.error("subject is required on okex get_delivery_prices")
            return False, []
        # inst_type 为 OPTION 或 FUTURES
        # uly 通过 subject 来确定 quote currency
        inst_type = SUBJECT_MAP[subject]
        uly = get_uly(currency, subject)
        params = dict(
            instType=inst_type,
            uly=uly,
            limit=100,
        )
        resp = await self.get("/api/v5/public/delivery-exercise-history", params=params)
        code = resp.get("code")
        result = []
        if code == "0":
            for i in resp["data"]:
                d = i["details"][0]
                d["ts"] = i["ts"]
                result.append(d)
        elif code == "50014":
            logger.warning(f"okex get_delivery_prices: uly {uly} not found")
        else:
            return False, []

        if latest and len(result) > 0:
            latest_data = max(result, key=lambda x: int(x["ts"]))
            result = [latest_data]

        formatted_data = FormatterFactory.format(
            self.account_id,
            self.exchange_name,
            config.SUBJECT_TYPE.OPTION.name,
            result,
            FormatMethod.DELIVERY_PRICE,
        )
        return True, formatted_data

    @capability.register
    async def get_settlement_history(self, currency, start_ms, end_ms, type="delivery", instrument_name=None):
        """获取交割历史记录
        https://www.okx.com/docs-v5/zh/#rest-api-account-get-bills-details-last-7-days
        由于 okex 没有时间范围查询，因此需要通过 billID 自己实现
        另外 okex 不仅区分 currency，还区分 subject，为了使接口对外统一，这里内部聚合 OPTION 和 FUTURE 类型的数据
        okex 只支持 type 为 delivery，没有 settlement
        """
        logger.info("okex get_settlement_history start")
        result = []
        method = "/api/v5/account/bills"
        inst_types = ["FUTURES", "OPTION"]
        after = None
        if type not in ["delivery"]:
            logger.warning(
                "okex get_settlement_history type: %s error, only support delivery",
                type,
            )
            return False, []

        for inst_type in inst_types:
            exit_flag = False
            for _ in range(1, 100):
                if exit_flag:
                    break
                params = dict(
                    instType=inst_type,
                    ccy=currency,
                    limit=100,
                    type=3,  # 交割 type 为 3
                )
                if after:
                    params["after"] = after
                resp = await self.get(method, params=params)
                data = resp.get("data", None)
                code = resp.get("code")
                if code == "0":
                    if not data:
                        break
                    for item in data:
                        after = item["billId"]
                        # 过滤超出 end_ms 的数据
                        if int(item["ts"]) > end_ms:
                            continue
                        if int(item["ts"]) < start_ms:
                            exit_flag = True
                            break
                        item["type"] = type
                        result.append(item)
                    await asyncio.sleep(0.1)
                else:
                    return False, result
        formatted_data = FormatterFactory.format(
            self.account_id,
            self.exchange_name,
            config.SUBJECT_TYPE.OPTION.name,
            result,
            FormatMethod.SETTLEMENT,
        )
        return True, formatted_data

    @capability.register
    async def get_funding_bills(self, start_ms):
        """获取资金费账单"""
        end_ms = int(time.time() * 1000)
        logger.info(f"okex get_funding_bills start, {start_ms=}, {end_ms=}")
        result = []
        method = "/api/v5/account/bills"
        limit = 100
        for _ in range(1, 100):
            params = dict(
                limit=limit,
                type=8,  # 资金费 type 为 8
                instType=OK_SWAP,  # 永续合约
                begin=start_ms,
                end=end_ms,
            )
            resp = await self.get(method, params=params)
            data = resp.get("data", None)
            code = resp.get("code")
            if code == "0":
                if not data:
                    break

                result.extend(self.formatter.funding_bill(data))
                await asyncio.sleep(0.1)
            else:
                return False, result
            end_ms = data[-1]["ts"]
            if len(data) < limit:
                break
        # 根据 bill_id 去重
        result = list({i["bill_id"]: i for i in result}.values())
        return True, result

    @capability.register
    async def get_maxsize(
        self,
        instrument_name: str,
        trade_mode: str,
        currency: Optional[str],
        price: Optional[str],
        leverage: Optional[str],
        un_spot_offset=False,
    ):
        """
        获取最大可买卖/开仓数量

        instId	String	是	产品ID，如 BTC-USDT
                            支持多产品ID查询（不超过5个），半角逗号分隔
        tdMode	String	是	交易模式 cross：全仓 isolated：逐仓 cash：非保证金
        ccy	String	可选	保证金币种，仅适用于单币种保证金模式下的全仓杠杆订单
        px	String	否	委托价格
                        当不填委托价时会按当前最新成交价计算
                        当指定多个产品ID查询时，忽略该参数，按当前最新成交价计算
        leverage	String	否	开仓杠杆倍数
                                默认为当前杠杆倍数
                                仅适用于币币杠杆/交割/永续
        unSpotOffset	Boolean	否	true：禁止现货对冲，false：允许现货对冲
                                    默认为false
                                    仅适用于组合保证金模式
                                    开启现货对冲模式下有效，否则忽略此参数。
        """

        method = "/api/v5/account/max-size"
        resp = await self.get(
            method,
            params={
                "instId": InstrumentConverter.to_exchange(instrument_name),
                "tdMode": trade_mode,
                "ccy": currency,
                "px": price,
                "leverage": leverage,
                "unSpotOffset": un_spot_offset,
            },
        )
        if resp.get("code") != "0":
            return False, resp
        result = []
        for data in resp["data"]:
            result.append(
                AccountMaxSize(
                    instrument_name=InstrumentConverter.to_system(data.get("instId", "")),
                    currency=data.get("ccy", ""),
                    max_buy=data.get("maxBuy", ""),
                    max_sell=data.get("maxSell", ""),
                )._asdict()
            )
        return True, result

    @capability.register
    async def get_interest_limits(self, type_: Optional[str], currency: Optional[str]):
        """获取借币利率与限额"""
        method = "/api/v5/account/interest-limits"
        params = {}
        if type_:
            params["type"] = type_
        if currency:
            params["ccy"] = currency
        resp = await self.get(method, params=params)
        if resp.get("code") != "0":
            return False, resp
        return True, self.formatter.interest_limits(resp["data"])

    @capability.register
    async def proxy_call(self, name, params=None):
        if params is None:
            params = {}
        post_api = ["/api/v5/trade/one-click-repay"]
        if name in post_api:
            resp = await self.post(name, json=params)
        else:
            resp = await self.get(name, params=params)
        return True, resp

    @capability.register
    async def get_max_loan(
        self,
        instrument_name: str,
        margin_mode: str,
        margin_currency: Optional[str] = None,
    ):
        """获取交易产品最大可借。
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-account-get-the-maximum-loan-of-instrument
        """
        params = {
            "instId": InstrumentConverter.to_exchange(instrument_name),
            "mgnMode": margin_mode,
        }
        if margin_currency:
            params["mgnCcy"] = margin_currency
        resp = await self.get("/api/v5/account/max-loan", params=params)
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            return True, self.formatter.max_loan(data)
        return False, resp

    @capability.register
    async def get_account_config(self):
        """查看当前账户的配置信息。
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-account-get-account-configuration
        """
        resp = await self.get("/api/v5/account/config")
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            return True, self.formatter.account_config(data)
        return False, resp

    @capability.register
    async def set_position_mode(self, position_mode: str):
        """设置持仓模式
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-account-set-position-mode
        @param position_mode: long_short_mode：双向持仓 net_mode：单向持仓 仅适用交割/永续
        @return:
        """
        params = {"posMode": position_mode}
        resp = await self.post("/api/v5/account/set-position-mode", json=params)
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            return True, self.formatter.position_mode(data)
        return False, resp

    @capability.register
    async def set_leverage(self, instrument_name, lever, margin_mode, position_side=None):
        """设置杠杆倍数
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-account-set-leverage
        """
        instrument = InstrumentConverter.to_exchange(instrument_name)
        params = dict(
            instId=instrument,
            lever=lever,
            mgnMode=margin_mode,
        )
        if position_side:
            params["posSide"] = position_side
        resp = await self.post("/api/v5/account/set-leverage", json=params)
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            return True, self.formatter.leverage(data)
        return False, resp

    @capability.register
    async def get_leverage_info(self, instrument_name, margin_mode):
        """获取杠杆倍数
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-account-get-leverage
        """
        instrument = InstrumentConverter.to_exchange(instrument_name)
        params = dict(
            instId=instrument,
            mgnMode=margin_mode,
        )
        resp = await self.get("/api/v5/account/leverage-info", params=params)
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            return True, self.formatter.leverage(data)
        return False, resp

    @capability.register
    async def get_account_trade_fee(self, instrument_name, subject):
        """获取当前账户交易手续费费率
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-account-get-fee-rates
        """
        inst_type = SUBJECT_MAP[subject]
        params = dict(instType=inst_type)

        inst_id = InstrumentConverter.to_exchange(instrument_name)
        if subject in (config.SUBJECT_TYPE.SPOT, config.SUBJECT_TYPE.MARGIN):
            params["instId"] = inst_id
        else:
            currency = CurrencyGetter.get_currency(instrument_name, subject, self.exchange_name)
            params["uly"] = get_uly(currency, subject)

        resp = await self.get("/api/v5/account/trade-fee", params=params)
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            for d in data:
                d["instType"] = subject  # 修正返回的数据为EE的subject
            return True, self.formatter.trade_fee(data)
        return False, resp

    @capability.register
    async def borrow_repay(self, currency, side, amount):
        """尊享借币还币
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-account-vip-loans-borrow-and-repay
        """
        params = dict(ccy=currency, side=side, amt=amount)

        resp = await self.post("/api/v5/account/borrow-repay", json=params)
        code = resp.get("code")
        data = resp.get("data", None)
        if code == "0":
            return True, self.formatter.borrow_repay(data)
        return False, resp

    @capability.register
    async def purchase_redempt(self, currency: str, amount: str, side: str, rate: str = None):
        """
        余币宝 申购/赎回,模拟盘不支持此接口！
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-funding-savings-purchase-redemption
        ccy	String	是	币种名称，如 BTC
        amt	String	是	申购（赎回）数量
        side	String	是	操作类型
            purchase：申购 redempt：赎回
        rate	String	是	申购利率
            仅适用于申购，新申购的利率会覆盖上次申购的利率
            参数取值范围在1%到365%之间
        """
        params = {"ccy": currency, "amt": amount, "side": side}
        result = []
        if params["side"] == "purchase":
            if rate and (0.01 <= float(rate) <= 3.65):
                params["rate"] = rate
            else:
                logger.warning(f"rate need fit: 0.01 <= {rate} <= 3.65")
                return False, result
        res = await self.post("/api/v5/asset/purchase_redempt", json=params)
        if res.get("code", 1) != "0":
            logger.warning(res["msg"])
            return False, res
        return True, self.formatter.purchase_redempt(res["data"])

    @capability.register
    async def get_lending_rate_history(
        self,
        currency: str = None,
        after: int = None,
        before: int = None,
        limit: int = None,
    ):
        """
        获取市场借贷历史（公共）,模拟盘不支持此接口！
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-funding-get-public-borrow-history-public
        """
        params = {}
        if currency:
            params["ccy"] = currency
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit and limit < 100:
            params["limit"] = limit

        res = await self.get("/api/v5/asset/lending-rate-history", params=params)
        if res.get("code", 1) != "0":
            logger.warning(res["msg"])
            return False, res
        return True, self.formatter.lending_rate_history(res["data"])

    @capability.register
    async def get_currencies(self, currency: str = None):
        params = {}
        if currency:
            params["ccy"] = currency

        res = await self.get("/api/v5/asset/currencies", params=params)
        if res.get("code", 1) != "0":
            logger.warning(res["msg"])
            return False, res
        return True, self.formatter.currencies(res["data"])

    @capability.register
    async def get_saving_balance(self, currency: str = None):
        """
        获取余币宝余额
        """
        params = {}
        if currency:
            params["ccy"] = currency
        res = await self.get("/api/v5/asset/saving-balance", params=params)
        if res.get("code", 1) != "0":
            logger.warning(res["msg"])
            return False, res
        return True, self.formatter.saving_balance(res["data"])

    @capability.register
    async def get_balances(self, currency: str = None):
        """
        获取资金账户所有资产列表，查询各币种的余额、冻结和可用等信息。
        """
        params = {}
        if currency:
            params["ccy"] = currency
        res = await self.get("/api/v5/asset/balances", params=params)
        if res.get("code", 1) != "0":
            logger.warning(res["msg"])
            return False, res

        return True, self.formatter.balance(res["data"])

    @capability.register
    async def transfer(
        self,
        currency: str,
        amount: str,
        _from: str,
        to: str,
        sub_account: str = None,
        _type: str = None,
        loan_trans: bool = None,
        client_id: str = None,
        omit_post_risk: bool = None,
    ):
        """
        资金划转
        api doc:https://www.okx.com/docs-v5/zh/#rest-api-funding-funds-transfer
        支持母账户的资金账户划转到交易账户，母账户到子账户的资金账户和交易账户划转；
        子账户默认可转出至母账户，划转到同一母账户下的其他子账户，需要先调用“设置子账户转出权限”接口进行授权。
        ccy	String	是	币种，如 USDT
        amt	String	是	划转数量
        from	String	是	转出账户
            6：资金账户 18：交易账户
        to	String	是	转入账户
            6：资金账户 18：交易账户
        subAcct	String	可选	子账户名称，type 为1，2 或 4：subAcct 为必填项
        type	String	否	划转类型
            0：账户内划转
            1：母账户转子账户(仅适用于母账户APIKey)
            2：子账户转母账户(仅适用于母账户APIKey)
            3：子账户转母账户(仅适用于子账户APIKey)
            4：子账户转子账户(仅适用于子账户APIKey，且目标账户需要是同一母账户下的其他子账户)
            默认是0
        loanTrans	Boolean	否	是否支持跨币种保证金模式或组合保证金模式下的借币转入/转出
        true 或 false，默认false
        clientId	String	否	客户自定义ID
            字母（区分大小写）与数字的组合，可以是纯字母、纯数字且长度要在1-32位之间。
        omitPosRisk	String	否	是否忽略仓位风险
            默认为false
            仅适用于组合保证金模式

        """
        params = {
            "ccy": currency,
            "amt": amount,
            "from": _from,
            "to": to,
        }
        if _type:
            params["type"] = _type

        if str(_type) in ("1", "2", "4"):
            if not sub_account:
                logger.warning(f"when type is {_type}, sub_account is required")
                return False, []
            params["subAcct"] = sub_account

        if loan_trans:
            params["loanTrans"] = loan_trans
        if client_id:
            assert 1 <= len(client_id) <= 32
            params["clientId"] = client_id
        if omit_post_risk:
            params["omitPosRisk"] = omit_post_risk

        res = await self.post("/api/v5/asset/transfer", json=params)
        if res.get("code", 1) != "0":
            logger.warning(res["msg"])
            return False, res

        return True, self.formatter.transfer(res["data"])
