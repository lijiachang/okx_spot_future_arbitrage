from django.contrib import admin

from strategy.models import Account, EquitySnapshot, Order, Strategy


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "api_key", "taker_fee_rate", "maker_fee_rate", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "api_key")


@admin.register(Strategy)
class StrategyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "manager",
        "min_open_rate",
        "max_close_rate",
        "per_order_usd",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "manager")
    filter_horizontal = ("accounts",)


@admin.register(EquitySnapshot)
class NetValueSnapshotAdmin(admin.ModelAdmin):
    list_display = ("account", "net_value", "timestamp")
    list_filter = ("account",)
    date_hierarchy = "timestamp"


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_id",
        "client_order_id",
        "side",
        "instrument_name",
        "price",
        "filled_price",
        "size",
        "filled_size",
        "fee",
        "fee_currency",
        "state",
        "created_at",
        "updated_at",
    )
    list_filter = ("instrument_name", "fee_currency")
    search_fields = ("order_id", "client_order_id", "instrument_name")
    date_hierarchy = "created_at"
