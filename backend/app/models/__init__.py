"""
Import every model here so that Base.metadata is fully populated when
Alembic's `--autogenerate` inspects it. Forgetting to add a new model to
this file is the #1 cause of "migration didn't pick up my new table."
"""
from app.models.business import Business
from app.models.user import User
from app.models.product import Product, Channel, ChannelProduct
from app.models.order import Order, OrderLine, Return
from app.models.inventory import InventorySnapshot
from app.models.import_batch import ImportBatch, ImportRawRow
from app.models.metrics import DailyProductMetric, DailyChannelMetric
from app.models.insight import ProductInsight

__all__ = [
    "Business",
    "User",
    "Product",
    "Channel",
    "ChannelProduct",
    "Order",
    "OrderLine",
    "Return",
    "InventorySnapshot",
    "ImportBatch",
    "ImportRawRow",
    "DailyProductMetric",
    "DailyChannelMetric",
    "ProductInsight",
]
