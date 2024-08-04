import logging
from dataclasses import asdict
from datetime import date
from typing import Dict, List, NamedTuple

from basis_alpha import config
from basis_alpha.config import STATE
from common.common import AmendOrder, BatchAmendOrder, DeliveryPrice, OrderInfo, Position, Settlement, Summary, Trade
from common.okx_common import (
    AccountConfig,
    BalanceItem,
    BorrowRepay,
    CurrencyItem,
    FundingBill,
    InstrumentConverter,
    InterestLimits,
    InterestLimitsRecords,
    LendingRateHistory,
    Leverage,
    MaxLoan,
    PositionMode,
    PurchaseRedempt,
    SavingBalanceItem,
    SizeConvertor,
    TradeFee,
    TransferItem,
)
from tools.instruments import get_subject_by, parse_ee_instrument

logger = logging.getLogger(__name__)


class DefaultZeroDict(dict):
    """
    字典值转换为float
    默认值字典，dict[key]默认为0.0
    !!!注意，这不会影响get()的行为
    """

    def __missing__(self, key):
        return 0.0

    def __getitem__(self, item):
        value = self.get(item)
        return value if value else 0.0


def okex_channel_detect(label):
    """OK交易渠道检测

    Args:
        label (str): 订单的客户端订单ID
    """
    if not label:
        return config.ORDERING_CHANNEL.OUTER
    return config.ORDERING_CHANNEL.INNER


class FeeDetail(NamedTuple):
    fee: float
    fee_asset: str
    fee_map: Dict[str, float]


