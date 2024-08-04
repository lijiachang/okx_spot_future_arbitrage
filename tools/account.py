def make_printable_account(accountid) -> str:
    if not accountid:
        return "-"
    return str(accountid)[:6]

