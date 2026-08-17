"""Seed CommerceIQ with a realistic demo dataset.

Usage:
    python seed.py
    python seed.py --reset

The script uses the application's SQLAlchemy models and password hashing.
It is intentionally idempotent-by-default: if the database already contains
businesses, it refuses to seed unless --reset is supplied.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete

# Import Base first so every model is registered.
from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models import (
    Business,
    Channel,
    ChannelProduct,
    DailyChannelMetric,
    DailyProductMetric,
    ImportBatch,
    ImportRawRow,
    InventorySnapshot,
    Order,
    OrderLine,
    Product,
    ProductInsight,
    Return,
    User,
)


PASSWORD = "Demo@12345"
TODAY = date.today()


def d(value: str | Decimal) -> Decimal:
    return Decimal(str(value))


def reset_database(session):
    """Delete all rows in FK-safe order."""
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))
    session.commit()


def seed(session):
    existing = session.query(Business).first()
    if existing:
        raise RuntimeError(
            "Database already contains data. Run `python seed.py --reset` "
            "if you want to replace it with demo data."
        )

    # ------------------------------------------------------------------
    # 1. BUSINESS + USERS
    # ------------------------------------------------------------------
    business = Business(name="Acme Retail India")
    session.add(business)
    session.flush()

    owner = User(
        business_id=business.id,
        email="owner@acmeretail.demo",
        hashed_password=hash_password(PASSWORD),
        role="owner",
    )
    analyst = User(
        business_id=business.id,
        email="analyst@acmeretail.demo",
        hashed_password=hash_password(PASSWORD),
        role="analyst",
    )
    session.add_all([owner, analyst])

    # ------------------------------------------------------------------
    # 2. CHANNELS
    # ------------------------------------------------------------------
    amazon = Channel(business_id=business.id, name="Amazon India", type="marketplace")
    flipkart = Channel(business_id=business.id, name="Flipkart", type="marketplace")
    d2c = Channel(business_id=business.id, name="Acme D2C", type="d2c")
    offline = Channel(business_id=business.id, name="Offline Retail", type="offline")
    session.add_all([amazon, flipkart, d2c, offline])
    session.flush()

    # ------------------------------------------------------------------
    # 3. MASTER PRODUCTS
    # ------------------------------------------------------------------
    products = [
        Product(
            business_id=business.id,
            sku="ACM-AIR141-BLK",
            name="Airdopes 141 Black",
            brand="boAt",
            category="Audio",
            cost_price=d("650.00"),
            selling_price=d("1299.00"),
        ),
        Product(
            business_id=business.id,
            sku="ACM-AIR141-WHT",
            name="Airdopes 141 White",
            brand="boAt",
            category="Audio",
            cost_price=d("650.00"),
            selling_price=d("1299.00"),
        ),
        Product(
            business_id=business.id,
            sku="ACM-WAVE-SIG2",
            name="Wave Sigma 2 Smartwatch",
            brand="Noise",
            category="Wearables",
            cost_price=d("1100.00"),
            selling_price=d("1999.00"),
        ),
        Product(
            business_id=business.id,
            sku="ACM-USBC-1M",
            name="USB-C Fast Charging Cable 1m",
            brand="Acme",
            category="Accessories",
            cost_price=d("120.00"),
            selling_price=d("399.00"),
        ),
        Product(
            business_id=business.id,
            sku="ACM-SBAR-MINI",
            name="Mini Bluetooth Soundbar",
            brand="Acme Audio",
            category="Audio",
            cost_price=d("1800.00"),
            selling_price=d("3499.00"),
        ),
        Product(
            business_id=business.id,
            sku="ACM-BAG-25L",
            name="Urban Travel Backpack 25L",
            brand="Acme Gear",
            category="Travel",
            cost_price=d("900.00"),
            selling_price=d("1799.00"),
        ),
    ]
    session.add_all(products)
    session.flush()

    p = {product.sku: product for product in products}

    # ------------------------------------------------------------------
    # 4. CHANNEL -> PRODUCT SKU MAPPINGS
    # ------------------------------------------------------------------
    mappings = []

    mapping_specs = [
        (amazon, p["ACM-AIR141-BLK"], "B0AIR141BLK", "AIR141-BLK-AMZ", "Airdopes 141 Black", "exact"),
        (amazon, p["ACM-AIR141-WHT"], "B0AIR141WHT", "AIR141-WHT-AMZ", "Airdopes 141 White", "exact"),
        (amazon, p["ACM-WAVE-SIG2"], "B0WAVESIGMA2", "WAVE-SIG2-AMZ", "Noise Wave Sigma 2", "exact"),
        (amazon, p["ACM-USBC-1M"], "B0USBC1M001", "USBC-1M-AMZ", "USB C Cable 1m", "alias"),
        (flipkart, p["ACM-AIR141-BLK"], "FSN-AIR141BLK", "AIR141-BLK-FK", "boAt Airdopes 141 Black", "exact"),
        (flipkart, p["ACM-WAVE-SIG2"], "FSN-WAVESIG2", "WAVE-SIG2-FK", "Noise Smartwatch Sigma 2", "manual"),
        (flipkart, p["ACM-BAG-25L"], "FSN-BAG25L", "BAG-25L-FK", "Acme Urban Backpack", "exact"),
        (d2c, p["ACM-AIR141-BLK"], None, "ACM-AIR141-BLK", "Airdopes 141 Black", "exact"),
        (d2c, p["ACM-SBAR-MINI"], None, "ACM-SBAR-MINI", "Mini Bluetooth Soundbar", "exact"),
        (d2c, p["ACM-BAG-25L"], None, "ACM-BAG-25L", "Urban Travel Backpack", "exact"),
        (offline, p["ACM-USBC-1M"], None, "POS-USBC-1M", "USB-C Cable", "manual"),
        (offline, p["ACM-BAG-25L"], None, "POS-BAG-25L", "Travel Backpack", "manual"),
    ]

    for channel, product, external_id, external_sku, external_name, method in mapping_specs:
        mappings.append(
            ChannelProduct(
                business_id=business.id,
                product_id=product.id,
                channel_id=channel.id,
                external_product_id=external_id,
                external_sku=external_sku,
                external_name=external_name,
                mapping_method=method,
            )
        )
    session.add_all(mappings)
    session.flush()

    # Quick lookup for order creation.
    mapping_by_channel_sku = {
        (m.channel_id, m.product_id): m for m in mappings
    }

    # ------------------------------------------------------------------
    # 5. ORDERS + ORDER LINES
    # ------------------------------------------------------------------
    order_specs = [
        ("AMZ-10001", amazon, 2, [("ACM-AIR141-BLK", 1, "1299", "100", "65")]),
        ("AMZ-10002", amazon, 3, [("ACM-AIR141-BLK", 2, "2598", "200", "130"), ("ACM-WAVE-SIG2", 1, "1999", "150", "100")]),
        ("FK-20001", flipkart, 4, [("ACM-AIR141-BLK", 1, "1299", "50", "65"), ("ACM-BAG-25L", 1, "1799", "100", "90")]),
        ("D2C-30001", d2c, 5, [("ACM-SBAR-MINI", 1, "3499", "300", "0")]),
        ("AMZ-10003", amazon, 6, [("ACM-WAVE-SIG2", 2, "3998", "300", "200")]),
        ("D2C-30002", d2c, 7, [("ACM-AIR141-BLK", 1, "1299", "0", "0"), ("ACM-BAG-25L", 1, "1799", "200", "0")]),
        ("FK-20002", flipkart, 8, [("ACM-WAVE-SIG2", 1, "1999", "100", "100")]),
        ("AMZ-10004", amazon, 9, [("ACM-USBC-1M", 3, "1197", "100", "60")]),
        ("OFF-40001", offline, 10, [("ACM-USBC-1M", 5, "1995", "0", "0")]),
        ("D2C-30003", d2c, 11, [("ACM-SBAR-MINI", 1, "3499", "500", "0"), ("ACM-USBC-1M", 2, "798", "50", "0")]),
        ("AMZ-10005", amazon, 12, [("ACM-AIR141-WHT", 2, "2598", "200", "130")]),
        ("FK-20003", flipkart, 13, [("ACM-BAG-25L", 2, "3598", "200", "180")]),
    ]

    orders = []
    order_lines = []

    for external_order_id, channel, day_offset, lines in order_specs:
        order = Order(
            business_id=business.id,
            channel_id=channel.id,
            external_order_id=external_order_id,
            order_date=TODAY - timedelta(days=day_offset),
            customer_ref=f"CUST-{day_offset:03d}",
        )
        orders.append(order)
        session.add(order)
        session.flush()

        for sku, quantity, gross, discount, fee in lines:
            product = p[sku]
            cp = mapping_by_channel_sku.get((channel.id, product.id))
            order_lines.append(
                OrderLine(
                    business_id=business.id,
                    order_id=order.id,
                    product_id=product.id,
                    channel_product_id=cp.id if cp else None,
                    quantity=quantity,
                    gross_amount=d(gross),
                    discount_amount=d(discount),
                    fee_amount=d(fee),
                    shipping_amount=d("40.00") if channel.id in (amazon.id, flipkart.id) else d("0.00"),
                    tax_amount=d("18.00"),
                )
            )

    session.add_all(order_lines)
    session.flush()

    # ------------------------------------------------------------------
    # 6. RETURNS
    # ------------------------------------------------------------------
    # Return one Airdopes unit and one smartwatch unit.
    session.add_all([
        Return(
            business_id=business.id,
            order_line_id=order_lines[0].id,
            quantity=1,
            return_date=TODAY - timedelta(days=1),
            reason="Customer changed mind",
            refund_amount=d("1199.00"),
        ),
        Return(
            business_id=business.id,
            order_line_id=order_lines[3].id,
            quantity=1,
            return_date=TODAY,
            reason="Product not as expected",
            refund_amount=d("3199.00"),
        ),
    ])

    # ------------------------------------------------------------------
    # 7. INVENTORY SNAPSHOTS
    # ------------------------------------------------------------------
    # A few daily snapshots demonstrate declining stock and a stockout-risk
    # scenario. channel_id=None means warehouse/global stock.
    inventory_specs = [
        ("ACM-AIR141-BLK", amazon, 18),
        ("ACM-AIR141-BLK", flipkart, 9),
        ("ACM-AIR141-BLK", None, 42),
        ("ACM-AIR141-WHT", amazon, 24),
        ("ACM-WAVE-SIG2", amazon, 7),
        ("ACM-WAVE-SIG2", flipkart, 4),
        ("ACM-SBAR-MINI", d2c, 3),
        ("ACM-USBC-1M", amazon, 65),
        ("ACM-BAG-25L", flipkart, 11),
    ]

    for sku, channel, qty in inventory_specs:
        product = p[sku]
        for days_ago, multiplier in [(2, 1.35), (1, 1.15), (0, 1.0)]:
            session.add(
                InventorySnapshot(
                    business_id=business.id,
                    product_id=product.id,
                    channel_id=channel.id if channel else None,
                    snapshot_date=TODAY - timedelta(days=days_ago),
                    quantity_on_hand=max(0, round(qty * multiplier)),
                )
            )

    # ------------------------------------------------------------------
    # 8. IMPORT BATCHES + RAW ROWS
    # ------------------------------------------------------------------
    amazon_sales = ImportBatch(
        business_id=business.id,
        channel_id=amazon.id,
        file_name="amazon_orders_aug_2026.csv",
        storage_path="imports/acme/2026/08/amazon_orders_aug_2026.csv",
        import_type="sales",
        status="completed",
        row_count=5,
        error_count=1,
    )
    flipkart_inventory = ImportBatch(
        business_id=business.id,
        channel_id=flipkart.id,
        file_name="flipkart_inventory_2026-08-16.csv",
        storage_path="imports/acme/2026/08/flipkart_inventory_2026-08-16.csv",
        import_type="inventory",
        status="completed",
        row_count=3,
        error_count=0,
    )
    session.add_all([amazon_sales, flipkart_inventory])
    session.flush()

    session.add_all([
        ImportRawRow(
            import_batch_id=amazon_sales.id,
            raw_data={
                "order_id": "AMZ-10005",
                "sku": "AIR141-WHT-AMZ",
                "qty": "2",
                "item_price": "2598.00",
                "order_date": str(TODAY - timedelta(days=12)),
            },
            processed=True,
        ),
        ImportRawRow(
            import_batch_id=amazon_sales.id,
            raw_data={
                "order_id": "AMZ-BAD-001",
                "sku": "UNKNOWN-SKU",
                "qty": "1",
                "item_price": "999.00",
                "order_date": str(TODAY - timedelta(days=11)),
            },
            processed=False,
            error_message="No channel SKU mapping found",
        ),
        ImportRawRow(
            import_batch_id=amazon_sales.id,
            raw_data={
                "order_id": "AMZ-10004",
                "sku": "USBC-1M-AMZ",
                "qty": "3",
                "item_price": "1197.00",
                "order_date": str(TODAY - timedelta(days=9)),
            },
            processed=True,
        ),
        ImportRawRow(
            import_batch_id=flipkart_inventory.id,
            raw_data={"sku": "BAG-25L-FK", "quantity": "11", "snapshot_date": str(TODAY)},
            processed=True,
        ),
        ImportRawRow(
            import_batch_id=flipkart_inventory.id,
            raw_data={"sku": "WAVE-SIG2-FK", "quantity": "4", "snapshot_date": str(TODAY)},
            processed=True,
        ),
    ])

    # ------------------------------------------------------------------
    # 9. DAILY PRODUCT METRICS
    # ------------------------------------------------------------------
    # These are materialized analytics records. Keep them plausible and
    # intentionally include both strong and weak-performing products.
    metric_specs = [
        ("ACM-AIR141-BLK", amazon, 2, 3, "3897", "3697", "200", "195", "310"),
        ("ACM-AIR141-BLK", amazon, 3, 2, "2598", "2398", "200", "130", "360"),
        ("ACM-AIR141-BLK", flipkart, 4, 1, "1299", "1249", "50", "65", "190"),
        ("ACM-AIR141-WHT", amazon, 12, 2, "2598", "2398", "200", "130", "260"),
        ("ACM-WAVE-SIG2", amazon, 6, 2, "3998", "3698", "300", "200", "500"),
        ("ACM-WAVE-SIG2", flipkart, 8, 1, "1999", "1899", "100", "100", "200"),
        ("ACM-SBAR-MINI", d2c, 5, 1, "3499", "3199", "300", "0", "3199"),
        ("ACM-USBC-1M", amazon, 9, 3, "1197", "1097", "100", "60", "180"),
        ("ACM-USBC-1M", offline, 10, 5, "1995", "1995", "0", "0", "0"),
        ("ACM-BAG-25L", flipkart, 13, 2, "3598", "3398", "200", "180", "300"),
    ]

    for sku, channel, days_ago, units, gross, net, discount, fee, returns_amount in metric_specs:
        gross_d = d(gross)
        net_d = d(net)
        fee_d = d(fee)
        returns_d = d(returns_amount)
        product = p[sku]
        # Approximate contribution profit: net revenue - COGS - fees - returns.
        cogs = product.cost_price * units
        contribution = net_d - cogs - fee_d - returns_d
        realized_price = net_d / units if units else d("0")

        session.add(
            DailyProductMetric(
                business_id=business.id,
                product_id=product.id,
                channel_id=channel.id,
                date=TODAY - timedelta(days=days_ago),
                units_sold=units,
                gross_revenue=gross_d,
                net_revenue=net_d,
                discount_amount=d(discount),
                fee_amount=fee_d,
                returns_amount=returns_d,
                contribution_profit=contribution,
                realized_price=realized_price,
            )
        )

    # ------------------------------------------------------------------
    # 10. DAILY CHANNEL METRICS
    # ------------------------------------------------------------------
    channel_metric_specs = [
        (amazon, 2, "3897", "3697", 1, 3, "310"),
        (amazon, 3, "2598", "2398", 1, 2, "360"),
        (amazon, 6, "3998", "3698", 1, 2, "500"),
        (flipkart, 4, "3098", "2948", 1, 2, "390"),
        (flipkart, 8, "1999", "1899", 1, 1, "200"),
        (flipkart, 13, "3598", "3398", 1, 2, "300"),
        (d2c, 5, "3499", "3199", 1, 1, "250"),
        (offline, 10, "1995", "1995", 1, 5, "0"),
    ]

    for channel, days_ago, gross, net, orders_count, units, fee in channel_metric_specs:
        gross_d = d(gross)
        net_d = d(net)
        fee_d = d(fee)
        # Use an approximate channel-level contribution based on a blended
        # margin assumption for demo purposes.
        contribution = net_d - fee_d - (gross_d * d("0.45"))
        session.add(
            DailyChannelMetric(
                business_id=business.id,
                channel_id=channel.id,
                date=TODAY - timedelta(days=days_ago),
                gross_revenue=gross_d,
                net_revenue=net_d,
                orders=orders_count,
                units=units,
                contribution_profit=contribution,
            )
        )

    # ------------------------------------------------------------------
    # 11. PRODUCT INSIGHTS
    # ------------------------------------------------------------------
    session.add_all([
        ProductInsight(
            business_id=business.id,
            product_id=p["ACM-AIR141-BLK"].id,
            type="DEMAND_SPIKE",
            severity="medium",
            confidence=d("0.91"),
            message="Airdopes 141 Black sales are trending above the recent baseline on Amazon.",
            recommendation="Increase Amazon replenishment quantity and monitor daily sell-through.",
        ),
        ProductInsight(
            business_id=business.id,
            product_id=p["ACM-WAVE-SIG2"].id,
            type="STOCKOUT_RISK",
            severity="high",
            confidence=d("0.94"),
            message="Amazon stock is low relative to recent smartwatch demand.",
            recommendation="Replenish Amazon inventory within the next 2-3 days.",
        ),
        ProductInsight(
            business_id=business.id,
            product_id=p["ACM-SBAR-MINI"].id,
            type="LOW_MARGIN",
            severity="medium",
            confidence=d("0.87"),
            message="Discounting is reducing contribution margin on the Mini Bluetooth Soundbar.",
            recommendation="Review discount depth and test a smaller promotional discount.",
        ),
        ProductInsight(
            business_id=business.id,
            product_id=p["ACM-USBC-1M"].id,
            type="CHANNEL_OPPORTUNITY",
            severity="low",
            confidence=d("0.82"),
            message="Offline volume is strong for the USB-C cable while Amazon has higher realized price.",
            recommendation="Use offline volume for inventory clearance while protecting Amazon pricing.",
        ),
        ProductInsight(
            business_id=business.id,
            product_id=p["ACM-BAG-25L"].id,
            type="RETURN_RATE",
            severity="low",
            confidence=d("0.78"),
            message="Backpack returns are slightly above the demo account baseline.",
            recommendation="Review return reasons and product listing expectations.",
            resolved_at=TODAY - timedelta(days=1),
        ),
    ])

    session.commit()

    print("\nCommerceIQ demo data seeded successfully.")
    print(f"Business ID: {business.id}")
    print(f"Owner login:   {owner.email} / {PASSWORD}")
    print(f"Analyst login: {analyst.email} / {PASSWORD}")
    print("\nRecord counts:")
    for model in [
        Business, User, Product, Channel, ChannelProduct, Order, OrderLine,
        Return, InventorySnapshot, ImportBatch, ImportRawRow,
        DailyProductMetric, DailyChannelMetric, ProductInsight,
    ]:
        print(f"  {model.__tablename__:24} {session.query(model).count()}")


def main():
    parser = argparse.ArgumentParser(description="Seed CommerceIQ demo data")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing rows before seeding",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        if args.reset:
            reset_database(session)
        seed(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
