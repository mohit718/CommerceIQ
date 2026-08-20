# Phase 4 — Inventory Intelligence

**Milestone Target:** M4 — Inventory Intelligence Complete

> Same approach as before: design and key decisions first. This phase has one important architectural choice that's actually the opposite of what we did in Phase 3, so the reasoning is explained clearly before building.

---

## 1. What This Phase Builds

Turns raw `inventory_snapshots` + the `daily_product_metrics` already materialized in **Phase 3** into actionable, per-product inventory intelligence.

```text
inventory_snapshots (latest stock) ─┐
                                    ├──► Read-time computation ──► GET /inventory/status
daily_product_metrics (sales velocity) ┘                          GET /inventory/stockout-risks
                                                                   GET /inventory/slow-moving
                                                                   GET /inventory/overview
```

### Use Cases

Covers the **Section 8** use cases:

- Stockout prediction
- Slow-moving detection
- Overstock detection
- Inventory valuation
- First-pass reorder recommendation

---

## 2. Key Decision: Read-Time Computation, NOT a New Materialized Table

This is the opposite of Phase 3's approach, and the reasoning is important.

| | Phase 3 — Sales Analytics | Phase 4 — Inventory Intelligence |
|---|---|---|
| **Source data volume** | `order_lines` — potentially huge, grows with every sale | `inventory_snapshots` — periodic (weekly/daily per product), much smaller |
| **Query parameters** | Fixed metric definitions such as revenue and profit | Thresholds vary per read — stockout window, slow-moving days, and overstock days are naturally query parameters |
| **Why materialize (or not)** | Avoids re-scanning huge `order_lines` on every dashboard load | Materializing would mean recomputing per threshold combination — wasteful because thresholds can change interactively |

### Why This Works

Phase 4 computes live, but relies entirely on:

1. **Already-materialized `daily_product_metrics`** for the sales-velocity side.
   - Cheap: it only needs to sum a date-indexed range.
2. A lightweight **latest snapshot per product/channel** query for the stock side.

There is:

- No new table
- No new recompute job

This keeps the phase small: it is a **read-side intelligence layer** over two datasets that already exist.

### What This Sets Up for Phase 5

Phase 5's insight engine is where:

> "This SKU crossed the stockout threshold"

gets persisted as a `product_insights` row with fixed severity, confidence, and timestamp.

This is a deliberate separation:

- **Phase 4:** "What is the state right now, with whatever thresholds you ask for?"
- **Phase 5:** "What changed, and should someone be alerted?"

---

## 3. Formulas

For a given `(product_id, channel_id | None)` as of a date:

### Average Daily Sales Velocity

```text
avg_daily_sales_velocity =
    SUM(daily_product_metrics.units_sold)
    over trailing window (default 30 days)
    ÷ window_days
```

### Latest Stock

```text
latest_stock =
    quantity_on_hand from the most recent
    inventory_snapshot ≤ as_of_date
```

### Coverage Days

```text
coverage_days =
    latest_stock ÷ avg_daily_sales_velocity

None if velocity = 0
("no recent sales, can't estimate")
```

### Days Since Last Sale

```text
days_since_last_sale =
    as_of_date − most recent date with units_sold > 0

None if never sold
```

### Inventory Value

```text
inventory_value =
    latest_stock × product.cost_price
```

---

## 3.1 Status Classification

**Priority order — first match wins:**

| Condition | Status |
|---|---|
| `latest_stock == 0` | `out_of_stock` |
| `coverage_days ≤ stockout_threshold_days` (default: 14) | `stockout_risk` |
| `days_since_last_sale ≥ slow_moving_threshold` (default: 60) and `stock > 0` | `slow_moving` |
| `coverage_days ≥ overstock_threshold_days` (default: 90) | `overstock` |
| Otherwise | `healthy` |

---

## 3.2 Reorder Recommendation

The reorder recommendation is **only computed when**:

```text
status == stockout_risk
```

Formula:

