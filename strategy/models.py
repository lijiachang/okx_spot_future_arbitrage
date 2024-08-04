from django.conf import settings
from django.db import models

from basis_alpha import config
from basis_alpha.config import SIDE_ITEMS
from tools.aes_encrypt import AesEncrypt


class Account(models.Model):
    """账户"""

    name = models.CharField(max_length=100, verbose_name="名称", db_index=True)
    exchange = models.CharField(max_length=100, verbose_name="交易所名称")
    api_key = models.CharField(max_length=200, verbose_name="Api Key")
    api_secret = models.CharField(max_length=200, verbose_name="Api Secret")
    api_passphrase = models.CharField(max_length=200, null=True, blank=True, verbose_name="Api passphrase")
    taker_fee_rate = models.DecimalField(max_digits=5, decimal_places=4, verbose_name="Taker费率")
    maker_fee_rate = models.DecimalField(max_digits=5, decimal_places=4, verbose_name="Maker费率")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")

    class Meta:
        verbose_name = "账户"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name

    def decrypt_api_secret(self):
        """解密API Secret"""
        aes_encrypt = AesEncrypt(settings.API_SECRET_SALT)
        return aes_encrypt.decrypt(self.api_secret)


class Strategy(models.Model):
    """策略配置"""

    name = models.CharField(max_length=100, verbose_name="名称")
    accounts = models.ManyToManyField(Account, verbose_name="关联账户")
    manager = models.CharField(max_length=100, verbose_name="管理人")
    min_open_rate = models.DecimalField(
        max_digits=5, decimal_places=2, verbose_name="开仓最低收益率", help_text="价差超过该值可开仓, 单位 %"
    )
    max_close_rate = models.DecimalField(
        max_digits=5, decimal_places=2, verbose_name="平仓最高收益率", help_text="价差低于该值可平仓, 单位 %"
    )
    per_order_usd = models.PositiveIntegerField(verbose_name="单笔订单金额(USD)", help_text="100的整数倍，每笔订单按照该数据进行下单")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    max_position_ratio = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name="持仓最大占比",
        help_text="可选。策略持仓占市场当前持仓的最大比例，超过该比例不进行开仓",
    )
    max_position_value = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="最大持仓市值", help_text="可选。限制单个币种的最大持仓量USD市值"
    )
    black_list = models.JSONField(null=True, blank=True, verbose_name="币种黑名单", help_text="可选。不进行交易的币种")

    class Meta:
        verbose_name = "策略配置"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


class EquitySnapshot(models.Model):
    """净值快照"""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, verbose_name="关联账户")
    net_value = models.DecimalField(max_digits=10, decimal_places=4, verbose_name="净值")
    raw_data = models.JSONField(verbose_name="原始数据")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="时间")

    class Meta:
        verbose_name = "净值快照"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.account} - {self.timestamp}"


class Order(models.Model):
    """订单"""

    order_id = models.CharField(max_length=100, verbose_name="Order ID")
    client_order_id = models.CharField(max_length=100, verbose_name="Client Order ID")
    instrument_name = models.CharField(max_length=100, verbose_name="交易对")
    side = models.IntegerField(choices=SIDE_ITEMS, verbose_name="方向")
    price = models.DecimalField(max_digits=20, decimal_places=8, verbose_name="价格")
    filled_price = models.DecimalField(max_digits=20, decimal_places=8, verbose_name="成交价格")
    size = models.DecimalField(max_digits=20, decimal_places=8, verbose_name="数量")
    filled_size = models.DecimalField(max_digits=20, decimal_places=8, verbose_name="成交数量")
    fee = models.DecimalField(max_digits=20, decimal_places=8, verbose_name="手续费")
    fee_currency = models.CharField(max_length=20, verbose_name="手续费币种")
    state = models.IntegerField(choices=config.STATE_ITEMS, default=config.STATE.INIT, verbose_name="状态")
    raw_data = models.JSONField(verbose_name="原始数据")
    created_at = models.DateTimeField(verbose_name="创建时间")
    updated_at = models.DateTimeField(verbose_name="更新时间")

    class Meta:
        verbose_name = "订单"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.order_id
