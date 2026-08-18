# Models & Database Tables

> Structured reference for the commerce analytics data model.

## Data Model at a Glance

1. **Tenant & access** → `businesses`, `users`
2. **Product & channel normalization** → `products`, `channels`, `channel_products`
3. **Sales facts** → `orders`, `order_lines`, `returns`
4. **Inventory history** → `inventory_snapshots`
5. **Data ingestion** → `import_batches`, `import_raw_rows`
6. **Analytics layer** → `daily_product_metrics`, `daily_channel_metrics`
7. **Business intelligence** → `product_insights`

---

## 1. businesses

The tenant. Everything else hangs off this.

### Sample Data

| id | name | created_at |
| --- | --- | --- |
| 1 | Boat Lifestyle Retail | 2026-01-05 |

### Why It Exists

**Why it exists:** This is the root of multi-tenancy. Every other table carries a `business_id` that traces back here. Without it, there's no way to guarantee Seller A never sees Seller B's data — it's the anchor for the isolation guarantee in Principle 6.

---

## 2. users

The people who log in.

### Sample Data

| id | business_id | email | role | created_at |
| --- | --- | --- | --- | --- |
| 1 | 1 | owner@boatlifestyle.com | owner | 2026-01-05 |
| 2 | 1 | analyst@boatlifestyle.com | analyst | 2026-01-06 |

### Why It Exists

**Why it exists:** Authentication + authorization. `business_id` here means a login token can carry "this user belongs to business 1" — that claim is what `shared/tenancy.py` checks on every request to scope queries correctly. `role` is intentionally minimal (`owner`/`analyst`) rather than a full permissions system, because Release 1 doesn't need granular RBAC yet.

---

## 3. products (Master Catalog)

The single canonical version of a physical product — this is the "Airdopes 141" in your Section 18 diagram, not any channel's specific listing.

### Sample Data

| id | business_id | sku | name | brand | category | cost_price | selling_price |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 101 | 1 | MASTER-AD141-BLK | Airdopes 141 Black | boAt | Audio | 450 | 1299 |

### Why It Exists

**Why it exists:** This is what all analytics actually run against. When you ask "how is Product A performing across channels," it's this row — not any channel's SKU — that the query groups by. Without a master catalog, Amazon, Flipkart, and Offline listings would look like unrelated products, making cross-channel comparison impossible.

---

## 4. channels

The sales channels this business sells through.

### Sample Data

| id | business_id | name | type |
| --- | --- | --- | --- |
| 1 | 1 | Amazon | marketplace |
| 2 | 1 | Flipkart | marketplace |
| 3 | 1 | Shopify | d2c |
| 4 | 1 | Offline Store | offline |

### Why It Exists

**Why it exists:** Every order, inventory snapshot, and metric needs to be attributed to a channel for channel-performance comparisons. `type` also lets future connector logic branch between marketplace, D2C, and offline sources without a schema change.

---

## 5. channel_products (The SKU Mapping Table)

The translation layer that solves the "same product, different SKU" problem.

### Sample Data

| id | business_id | product_id | channel_id | external_product_id | external_sku | external_name | mapping_method |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 101 | 1 (Amazon) | B08XYZ123 | — | Boat Airdopes 141 Black | exact |
| 2 | 1 | 101 | 2 (Flipkart) | FLPKT9981 | SKU123 | boAt Airdopes 141 TWS - Black | manual |
| 3 | 1 | 101 | 4 (Offline) | — | AD141-BLK | AD141-BLK | exact |

### Why It Exists

**Why it exists:** When a sales CSV contains `external_sku = B08XYZ123`, ingestion resolves it to `product_id = 101`. Downstream tables can then work with clean master `product_id`s instead of knowing every channel's naming quirks. `mapping_method` records whether the mapping was exact or manually confirmed.

---

## 6. orders

The order header — one row per actual customer order.

### Sample Data

| id | business_id | channel_id | external_order_id | order_date |
| --- | --- | --- | --- | --- |
| 5001 | 1 | 1 (Amazon) | 402-1234567-9876543 | 2026-06-14 |

### Why It Exists

**Why it exists:** Some metrics are order-level, not line-level. Average Order Value uses distinct orders, so an order containing three products must still count as one order.

---

## 7. order_lines (The Core Sales Fact Table)

One row per product within an order — the atomic sales fact used for most revenue and profit calculations.

### Sample Data

| id | business_id | order_id | product_id | channel_product_id | quantity | gross_amount | discount_amount | fee_amount | shipping_amount | tax_amount |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9001 | 1 | 5001 | 101 | 1 | 2 | 2598 | 260 | 180 | 0 | 130 |

