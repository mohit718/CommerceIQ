# 19. Project Phases & Milestones

# Phase 0 — Requirements & Product Design
> Deadline: 17 August
### Objective

Define the business and technical requirements.

### Tasks

- Identify target customer
- Define business problems
- Define core KPIs
- Define data sources
- Define data model
- Define dashboard structure
- Define insight types
- Define MVP scope

### Milestone

**M0 — Product Specification Complete**

Deliverables:

- Product requirements document
- Architecture diagram
- Database schema
- User flows
- MVP feature list

---

# Phase 1 — Data Foundation
> Deadline: 17 August

### Objective

Build the unified commerce data model.

### Tasks

- Set up repository
- Set up backend
- Set up PostgreSQL
- Create database schema
- Create business/account model
- Create product model
- Create channel model
- Create order model
- Create inventory model
- Create returns model
- Create CSV import pipeline

### Milestone

**M1 — Unified Commerce Database**

Input:

```text
CSV
```

Output:

```text
Normalized PostgreSQL data
```

---

# Phase 2 — Data Normalization
> Deadline: 18 August

### Objective

Convert different channel data formats into a common structure.

### Tasks

- Build CSV parsers
- Create channel-specific mappings
- Normalize dates
- Normalize currencies
- Normalize product names
- Normalize SKUs
- Map marketplace SKUs to master SKUs
- Handle duplicate records
- Handle missing values
- Build validation system

### Milestone

**M2 — Multi-Channel Data Successfully Unified**

Example:

```text
Amazon SKU ─────┐
Flipkart SKU ───┼──> Master Product
Offline SKU ────┘
```

---

# Phase 3 — Analytics Engine
> Deadline: 19 August

### Objective

Create the core business metrics.

### Tasks

Implement:

- Revenue calculations
- Net sales
- Orders
- Units sold
- Average order value
- Growth rate
- Product performance
- Channel performance
- Profit
- Contribution margin
- Return rate
- Discount analysis

### Milestone

**M3 — Analytics Engine Complete**

All major KPIs can be calculated from normalized data.

---

# Phase 4 — Inventory Intelligence
> Deadline: 20 August

### Objective

Turn inventory data into actionable information.

### Tasks

- Inventory aging
- Inventory valuation
- Inventory turnover
- Sales velocity
- Days of inventory remaining
- Stockout prediction
- Overstock detection
- Slow-moving product detection
- Reorder recommendations

### Milestone

**M4 — Inventory Intelligence Complete**

Example:

> Product X → Stockout risk in 8 days.

> Product Y → ₹2.3L inventory inactive for 70 days.

---

# Phase 5 — Insight Engine
> Deadline: 21 August

### Objective

Automatically discover business opportunities and problems.

### Insight categories

```text
Sales
Inventory
Products
Channels
Profitability
Growth
Seasonality
Forecasting
```

### Tasks

- Define insight rules
- Create severity levels
- Create confidence scores
- Create recommendation engine
- Create insight storage
- Create insight API

### Milestone

**M5 — Automated Business Insights**

Example:

```text
INSIGHT
--------
Type: Demand Spike
SKU: ABC123
Growth: +74%
Confidence: 91%
Action: Increase inventory
```

---

# Phase 6 — Dashboard
> Deadline: 26 August

### Objective

Create the business-facing application.

### Screens

### 1. Executive Dashboard

```text
Revenue
Orders
Profit
Growth
Inventory
Alerts
```

### 2. Sales Analytics

- Revenue trends
- Channel comparison
- Category analysis
- Product performance

### 3. Product Analytics

- Top products
- Declining products
- Growth products
- Product profitability

### 4. Inventory

- Inventory value
- Aging
- Stockout risks
- Overstock
- Reorder recommendations

### 5. Insights

- Critical alerts
- Recommendations
- Trends
- Opportunities

### 6. Reports

- Weekly
- Monthly
- Custom date range

### Milestone

**M6 — Functional Business Dashboard**

---

# Phase 7 — Reporting & Notifications
Deadline: 27 August

### Objective

Make the platform proactive.

### Tasks

- Weekly report generation
- Monthly report generation
- Email notifications
- WhatsApp integration
- Alert preferences
- Notification scheduling
- Notification history

### Milestone

**M7 — Automated Business Reporting**

The system can automatically send:

```text
Weekly Report
Monthly Report
Critical Alerts
Stockout Alerts
Demand Alerts
Inventory Alerts
```

---

# Phase 8 — Forecasting
Deadline: 28 August

### Objective

Move from descriptive analytics to predictive analytics.

### Tasks

- Sales trend analysis
- Moving average
- Seasonal analysis
- Demand forecasting
- SKU-level prediction
- Forecast confidence
- Inventory planning

### Milestone

**M8 — Demand Forecasting Engine**

Example:

> Product X expected demand next month: 1,240–1,380 units.

---

# Phase 9 — AI Business Analyst
Deadline: 31 August

### Objective

Allow users to interact with their business data naturally.

### Tasks

- Natural-language query interface
- Metric-aware AI
- Insight explanation
- Report generation
- Business Q&A
- Recommendation explanations

### Milestone

**M9 — AI Commerce Analyst**

Example:

**User:**

> Why did my profit decrease this month?

**System:**

> Profit decreased 8.4%. The primary contributors were a 6% increase in marketplace fees, a 4% increase in returns and higher average discounts on three major SKUs.

---

# 20. Milestone Summary

| Milestone | Deliverable                  |
| --------- | ---------------------------- |
| M0        | Product specification        |
| M1        | Unified commerce database    |
| M2        | Multi-channel normalization  |
| M3        | Analytics engine             |
| M4        | Inventory intelligence       |
| M5        | Insight engine               |
| M6        | Business dashboard           |
| M7        | Automated reports & WhatsApp |
| M8        | Forecasting                  |
| M9        | AI Commerce Analyst          |

---