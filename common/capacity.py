import functools
from typing import Dict, List, Sequence

from basis_alpha import config
from tools.instruments import EEInstrument, parse_ee_instrument


class SubjectCap:
    def __init__(
        self,
        subject: str,
        default_base: List[str],
        default_quote: List[str],
        support_base=None,
        unsupport_base: Sequence[str] = (),
        support_quote=None,
        unsupport_quote: Sequence[str] = (),
        extra="",
    ):
        self.subject = subject

        self.base = set(support_base or default_base) - set(unsupport_base)
        self.quote = set(support_quote or default_quote) - set(unsupport_quote)
        if subject == config.SUBJECT_TYPE.OPTION.name:
            self.base = self.base.intersection(self.quote)
            self.quote = self.base.copy()

        self.extra = extra

    def is_support(self, ins: EEInstrument) -> bool:
        return (self.subject == ins.subject) and (ins.base in self.base) and (ins.quote in self.quote)

    def intersection(self, other):
        self.base = self.base.intersection(other.base)
        self.quote = self.quote.intersection(other.quote)
        self.extra = self.extra or other.extra
        return self

    def as_json(self) -> dict:
        return {
            "subject": self.subject,
            "base": list(self.base),
            "quote": list(self.quote),
            "extra": self.extra,
        }


class ExchangeBrokerCap:
    def __init__(self):
        self.private_api_cap: Dict[
            str, Dict[str, SubjectCap]
        ] = {}  # api -> {subject_type: {base: xx, quote: xx, extra: xx}}

    def base_currencies(self, subject: str) -> List[str]:
        raise NotImplementedError

    def quote_currencies(self, subject: str) -> List[str]:
        raise NotImplementedError

    def subjects(self) -> List[str]:
        raise NotImplementedError

    def subject_cap(
        self,
        subject: str,
        support_base=None,
        unsupport_base: Sequence[str] = (),
        support_quote=None,
        unsupport_quote: Sequence[str] = (),
        extra="",
    ):

        if subject not in self.subjects():
            raise Exception(f"exchange not support subject {subject}")

        return SubjectCap(
            subject,
            self.base_currencies(subject),
            self.quote_currencies(subject),
            support_base,
            unsupport_base,
            support_quote,
            unsupport_quote,
            extra,
        )

    def register(
        self,
        func=None,
        support_subjects: Sequence[str] = (),
        unsupport_subjects: Sequence[str] = (),
        support_base: Sequence[str] = (),
        unsupport_base: Sequence[str] = (),
        support_quote: Sequence[str] = (),
        unsupport_quote: Sequence[str] = (),
        subjects_caps: Sequence[SubjectCap] = (),
    ):
        if func is None:
            return functools.partial(
                self.register,
                support_subjects=support_subjects,
                unsupport_subjects=unsupport_subjects,
                support_base=support_base,
                unsupport_base=unsupport_base,
                support_quote=support_quote,
                unsupport_quote=unsupport_quote,
                subjects_caps=subjects_caps,
            )

        subjects = set(support_subjects or self.subjects()) - set(unsupport_subjects)
        m: Dict[str, SubjectCap] = {}
        for s in subjects:
            base = list(set(support_base or self.base_currencies(s)) - set(unsupport_base))
            quote = list(set(support_quote or self.quote_currencies(s)) - set(unsupport_quote))
            m[s] = SubjectCap(s, base, quote)

        # for special subject cap:
        for c in subjects_caps:
            if c.subject in m:
                m[c.subject].intersection(c)

        if func.__name__ not in self.private_api_cap:
            self.private_api_cap[func.__name__] = m
        else:
            self.private_api_cap[func.__name__].update(m)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    def get_instrument_cap(self, ee_instrument_name: str) -> List[str]:
        inst = parse_ee_instrument(ee_instrument_name)
        if not inst:
            return []

        rst = []
        for api, caps in self.private_api_cap.items():
            cap = caps.get(inst.subject)
            if cap and cap.is_support(inst):
                rst.append(api)
        return rst

    def as_json(self) -> list:
        """
        [
            {
                "api": "take_order",
                "caps": [
                    {
                        "subject": "SPOT",
                        "base": ["BTC", "ETH"],
                        "quote": ["USDT"]
                    }
                ]
            }
        ]
        """
        return [{"api": api, "caps": [c.as_json() for c in cap.values()]} for api, cap in self.private_api_cap.items()]


class InstrumentWithCap:  # for exchange info api
    def __init__(self, ins: EEInstrument, public_topics: Sequence[str] = (), api: Sequence[str] = ()) -> None:
        self.ins = ins
        self.public_topics = public_topics
        self.api = api

    def as_json(self):
        return {
            "instrument": self.ins._asdict(),
            "public_topics": self.public_topics,
            "api": self.api,
        }


def test_cap_decorator():
    class FakeExchangeCap(ExchangeBrokerCap):
        def base_currencies(self, subject: str) -> List[str]:
            return ["BTC", "ETH"]

        def quote_currencies(self, subject: str) -> List[str]:
            if subject == "OPTION":
                return ["BTC", "ETH"]
            return ["BTC", "ETH", "USDT"]

        def subjects(self) -> List[str]:
            return ["OPTION", "SPOT"]

    cap = FakeExchangeCap()

    class FakeBroker:
        @cap.register  # or @cap.register()
        def test_all_support(self):
            pass

        @cap.register(support_subjects=["OPTION"])
        def test_only_option(self):
            pass

        @cap.register(unsupport_subjects=["OPTION"])
        def test_exclude_option(self):
            pass

        @cap.register(support_base=["BTC"])
        def test_only_btc(self):
            pass

        @cap.register(unsupport_base=["BTC"])
        def test_exclude_btc(self):
            pass

        @cap.register(support_quote=["BTC"])
        def test_only_btc_quote(self):
            pass

        @cap.register(unsupport_quote=["BTC"])
        def test_exclude_btc_quote(self):
            pass

        @cap.register(subjects_caps=[cap.subject_cap("OPTION", support_base=["BTC"])])
        def test_option_only_btc(self):
            pass

        @cap.register(subjects_caps=[cap.subject_cap("OPTION", unsupport_base=["BTC"])])
        def test_option_exclude_btc(self):
            pass

        @cap.register(subjects_caps=[cap.subject_cap("SPOT", support_quote=["BTC"])])
        def test_spot_only_btc_quote(self):
            pass

        @cap.register(subjects_caps=[cap.subject_cap("SPOT", unsupport_quote=["BTC"])])
        def test_spot_exclude_btc_quote(self):
            pass

    import json

    print(json.dumps(cap.as_json(), indent=4))