class OkexFormatter:
    ORDER_STATE_MAPPING = {
        "live": STATE.POSTED,
        "filled": STATE.FILLED,
        "canceled": STATE.CANCELLED,
        "partially_filled": STATE.PARTIAL_FILLED,
    }

    def _order_fee(self, data) -> FeeDetail:
        fee = -1 * float(data.get("fee") or 0)  # 对于OKX，手续费扣除 为 ‘负数’，手续费返佣 为 ‘正数’，这和EE的规则相反，所以要乘-1
        fee_asset = (data.get("feeCcy") or config.FEE_ASSET_UNKNOWN).upper()
        rebate = -1 * float(data.get("rebate") or 0)
        rebate_asset = (data.get("rebateCcy") or config.FEE_ASSET_UNKNOWN).upper()
        if fee_asset == rebate_asset:
            fee += rebate
            return FeeDetail(
                fee=fee,
                fee_asset=fee_asset,
                fee_map={
                    fee_asset: fee,
                },
            )
        return FeeDetail(
            fee=fee,
            fee_asset=fee_asset,
            fee_map={
                fee_asset: fee,
                rebate_asset: rebate,
            },
        )

    def _order(self, data):
        """{'accFillSz': '1',
        'avgPx': '20930.4',
        'cTime': '1655376397830',
        'category': 'normal',
        'ccy': '',
        'clOrdId': '1655376397148718140',
        'fee': '-0.0000023888697779',
        'feeCcy': 'BTC',
        'fillPx': '20930.4',
        'fillSz': '1',
        'fillTime': '1655376397832',
        'instId': 'BTC-USD-220930',
        'instType': 'FUTURES',
        'lever': '',
        'ordId': '457617012238454784',
        'ordType': 'market',
        'pnl': '0',
        'posSide': 'net',
        'px': '',
        'rebate': '0',
        'rebateCcy': 'BTC',
        'side': 'buy',
        'slOrdPx': '',
        'slTriggerPx': '',
        'slTriggerPxType': '',
        'source': '',
        'state': 'filled',
        'sz': '1',
        'tag': '',
        'tdMode': 'cross',
        'tgtCcy': '',
        'tpOrdPx': '',
        'tpTriggerPx': '',
        'tpTriggerPxType': '',
        'tradeId': '11114712',
        'uTime': '1655376397833'}"""
        instrument_name = InstrumentConverter.to_system(data["instId"])
        subject = get_subject_by(instrument_name)
        # EE对于币本位：amount单位是USD (okex的单位是张数)
        # EE对于U本位和期权：amount单位是币数 （okex的单位是张数）
        force_convert = (
            True if subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name) else False
        )
        amount = SizeConvertor.to_system(
            size=float(data["sz"]), subject=subject, force_convert=force_convert, system_instrument=instrument_name
        )
        filled_amount = SizeConvertor.to_system(
            size=float(data.get("accFillSz", 0)),
            subject=subject,
            force_convert=force_convert,
            system_instrument=instrument_name,
        )
        fee_asset = data.get("feeCcy") or config.FEE_ASSET_UNKNOWN
        if fee_asset == config.FEE_ASSET_UNKNOWN:
            logger.error("fee_asset unknown, order_data: {}".format(data))

        label = data["clOrdId"]
        fee_detail = self._order_fee(data)
        return OrderInfo(
            exchange=config.EXCHANGE.OKEX.name,
            exchange_order_id=data["ordId"],
            order_id=data["clOrdId"],
            direction=config.SIDE.SELL if data["side"] == "sell" else config.SIDE.BUY,
            state=self.ORDER_STATE_MAPPING[data["state"]],  # status 需要在枚举类型之中
            amount=abs(amount),
            price=float(data.get("px", "0") or "0"),
            filled_amount=abs(filled_amount),
            avg_price=float(data.get("avgPx", "0") or "0"),
            fee=fee_detail.fee,
            fee_asset=fee_detail.fee_asset,
            fee_map=fee_detail.fee_map,
            instrument_name=instrument_name,
            created_at=int(data["cTime"]),
            updated_at=int(data["uTime"]),
            original_data=data,
            channel=okex_channel_detect(label),
        )._asdict()

    def order(self, dataset):
        # logger.debug(f'format order input: {dataset}')
        if isinstance(dataset, list):
            result = []
            for data in dataset:
                result.append(self._order(data))
            return result
        else:
            # dict, per order
            return self._order(dataset)

    def summary(self, data: Dict):
        """
         我们通过 Position channel 获取 mgnRatio = Margin ratio
         计算公式：
         reference：
         https://www.okx.com/support/hc/en-us/articles/360054690531-%E2%85%A3-Multi-currency-margin-mode-cross-margin-trading
         Margin ratio = Adjusted margin / (Maintenance margin + Transaction fees of position-reducing)
         {
         # 来自 account channel
            "availBal":"2.309819168967745",
            "availEq":"2.309819168967745",
            "cashBal":"2.7881857922877162",
            "ccy":"BTC",
            "coinUsdPrice":"39748.4",
            "crossLiab":"0",
            "disEq":"107074.3679715123",
            "eq":"2.6938032215513656",
            "eqUsd":"107074.3679715123",
            "frozenBal":"0.3839840525836206",
            "interest":"0",
            "isoEq":"0",
            "isoLiab":"0",
            "isoUpl":"0",
            "liab":"0",
            "maxLoan":"25.865724647203727",

         # 来自 position channel
            "mgnRatio":"18.813978414008325",   特殊情况："mgnRatio":""

        # 来自 greeks channel
            "notionalLever":"",
            "ordFrozen":"0",
            "stgyEq":"0",
            "twap":"0",
            "uTime":"1649729293454",
            "upl":"-0.0013825707363505",
            "deltaBS":"1.8908094961650468",
            "deltaPA":"-0.8025888701882187",
            "gammaBS":"-0.0001505790832193",
            "gammaPA":"-4.3802170917762450",
            "thetaBS":"279.9674896997475000",
            "thetaPA":"0.0068581991797705",
            "ts":"1649736036919",
            "vegaBS":"-102.7927347071201900",
            "vegaPA":"-0.0025860342028987"
         }
        """
        # logger.debug(f'format summary input: {data}')
        data = DefaultZeroDict(data)  # dict默认key值为0.0
        return Summary(
            currency=data.get("ccy", ""),
            equity=round(float(data["eq"]), 8),
            available_funds=round(float(data["availEq"]), 2),
            im="{}%".format(round(100 / float(data.get("mgnRatio") or "99999999"), 3)),
            mm="{}%".format(round(100 / float(data.get("mgnRatio") or "99999999"), 3)),
            options_pl=round(float(data["upl"]), 2),
            exchange=config.EXCHANGE.OKEX.name,
            cash_balance=float(data["cashBal"]),
            pnl=float(data["upl"]),
            updated_at_ts=int(data["uTime"]),
            leverage=0.0,
            delta_total=round(float(data["deltaBS"]), 8),
            options_delta=round(float(data["deltaBS"]), 8),
            future_delta=0.0,
            options_gamma=round(float(data["gammaBS"]), 8),
            options_theta=round(float(data["thetaBS"]), 8),
            options_vega=round(float(data["vegaBS"]), 8),
            options_value=0.0,
        )._asdict()

    def position(self, dataset: List):
        """{'adl': '5',
        'availPos': '',
        'avgPx': '21481.7',
        'baseBal': '',
        'cTime': '1655175542527',
        'ccy': 'BTC',
        'deltaBS': '',
        'deltaPA': '',
        'gammaBS': '',
        'gammaPA': '',
        'imr': '0.1365362133294671',
        'instId': 'BTC-USD-220617',
        'instType': 'FUTURES',
        'interest': '0',
        'last': '21443',
        'lever': '0',
        'liab': '',
        'liabCcy': '',
        'liqPx': '',
        'margin': '',
        'markPx': '21452.87',
        'mgnMode': 'cross',
        'mgnRatio': '10.491770629416866',
        'mmr': '0.1050278564072823',
        'notionalUsd': '99.99547846045775',
        'optVal': '',
        'pos': '1',
        'posCcy': '',
        'posId': '456774564037660673',
        'posSide': 'net',
        'quoteBal': '',
        'thetaBS': '',
        'thetaPA': '',
        'tradeId': '759934',
        'uTime': '1655175542527',
        'upl': '-0.0000062559114691',
        'uplRatio': '-0.0040316284021675',
        'usdPx': '',
        'vegaBS': '',
        'vegaPA': ''}"""
        positions = []
        for data in dataset:
            data = DefaultZeroDict(data)  # 默认key值为0.0
            # logger.info(f'format position input: {data}')
            instrument_name = InstrumentConverter.to_system(data["instId"])
            instrument = parse_ee_instrument(instrument_name, exchange=config.EXCHANGE.OKEX.name)
            subject = instrument.subject if instrument else get_subject_by(instrument_name)
            inst_type = data["instType"].lower()
            if inst_type == "option":
                size = SizeConvertor.to_system(size=float(data["pos"]), system_instrument=instrument_name)
                position = Position(
                    instrument_name=instrument_name,
                    subject=subject,
                    currency=instrument.base if instrument else "",
                    quote_currency=instrument.quote if instrument else "",
                    size=size,  # if negative position, we have -5
                    kind="option",
                    direction=config.SIDE.BUY if float(data["pos"]) > 0 else config.SIDE.SELL,
                    mark_price=round(float(data["markPx"]), 6),
                    average_price=round(float(data["avgPx"]), 6),
                    delta=float(data["deltaBS"]),
                    gamma=float(data["gammaBS"]),
                    theta=float(data["thetaBS"]),
                    vega=float(data["vegaBS"]),
                    pnl=float(data["upl"]),
                    options_value=float(data["optVal"]),
                    maintenance_margin=float(data["mmr"]),
                    initial_margin=float(data["imr"]),
                    unreleased_pnl=float(data["upl"]),  # okex 只有 unreleased_pnl
                )._asdict()
                positions.append(position)
            if inst_type == "futures" or inst_type == "swap":
                subject = get_subject_by(instrument_name)
                contract = float(data["pos"])  # 张数
                if contract == 0:  # 跳过空仓位
                    continue

                avg_price = float(data["avgPx"] or data["last"])  # 平均价格

                # size单位：将张数转换为币数 (提示：币本位的size只是展示作用，不参与逻辑计算)
                if subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name):  # 币本位
                    size_usd = SizeConvertor.to_system(contract, instrument_name, force_convert=True)  # 张数转换为USD
                    size = round(size_usd / avg_price, 8)  # 币数 = 美元数量/平均价格
                else:  # U本位
                    size = SizeConvertor.to_system(size=contract, system_instrument=instrument_name)  # 张数转换为币数
                    size_usd = SizeConvertor.to_system(
                        size=contract, system_instrument=instrument_name, force_convert=True, avg_price=avg_price
                    )  # 张数转换为USD

                position = Position(
                    instrument_name=instrument_name,
                    subject=subject,
                    currency=instrument.base if instrument else "",
                    quote_currency=instrument.quote if instrument else "",
                    size=size,
                    size_usd=size_usd,
                    direction=config.SIDE.BUY if size > 0 else config.SIDE.SELL,
                    kind="future",
                    average_price=round(avg_price, 6),
                    average_price_usd=round(avg_price, 6),
                    mark_price_usd=round(float(data["markPx"]), 6),
                    # mark_price = ? , need index price
                    pnl=float(data["upl"]),
                    initial_margin=float(data["imr"]),
                    maintenance_margin=float(data["mmr"]),
                    unreleased_pnl=float(data["upl"]),
                )._asdict()
                positions.append(position)
        return positions

    def trade(self, trades):
        # logger.debug(f'format trades input: {trades}')
        if isinstance(trades, dict):
            trades = [trades]

        formatted_trades = []
        for item in trades:
            if item["tradeId"]:
                instrument_name = InstrumentConverter.to_system(item["instId"])
                subject = get_subject_by(instrument_name)
                # EE对于币本位：size单位是USD (okex的单位是张数)
                # EE对于U本位和期权：size单位是币数 （okex的单位是张数）
                force_convert = (
                    True
                    if subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name)
                    else False
                )
                size = SizeConvertor.to_system(
                    size=float(item.get("fillSz", "0.0") or "0.0"),
                    subject=subject,
                    force_convert=force_convert,
                    system_instrument=instrument_name,
                )

                label = item.get("clOrdId", None)
                formatted_trades.append(
                    Trade(
                        order_id=item["ordId"],
                        trade_id=item["tradeId"],
                        instrument_name=instrument_name,
                        #  iv=item.get('iv', 0),
                        amount=abs(size),
                        side=config.SIDE.SELL if item["side"] == "sell" else config.SIDE.BUY,
                        price=float(item.get("fillPx", "0.0") or "0.0"),
                        is_maker=item.get("execType") == "M",
                        fee=-1 * float(item.get("fillFee", "0.0") or "0.0"),
                        # 对于OKX，手续费扣除 为 ‘负数’，手续费返佣 为 ‘正数’，这和EE的规则相反，所以要乘-1
                        fee_asset=item.get("fillFeeCcy", "").upper(),
                        label=label,
                        original_data=item,
                        channel=okex_channel_detect(label),
                        created_at=int(item.get("ts", "0")) or int(item.get("cTime", "0")),  # ts: rest / cTime: ws
                    )._asdict()
                )
        return formatted_trades

    @staticmethod
    def batch_amend_order(amend_result: dict):
        """
        Args:
            amend_result:
            {'443803558314647552': {
                'succeed': True, 'result': {
                    'clOrdId': '1652083012643552120', 'ordId': '443803558314647552',
                    'reqId': '', 'sCode': '0', 'sMsg': ''
            }},
            '443803558700523520': {
                'succeed': True, 'result': {
                    'clOrdId': '1652083012669971120', 'ordId': '443803558700523520',
                    'reqId': '', 'sCode': '0', 'sMsg': ''
            }}}

        Returns:

        """
        data, partial_success = {}, False
        for order_id, order_data in amend_result.items():
            if not order_data or not isinstance(order_data, dict):
                partial_success = True
                logger.warning("出现空的返回值, order_id:%s, order_data:%s", order_id, order_data)
                data[order_id] = AmendOrder(order_id=order_id, ret_code="-1", ret_msg=order_data._asdict())
                continue
            data[order_id] = AmendOrder(
                order_id=order_data.get("result", {}).get("clOrdId", ""),
                exchange_order_id=order_data.get("result", {}).get("ordId", ""),
                ret_code=order_data.get("result", {}).get("sCode", ""),
                ret_msg=order_data.get("result", {}).get("sMsg", ""),
            )._asdict()

        return BatchAmendOrder(
            ret_code="1" if partial_success else "0",
            ret_msg="partial success" if partial_success else "success",
            ret_data=data,
        )._asdict()

    def delivery_price(self, deliveries):
        logger.info(f"format deliveries: {deliveries}")
        data = [
            DeliveryPrice(
                delivery_price=item["px"], date=date.fromtimestamp(int(item["ts"]) / 1000).isoformat()
            )._asdict()
            for item in deliveries
        ]
        return data

    def settlement(self, settlements):
        logger.info(f"format settlements: {settlements}")
        data = [
            Settlement(
                timestamp=int(item["ts"]),
                instrument_name=InstrumentConverter.to_system(item["instId"]),
                size=SizeConvertor.to_system(
                    float(item["sz"]), InstrumentConverter.to_system(item["instId"]), force_convert=True
                ),
                settle_price=0,  # okex 暂无返回 settle price，先置为 0
                settle_pnl=float(item["pnl"]),
                original_data=item,
                session_upnl=0,  # okex 暂无返回 settle price，先置为 0
                session_rpnl=0,  # okex 暂无返回 settle price，先置为 0
                session_funding=0,  # okex 暂无返回 settle price，先置为 0
                type=item["type"],
            )._asdict()
            for item in settlements
        ]
        return data

    def purchase_redempt(self, data):
        data = [
            PurchaseRedempt(currency=item["ccy"], amount=item["amt"], side=item["side"], rate=item["rate"])._asdict()
            for item in data
        ]
        return data

    def balance(self, balances):
        data = [
            BalanceItem(
                currency=item["ccy"],
                available=float(item["availBal"]),
                balance=float(item["bal"]),
                frozen=float(item["frozenBal"]),
            )._asdict()
            for item in balances
        ]
        return data

    def saving_balance(self, saving_balances):
        data = [
            SavingBalanceItem(
                currency=item["ccy"],
                redempt_amount=float(item["redemptAmt"]),
                rate=float(item["rate"]),
                amount=float(item["amt"]),
                loan_amount=float(item["loanAmt"]),
                pending_amount=float(item["pendingAmt"]),
                earnings=float(item["earnings"]),
            )._asdict()
            for item in saving_balances
        ]
        return data

    def currencies(self, currencies):
        data = [
            CurrencyItem(
                currency=item["ccy"],
                can_deposit=item["canDep"],
                can_internal=item["canInternal"],
                can_withdraw=item["canWd"],
                chain=item["chain"],
                deposit_quota_fixed=float(item.get("depQuotaFixed") or 0),
                main_net=item["mainNet"],
                max_fee=float(item.get("maxFee") or 0),
                max_withdraw=float(item.get("maxWd") or 0),
                min_deposit=float(item.get("maxDep") or 0),
                min_deposit_arrival_confirm=float(item.get("minDepArrivalConfirm") or 0),
                min_fee=float(item.get("minFee") or 0),
                min_withdraw=float(item.get("minWd") or 0),
                min_withdraw_unlock_confirm=float(item.get("minWdUnlockConfirm") or 0),
                name=item["name"],
                need_tag=item["needTag"],
                used_deposit_quota_fixed=float(item.get("usedDepQuotaFixed") or 0),
                used_withdraw_quota=float(item["usedWdQuota"]),
                withdraw_quota=float(item["wdQuota"]),
                withdraw_tick_sz=int(item["wdTickSz"]),
            )._asdict()
            for item in currencies
        ]
        return data

    def transfer(self, trans):
        data = [
            TransferItem(
                currency=item["ccy"],
                amount=item["amt"],
                trans_id=item["transId"],
                from_=item["from"],
                to=item["to"],
                client_id=item["clientId"],
            )._asdict()
            for item in trans
        ]
        return data

    def lending_rate_history(self, rates):
        data = [
            LendingRateHistory(
                currency=item["ccy"], lending_amount=item["amt"], lending_rate=item["rate"], lending_time=item["ts"]
            )._asdict()
            for item in rates
        ]
        return data

    @staticmethod
    def account_config(configs):
        return [
            asdict(
                AccountConfig(
                    account_level=item["acctLv"],
                    auto_loan=item["autoLoan"],
                    contract_isolated_mode=item["ctIsoMode"],
                    greeks_type=item["greeksType"],
                    level=item["level"],
                    level_temporary=item["levelTmp"],
                    liquidation_gear=item["liquidationGear"],
                    margin_isolated_mode=item["mgnIsoMode"],
                    position_mode=item["posMode"],
                    spot_offset_type=item["spotOffsetType"],
                )
            )
            for item in configs
        ]

    @staticmethod
    def position_mode(data):
        return [asdict(PositionMode(position_mode=item["posMode"])) for item in data]

    @staticmethod
    def leverage(data):
        return [
            asdict(
                Leverage(
                    instrument_name=InstrumentConverter.to_system(item["instId"]),
                    lever=item["lever"],
                    margin_mode=item["mgnMode"],
                    position_side=item["posSide"],
                )
            )
            for item in data
        ]

    @staticmethod
    def trade_fee(data):
        return [
            asdict(
                TradeFee(
                    delivery=item["delivery"],
                    exercise=item["exercise"],
                    subject=item["instType"],
                    level=item["level"],
                    maker=item["maker"],
                    maker_u=item["makerU"],
                    maker_usdc=item["makerUSDC"],
                    taker=item["taker"],
                    taker_u=item["takerU"],
                    taker_usdc=item["takerUSDC"],
                    timestamp=item["ts"],
                )
            )
            for item in data
        ]

    @staticmethod
    def borrow_repay(data):
        return [
            asdict(
                BorrowRepay(
                    amount=item["amt"],
                    available_loan=item["availLoan"],
                    currency=item["ccy"],
                    loan_quota=item["loanQuota"],
                    possess_loan=item["posLoan"],
                    side=item["side"],
                    used_loan=item["usedLoan"],
                )
            )
            for item in data
        ]

    @staticmethod
    def interest_limits(data):
        return [
            asdict(
                InterestLimits(
                    debt=item["debt"],
                    interest=item["interest"],
                    next_discount_time=item["nextDiscountTime"],
                    next_interest_time=item["nextInterestTime"],
                    records=[
                        InterestLimitsRecords(
                            available_loan=i["availLoan"],
                            currency=i["ccy"],
                            interest=i["interest"],
                            loan_quota=i["loanQuota"],
                            possess_loan=i["posLoan"],
                            rate=i["rate"],
                            surplus_limit=i["surplusLmt"],
                            used_limit=i["usedLmt"],
                            used_loan=i["usedLoan"],
                        )
                        for i in item["records"]
                    ],
                )
            )
            for item in data
        ]

    @staticmethod
    def max_loan(data):
        return [
            asdict(
                MaxLoan(
                    instrument_name=InstrumentConverter.to_system(item["instId"]),
                    margin_mode=item["mgnMode"],
                    margin_currency=item["mgnCcy"],
                    max_loan=item["maxLoan"],
                    currency=item["ccy"],
                    side=item["side"],
                )
            )
            for item in data
        ]

    @staticmethod
    def funding_bill(data):
        res = []
        for item in data:
            instrument_name = InstrumentConverter.to_system(item["instId"])
            subject = get_subject_by(instrument_name)
            force_convert = (
                True if subject in (config.SUBJECT_TYPE.FUTURE_USD.name, config.SUBJECT_TYPE.SWAP_USD.name) else False
            )
            res.append(
                asdict(
                    FundingBill(
                        bill_id=item["billId"],
                        currency=item["ccy"],
                        instrument_name=instrument_name,
                        pnl=float(item["pnl"]),
                        size=float(SizeConvertor.to_system(item["sz"], instrument_name, force_convert=force_convert)),
                        price=float(item["price"]),
                        ts=int(item["ts"]),
                    )
                )
            )
        return res