```text
reorder_qty =
    max(
        0,
        ceil(
            avg_daily_sales_velocity × target_coverage_days
            − latest_stock
        )
    )
```

**Default:** `target_coverage_days = 30`

---

## 3.3 Known Simplification: Inventory Aging

Without a stock-receipt or purchase-order ledger, we cannot track true FIFO lot aging, such as:

> "This specific 200 units arrived 45 days ago."

Instead, `days_since_last_sale` is used as the **aging proxy**.

It answers:

> "How stale is this SKU?"

It does **not** answer:

> "How old is this specific batch?"

A real lot-aging feature would require a new receiving-event table. This is intentionally flagged as a known simplification rather than being built now (**Principle 7**).

---

## 3.4 Channel Handling

`inventory_snapshots.channel_id` is nullable.

Some sellers track stock per channel, for example:

- Amazon FBA
- Offline store

Others track one pooled inventory number.

The calculation behaves as follows:

- **When `channel_id` is set:** velocity uses that channel's own `daily_product_metrics`.
- **When `channel_id` is `NULL`:** velocity sums across all channels for that product.

---

## 4. API Endpoints

### `GET /api/v1/inventory/status`

**Query parameters:**

```text
?channel_id=
&velocity_window_days=30
&stockout_threshold_days=14
&slow_moving_threshold_days=60
&overstock_threshold_days=90
&as_of_date=
```

**Returns:** Full per-product(-channel) row containing:

- `stock`
- `velocity`
- `coverage_days`
- `days_since_last_sale`
- `inventory_value`
- `status`
- `reorder_qty`

---

### `GET /api/v1/inventory/stockout-risks`

Returns the same computation filtered to:

```text
status ∈ {out_of_stock, stockout_risk}
```

Sorted by:

```text
coverage_days ASC
```

**Most urgent items appear first.**

---

### `GET /api/v1/inventory/slow-moving`

Returns the same computation filtered to:

```text
status == slow_moving
```

Sorted by:

```text
inventory_value DESC
```

**Biggest locked capital appears first.**

---

### `GET /api/v1/inventory/overview`

Returns aggregate KPIs:

- Total inventory value
- Count at stockout risk
- Count of slow-moving products
- Total capital locked in slow-moving stock

These KPIs feed the **Inventory screen's summary cards**.

---

### Shared Computation

All four endpoints share the same underlying computation function.

The endpoints are simply different **filters and sorts** over one intelligence query, matching how **Section 6's dashboard** groups these views.

---

## 5. File Structure

```text
app/modules/inventory/
├── router.py     # Extends the existing inventory module — adds the 4 endpoints above
└── service.py    # NEW — intelligence computation:
                  # latest-stock query,
                  # velocity query,
                  # status classification,
                  # reorder calculation
```

### Existing File

`app/modules/inventory/router.py` already exists and currently contains only:

```text
GET /snapshots
```

This phase **extends** the existing router rather than replacing it.


Refined aging logic
if a sale has ever happened:
    aging_days = as_of_date − most recent date with units_sold > 0
    aging_basis = "last_sale"
elif the product has at least one inventory snapshot:
    aging_days = as_of_date − earliest inventory_snapshot.snapshot_date  (first time we saw it in stock)
    aging_basis = "first_seen"
else:
    aging_days = None
    aging_basis = "unknown"   (no sales AND no snapshot — genuinely no data to age against)

Both aging_days and aging_basis are returned in the API response — never silently mixed, so a dashboard (or you, reading the JSON) always knows which measurement grounded the number. A brand-new product that arrived yesterday with aging_days = 1 and aging_basis = "first_seen" correctly stays healthy; the same product sitting untouched for 90 days correctly flips to slow_moving on the same basis.


Endpoints added:

GET /api/v1/inventory/status          # full per-product intelligence row
GET /api/v1/inventory/stockout-risks  # filtered + sorted by urgency
GET /api/v1/inventory/slow-moving     # filtered + sorted by locked capital
GET /api/v1/inventory/overview        # aggregate KPIs for the Inventory screen