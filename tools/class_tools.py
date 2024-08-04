class Singleton:
    """常规单例，子类通过_init__可以覆盖类属性"""
    _instance = None
    _inited = False

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = object.__new__(cls)
        return cls._instance


class StrongSingleton(type):
    """强单例，一次实例化后不再执行__init__"""
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
