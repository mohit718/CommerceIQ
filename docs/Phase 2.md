## one generic parser + a per-channel config dict:
CHANNEL_CONFIGS = {
    "amazon": {
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
        "date_format": "%Y-%m-%dT%H:%M:%SZ",
    },
    "flipkart": {
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
    # shopify, offline similarly — shopify has no fee column,
    # so fee_amount is computed (2% of gross) rather than mapped
}


## Normalisation
Dates: parsed per-channel using the configured format, falling back to dateutil if it doesn't match (real CSVs are messy)
Currency: strip ₹, ,, whitespace → Decimal
SKU: trimmed + uppercased for matching purposes only — we still store the original external_sku as given, matching happens on a normalized copy

## Product mapping resolution (per row)
Look up channel_products by (channel_id, external_sku)
  → found: use that product_id
  → not found: check if a Product with matching sku already exists
      → yes: auto-create the channel_products mapping (mapping_method='exact')
      → no: skip this row, mark unprocessed with "unmapped" error,
             leave it queryable for manual mapping

## Deduplication
Re-uploading the same file (a seller does this more than you'd think) shouldn't create duplicate data:

Table	Uniqueness rule	Behavior on conflict
orders	(business_id, channel_id, external_order_id)	Reuse existing Order, don't recreate
order_lines	app-level check: no duplicate line for same order + same channel_product_id	Skip the row
inventory_snapshots	(business_id, product_id, channel_id, snapshot_date)	Upsert — overwrite quantity_on_hand (sellers often re-upload corrected inventory for the same date)