from dataclasses import asdict


class IgnoreUnconcernedKey:
    def __new__(cls, *args, **kwargs):
        try:
            initializer = cls.__initializer
        except AttributeError:
            cls.__initializer = initializer = cls.__init__
            cls.__init__ = lambda *a, **k: None

        invalid_keys = [k for k in kwargs.keys() if k not in cls.__dataclass_fields__.keys()]
        for k in invalid_keys:
            kwargs.pop(k)
        ins = object.__new__(cls)
        initializer(ins, *args, **kwargs)
        return ins

    def as_dict(self):
        d = asdict(self)
        invalid_key = [k for k, v in d.items() if v is None]
        for k in invalid_key:
            d.pop(k)
        return d


def camel_to_underline(camel_format):
    """驼峰转下划线"""
    underline_format = ''
    for _s_ in camel_format:
        if _s_.isupper():
            underline_format += '_' + _s_.lower()
        else:
            underline_format += _s_
    return underline_format


def build_dataclass_code_from_dict(data_dict: dict) -> str:
    """小工具：根据数据字典生成 dataclass 的代码（将结果复制到你的代码中）"""
    code = ''
    for key, value in data_dict.items():
        key = camel_to_underline(key)
        code += f'{key}: {type(value).__name__}\n'
    return code


def build_dataclass_args_from_dict(data_dict: dict) -> str:
    """小工具：根据数据字典生成 dataclass 的参数（将结果复制到你的代码中）"""
    code = ''
    for key, value in data_dict.items():
        key_ = camel_to_underline(key)
        code += f"{key_}=item['{key}'],\n"
    return code[:-2]


if __name__ == '__main__':
    # test
    data_dict = {'acctLv': 4,
                 'autoLoan': True,
                 'ctIsoMode': 'automatic', }
    print(build_dataclass_code_from_dict(data_dict))
    print('-' * 100)
    print(build_dataclass_args_from_dict(data_dict))
