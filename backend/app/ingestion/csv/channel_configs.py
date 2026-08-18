"""
Per-channel CSV column configuration. This is the ONLY place that knows a
particular channel calls the order-id column 'order-id' vs 'Order Id' — the
rest of the ingestion pipeline only ever deals with our canonical field
names (external_order_id, external_sku, order_date, quantity, gross_amount,
discount_amount, fee_amount, shipping_amount, tax_amount, return_date,
reason, refund_amount).

Column names here are mocked/representative, not pulled from real
marketplace export docs — swap them for the real ones once real
integrations start (Section 32 of the master prompt).
"""

CHANNEL_CONFIGS: dict[str, dict] = {
    "amazon": {
        "sales": {
            "column_map": {
                "external_order_id": "order-id",
                "external_sku": "sku",
                "order_date": "purchase-date",
                "quantity": "quantity",
                "gross_amount": "item-price",
                "discount_amount": "item-promotion-discount",
                "fee_amount": "commission",
                "shipping_amount": "shipping-price",
                "tax_amount": "item-tax",
            },
            "date_format": "%Y-%m-%d",
        },
        "inventory": {
            "column_map": {
                "external_sku": "sku",
                "snapshot_date": "date",
                "quantity_on_hand": "quantity-available",
            },
            "date_format": "%Y-%m-%d",
        },
        "returns": {
            "column_map": {
                "external_order_id": "order-id",
                "external_sku": "sku",
                "quantity": "quantity-returned",
                "return_date": "return-date",
                "reason": "return-reason",
                "refund_amount": "refund-amount",
            },
            "date_format": "%Y-%m-%d",
        },
    },
    "flipkart": {
        "sales": {
            "column_map": {
                "external_order_id": "Order Id",
                "external_sku": "SKU",
                "order_date": "Order Date",
                "quantity": "Quantity",
                "gross_amount": "Selling Price",
                "discount_amount": "Discount",
                "fee_amount": "Marketplace Fee",
                "shipping_amount": "Shipping Charge",
                "tax_amount": "Taxes",
            },
            "date_format": "%d-%m-%Y",
        },
        "inventory": {
            "column_map": {
                "external_sku": "SKU",
                "snapshot_date": "Snapshot Date",
                "quantity_on_hand": "Available Qty",
            },
            "date_format": "%d-%m-%Y",
        },
        "returns": {
            "column_map": {
                "external_order_id": "Order Id",
                "external_sku": "SKU",
                "quantity": "Return Quantity",
                "return_date": "Return Date",
                "reason": "Return Reason",
                "refund_amount": "Refund Amount",
            },
            "date_format": "%d-%m-%Y",
        },
    },
    "shopify": {
        "sales": {
            "column_map": {
                "external_order_id": "Name",
                "external_sku": "Lineitem sku",
                "order_date": "Created at",
                "quantity": "Lineitem quantity",
                "gross_amount": "Lineitem price",
                "discount_amount": "Discount Amount",
                # Shopify export has no fee column — we compute it below.
                "shipping_amount": "Shipping",
                "tax_amount": "Taxes",
            },
            "date_format": "%Y-%m-%d %H:%M:%S %z",
            # No 'fee_amount' mapping -> fall back to a flat pct of gross.
            "fee_pct_of_gross": 0.02,
        },
        "inventory": {
            "column_map": {
                "external_sku": "SKU",
                "snapshot_date": "Snapshot Date",
                "quantity_on_hand": "Available",
            },
            "date_format": "%Y-%m-%d",
        },
        "returns": {
            "column_map": {
                "external_order_id": "Name",
                "external_sku": "Lineitem sku",
                "quantity": "Return Quantity",
                "return_date": "Return Date",
                "reason": "Return Reason",
                "refund_amount": "Refund Amount",
            },
            "date_format": "%Y-%m-%d",
        },
    },
    "offline": {
        "sales": {
            "column_map": {
                "external_order_id": "Bill No",
                "external_sku": "Item Code",
                "order_date": "Bill Date",
                "quantity": "Qty",
                "gross_amount": "Amount",
                "discount_amount": "Discount",
                # No marketplace fee, shipping, or separate tax line for
                # offline/POS sales.
            },
            "date_format": "%d/%m/%Y",
        },
        "inventory": {
            "column_map": {
                "external_sku": "Item Code",
                "snapshot_date": "Stock Date",
                "quantity_on_hand": "Stock Qty",
            },
            "date_format": "%d/%m/%Y",
        },
        "returns": {
            "column_map": {
                "external_order_id": "Bill No",
                "external_sku": "Item Code",
                "quantity": "Return Qty",
                "return_date": "Return Date",
                "reason": "Reason",
                "refund_amount": "Refund Amount",
            },
            "date_format": "%d/%m/%Y",
        },
    },
}

# Fallback used when a channel name doesn't match a known config key —
# keeps ingestion working (rather than hard-failing) for a channel we
# haven't added mock config for yet.
GENERIC_FALLBACK_CONFIG = {
    "sales": {
        "column_map": {
            "external_order_id": "order_id",
            "external_sku": "sku",
            "order_date": "order_date",
            "quantity": "quantity",
            "gross_amount": "gross_amount",
            "discount_amount": "discount_amount",
            "fee_amount": "fee_amount",
            "shipping_amount": "shipping_amount",
            "tax_amount": "tax_amount",
        },
        "date_format": "%Y-%m-%d",
    },
    "inventory": {
        "column_map": {
            "external_sku": "sku",
            "snapshot_date": "snapshot_date",
            "quantity_on_hand": "quantity_on_hand",
        },
        "date_format": "%Y-%m-%d",
    },
    "returns": {
        "column_map": {
            "external_order_id": "order_id",
            "external_sku": "sku",
            "quantity": "quantity",
            "return_date": "return_date",
            "reason": "reason",
            "refund_amount": "refund_amount",
        },
        "date_format": "%Y-%m-%d",
    },
}

# Products CSV isn't channel-format-specific — it's the seller's own master
# catalog upload, so it gets one shared config regardless of which channel
# the import_batch.channel_id points at (that channel becomes where the
# resulting channel_products mapping is created).
PRODUCT_IMPORT_CONFIG = {
    "column_map": {
        "sku": "Master SKU",
        "external_sku": "Channel SKU",
        "name": "Product Name",
        "brand": "Brand",
        "category": "Category",
        "cost_price": "Cost Price",
        "selling_price": "Selling Price",
    }
}


def resolve_channel_config(channel_name: str, import_type: str) -> dict:
    """Maps a Channel.name (e.g. 'Amazon', 'Offline Store') to its CSV
    config. Falls back to a generic layout for unrecognized channel names
    rather than hard-failing, so ingestion still works for a channel we
    haven't added a mock format for."""
    key = channel_name.strip().lower()
    for known_key in CHANNEL_CONFIGS:
        if key.startswith(known_key):
            return CHANNEL_CONFIGS[known_key][import_type]
    return GENERIC_FALLBACK_CONFIG[import_type]