### Why It Exists

**Why it exists:** This is the atomic unit analytics runs on.

- **Net Revenue** = `2598 − 260 = 2338`
- **Realized Price (per unit)** = `2338 / 2 = 1169`
- **Contribution Profit** = `2598 − 260 − 180 − 0 − (2 × 450) = 1258`

`channel_product_id` is retained alongside `product_id` so historical order lines can still be traced back to the original channel SKU if a mapping is corrected later.

---

## 8. returns

A returned quantity from an order line.

### Sample Data

| id | business_id | order_line_id | quantity | return_date | reason | refund_amount |
| --- | --- | --- | --- | --- | --- | --- |
| 200 | 1 | 9001 | 1 | 2026-06-20 | Defective | 1299 |

### Why It Exists

**Why it exists:** Needed for Return Rate and for correcting Contribution Profit. Linking to `order_line_id` preserves the exact sale, channel, and price context behind the return.

---

## 9. inventory_snapshots

A dated record of stock level — not a single mutable current-stock field.

### Sample Data

| id | business_id | product_id | channel_id | snapshot_date | quantity_on_hand |
| --- | --- | --- | --- | --- | --- |
| 3001 | 1 | 101 | 1 (Amazon FBA) | 2026-06-01 | 480 |
| 3002 | 1 | 101 | 1 (Amazon FBA) | 2026-06-08 | 350 |
| 3003 | 1 | 101 | 1 (Amazon FBA) | 2026-06-15 | 210 |

### Why It Exists

**Why it exists:** Stockout prediction needs sales velocity and stock history over time. Each snapshot is a point-in-time fact; the analytics layer computes velocity and coverage from the sequence.

---

## 10. import_batches

One row per file upload.

### Sample Data

| id | business_id | channel_id | file_name | import_type | status | row_count | error_count |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 77 | 1 | 1 (Amazon) | amazon_sales_june.csv | sales | completed | 4200 | 12 |

### Why It Exists

**Why it exists:** Tracks the health and history of each ingestion run and provides an entry point for re-processing raw rows without asking the seller to re-upload the file.

---

## 11. import_raw_rows

The untouched original data, one row per CSV row.

### Sample Data

| id | import_batch_id | raw_data | processed | error_message |
| --- | --- | --- | --- | --- |
| 50001 | 77 | {"Order ID": "402-...", "SKU": "B08XYZ123", "Qty": "2", "Price": "1299.00", ...} | true | null |
| 50002 | 77 | {"Order ID": "402-...", "SKU": "", "Qty": "abc", ...} | false | Invalid quantity value |

### Why It Exists

**Why it exists:** The raw/source layer preserves the original input exactly as received. This allows failed rows to be replayed after fixing normalization logic and makes seller disputes traceable to the original CSV row.

---

## 12. daily_product_metrics (Analytics/Aggregate Layer)

Pre-computed, one row per product per channel per day. This is a derived cache, not the source of truth.

### Sample Data

| business_id | product_id | channel_id | date | units_sold | gross_revenue | net_revenue | discount_amount | fee_amount | returns_amount | contribution_profit | realized_price |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 101 | 1 | 2026-06-14 | 2 | 2598 | 2338 | 260 | 180 | 0 | 1258 | 1169 |

### Why It Exists

**Why it exists:** Instead of recomputing revenue and profit from raw `order_lines` for every dashboard load, the system reads this materialized daily summary for fast queries and trend analysis.

---

## 13. daily_channel_metrics

The same aggregate concept as `daily_product_metrics`, but at channel level.

### Sample Data

| business_id | channel_id | date | gross_revenue | net_revenue | orders | units | contribution_profit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 (Amazon) | 2026-06-14 | 45000 | 41200 | 28 | 34 | 19800 |

### Why It Exists

**Why it exists:** Powers cross-channel analysis such as comparing growth, revenue, orders, units, and margins without scanning every order line on each dashboard request.

---

## 14. product_insights

Where the rule engine (Phase 5) writes its output.

### Sample Data

| id | business_id | product_id | type | severity | confidence | message | recommendation | created_at | resolved_at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 500 | 1 | 101 | STOCKOUT_RISK | high | 0.91 | Product likely to run out in 7 days | Reorder ~450 units | 2026-06-15 | null |

### Why It Exists

**Why it exists:** This is the structured artifact consumed by the AI layer. The LLM turns the structured insight into human-readable language but does not calculate confidence or severity. `resolved_at` prevents repeatedly alerting on an issue that is no longer relevant.

---
