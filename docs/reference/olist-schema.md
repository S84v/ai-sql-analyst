I've researched the Olist dataset extensively, combining documented facts, observed characteristics from your exploration, and community-validated schema patterns. Below is the complete schema design specification, optimized for an AI SQL agent operating via LangGraph.

---

## A. Dataset Findings Relevant to Schema Design

| Finding | Source | Implication |
|---|---|---|
| `customer_id` is generated per order; `customer_unique_id` persists across orders for the same real person | GitHub data dictionaries, RFM project docs  | Any customer count, repeat-purchase, or CLTV metric **must** use `customer_unique_id`. Using `customer_id` silently treats every order as a new customer. |
| `review_id` is **not unique**. `(review_id, order_id)` was unique in your exploration; 547 orders have >1 review | Your observation; community reports confirm duplicate `review_id` values  | `review_id` alone cannot be a primary key. Composite PK `(review_id, order_id)` is required, or a surrogate key. One order can have multiple review events. |
| `(order_id, order_item_id)` is the natural composite PK for order items | Data dictionaries  | `order_item_id` is a sequence number *within* an order, not globally unique. |
| `(order_id, payment_sequential)` is the natural composite PK for payments | Community documentation  | An order can have multiple payment rows (split payments, multiple methods). Summing `payment_value` without grouping by `order_id` double-counts. |
| Geolocation grain is **zip-code-prefix + lat/lng coordinates**. A single ZIP prefix can have many rows because multiple coordinate points map to it | Stack Overflow discussions, community notebooks  | Joining `customers` or `sellers` directly to raw `geolocation` multiplies rows. Must use a deduplicated/aggregated view. |
| Product category translation has 71 entries vs. 73 categories in products | Your observation; community reports missing values  | Two categories have no English translation. Schema must handle these gracefully (LEFT JOIN, COALESCE). |
| Timestamps are stored as naive timestamps (no timezone) | Data dictionaries; observed from CSV samples  | Stored as `timestamp` (without time zone). Brazil spans multiple timezones; the dataset does not document which one. Derived durations (delivery days) should be calculated dynamically, not stored. |
| Order status values: `delivered`, `shipped`, `canceled`, `processing`, `invoiced`, `approved`, `unavailable`, `created` | Kaggle documentation | Prefer `text` + `CHECK` constraint over ENUM for robustness and agent-friendliness. |
| Payment types: `credit_card`, `boleto`, `voucher`, `debit_card` | Data dictionaries  | Text + CHECK constraint. |
| Review scores are integers 1–5 | Data dictionaries  | `smallint` with CHECK (1–5). |
| `olist_order_items` contains `shipping_limit_date` | Community schema  | This is the seller's shipping deadline, distinct from actual delivery dates in orders. |

---

## B. Recommended Logical Model

The model preserves the source tables as **raw/landing tables** and adds a thin **agent-facing semantic layer** (views + comments). The logical entities are:

```
┌──────────────┐     ┌──────────────┐     ┌─────────────────┐
│  CUSTOMERS   │     │   SELLERS    │     │   PRODUCTS      │
│──────────────│     │──────────────│     │─────────────────│
│ customer_id  │     │ seller_id    │     │ product_id      │
│ customer_    │     │ seller_zip_  │     │ product_category│
│  unique_id   │     │  code_prefix │     │  _name          │
│ customer_zip_│     │ seller_city  │     │ product_weight_g│
│  code_prefix │     │ seller_state │     │ ... dimensions  │
│ customer_city│     └──────┬───────┘     └────────┬────────┘
│ customer_    │            │                      │
│  state       │            │                      │
└──────┬───────┘            │                      │
       │                    │                      │
       │ 1                  │                      │
       │                    │                      │
       ▼ *                  │                      │
┌──────────────┐            │                      │
│   ORDERS     │            │                      │
│──────────────│            │                      │
│ order_id (PK)│            │                      │
│ customer_id  │──┐         │                      │
│ order_status │  │         │                      │
│ purchase_ts  │  │         │                      │
│ approved_at  │  │         │                      │
│ carrier_date │  │         │                      │
│ delivered_dt │  │         │                      │
│ estimated_dt │  │         │                      │
└──────┬───────┘  │         │                      │
       │ 1        │         │                      │
       │          │         │                      │
       ▼ *        │         │                      │
┌──────────────┐  │         │                      │
│ ORDER_ITEMS  │  │         │                      │
│──────────────│  │         │                      │
│ order_id (PK)│  │         │                      │
│ order_item_id│  │         │                      │
│ product_id   │──┼─────────┼──────────────────────┘
│ seller_id    │──┼─────────┘
│ price        │  │
│ freight_value│  │
└──────────────┘  │
                  │ 1
                  ▼ *
┌──────────────────┐     ┌──────────────────────┐
│  ORDER_PAYMENTS  │     │   ORDER_REVIEWS      │
│──────────────────│     │──────────────────────│
│ order_id (PK)    │     │ review_id (PK)       │
│ payment_sequential│    │ order_id (PK)        │
│ payment_type     │     │ review_score         │
│ payment_installments│  │ review_comment_title │
│ payment_value    │     │ review_comment_message│
└──────────────────┘     │ review_creation_date │
                         │ review_answer_timestamp│
                         └──────────────────────┘

┌──────────────────────────┐     ┌──────────────────────┐
│ PRODUCT_CATEGORY_        │     │   GEOLOCATION        │
│ TRANSLATION              │     │──────────────────────│
│──────────────────────────│     │ zip_code_prefix      │
│ product_category_name(PK)│     │ lat, lng             │
│ product_category_name_   │     │ city, state          │
│  english                 │     └──────────────────────┘
└──────────────────────────┘            (deduplicated view)
```

**Grain summary:**

| Table | Grain |
|---|---|
| `customers` | One row per `customer_id` (per-order transient identity) |
| `orders` | One row per `order_id` |
| `order_items` | One row per (`order_id`, `order_item_id`) — an order with 3 products yields 3 rows |
| `order_payments` | One row per (`order_id`, `payment_sequential`) — split payments yield multiple rows |
| `order_reviews` | One row per (`review_id`, `order_id`) — an order can have multiple review events |
| `products` | One row per `product_id` |
| `sellers` | One row per `seller_id` |
| `geolocation` (raw) | One row per (`zip_code_prefix`, lat, lng) — many rows per ZIP prefix |
| `geolocation` (view) | One row per `zip_code_prefix` (averaged coordinates) |
| `product_category_translation` | One row per Portuguese category name |

---

## C. Recommended PostgreSQL Tables

I recommend a **two-schema architecture**: `raw` for source-faithful landing tables and `analytics` for agent-facing views. For simplicity in a portfolio project, you can use a single `public` schema with prefixed table names (`olist_*`) and clearly named views.

### C.1 `customers`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `customer_id` | `text` | No | Yes | — | Transient ID generated per order | 32-char hex string |
| `customer_unique_id` | `text` | No | No | — | Persistent human buyer ID | Use this for customer counts |
| `customer_zip_code_prefix` | `integer` | Yes | No | — | First 5 digits of Brazilian ZIP | Joins to geolocation on this |
| `customer_city` | `text` | Yes | No | — | City name (lowercase, unaccented) | |
| `customer_state` | `char(2)` | Yes | No | — | Brazilian state abbreviation | 27 states |

### C.2 `orders`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `order_id` | `text` | No | Yes | — | Unique order identifier | 32-char hex |
| `customer_id` | `text` | No | No | → customers | Links to customer | |
| `order_status` | `text` | No | No | — | Current order status | CHECK constraint below |
| `order_purchase_timestamp` | `timestamp` | No | No | — | When order was placed | Naive timestamp |
| `order_approved_at` | `timestamp` | Yes | No | — | Payment approval timestamp | Nullable |
| `order_delivered_carrier_date` | `timestamp` | Yes | No | — | Handed to carrier | Nullable |
| `order_delivered_customer_date` | `timestamp` | Yes | No | — | Actual delivery | Nullable |
| `order_estimated_delivery_date` | `timestamp` | Yes | No | — | Promised delivery | Nullable |

### C.3 `order_items`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `order_id` | `text` | No | Yes | → orders | Order identifier | Composite PK part |
| `order_item_id` | `integer` | No | Yes | — | Sequence within order | Composite PK part |
| `product_id` | `text` | No | No | → products | Product purchased | |
| `seller_id` | `text` | No | No | → sellers | Seller fulfilling | |
| `shipping_limit_date` | `timestamp` | Yes | No | — | Seller shipping deadline | |
| `price` | `numeric(12,2)` | No | No | — | Item price (BRL) | Excludes freight |
| `freight_value` | `numeric(12,2)` | No | No | — | Shipping cost (BRL) | |

### C.4 `order_payments`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `order_id` | `text` | No | Yes | → orders | Order identifier | Composite PK part |
| `payment_sequential` | `integer` | No | Yes | — | Payment sequence number | Composite PK part |
| `payment_type` | `text` | No | No | — | Payment method | CHECK constraint |
| `payment_installments` | `integer` | No | No | — | Number of installments | CHECK ≥ 1 |
| `payment_value` | `numeric(12,2)` | No | No | — | Payment amount (BRL) | Sum per order for true revenue |

### C.5 `order_reviews`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `review_id` | `text` | No | Yes | — | Review identifier | **Not unique alone** |
| `order_id` | `text` | No | Yes | → orders | Order reviewed | Composite PK part |
| `review_score` | `smallint` | No | No | — | Satisfaction (1–5) | CHECK (1–5) |
| `review_comment_title` | `text` | Yes | No | — | Review title | Often null |
| `review_comment_message` | `text` | Yes | No | — | Review body | Often null |
| `review_creation_date` | `timestamp` | No | No | — | When survey sent | |
| `review_answer_timestamp` | `timestamp` | Yes | No | — | When customer responded | |

### C.6 `products`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `product_id` | `text` | No | Yes | — | Unique product | 32-char hex |
| `product_category_name` | `text` | Yes | No | → category_translation | Portuguese category | Nullable |
| `product_name_length` | `integer` | Yes | No | — | Character count | Original typo: `lenght` |
| `product_description_length` | `integer` | Yes | No | — | Character count | Original typo: `lenght` |
| `product_photos_qty` | `integer` | Yes | No | — | Number of photos | |
| `product_weight_g` | `numeric(10,2)` | Yes | No | — | Weight in grams | |
| `product_length_cm` | `numeric(10,2)` | Yes | No | — | Length in cm | |
| `product_height_cm` | `numeric(10,2)` | Yes | No | — | Height in cm | |
| `product_width_cm` | `numeric(10,2)` | Yes | No | — | Width in cm | |

### C.7 `sellers`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `seller_id` | `text` | No | Yes | — | Unique seller | |
| `seller_zip_code_prefix` | `integer` | Yes | No | — | ZIP prefix | |
| `seller_city` | `text` | Yes | No | — | City | |
| `seller_state` | `char(2)` | Yes | No | — | State abbreviation | |

### C.8 `product_category_translation`

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `product_category_name` | `text` | No | Yes | — | Portuguese category name | 73 values expected |
| `product_category_name_english` | `text` | Yes | No | — | English translation | 71 values; 2 nulls |

### C.9 `geolocation` (raw)

| Column | Type | Nullable | PK | FK | Description | Notes |
|---|---|---|---|---|---|---|
| `geolocation_zip_code_prefix` | `integer` | No | Yes* | — | ZIP prefix | *Composite logical key |
| `geolocation_lat` | `numeric(10,6)` | No | Yes* | — | Latitude | *with zip prefix |
| `geolocation_lng` | `numeric(10,6)` | No | Yes* | — | Longitude | *with zip prefix |
| `geolocation_city` | `text` | Yes | No | — | City | |
| `geolocation_state` | `char(2)` | Yes | No | — | State | |

*The raw table has no natural single-column PK. A surrogate `bigserial` PK is recommended for loading, but the logical grain is (zip_code_prefix, lat, lng).*

---

## D. Keys and Relationships

| Parent Table | Child Table | Join Columns | Cardinality | Enforce FK? | Notes |
|---|---|---|---|---|---|
| `customers` | `orders` | `customer_id` | 1 : N | Yes | Every order must have a customer |
| `orders` | `order_items` | `order_id` | 1 : N | Yes | An order can have multiple items |
| `orders` | `order_payments` | `order_id` | 1 : N | Yes | Split payments → multiple rows |
| `orders` | `order_reviews` | `order_id` | 1 : N | Yes | **Not 1:1** — some orders have multiple reviews |
| `products` | `order_items` | `product_id` | 1 : N | Yes | |
| `sellers` | `order_items` | `seller_id` | 1 : N | Yes | |
| `product_category_translation` | `products` | `product_category_name` | 1 : N | Yes (nullable) | 2 categories lack translation |
| `geolocation` (view) | `customers` | `zip_code_prefix` | 1 : N | No (view) | Use deduplicated view to avoid fan-out |
| `geolocation` (view) | `sellers` | `zip_code_prefix` | 1 : N | No (view) | Same |

**Orphan records:** The Olist dataset is generally well-linked. No major orphan issues are reported for orders→customers, order_items→products, or order_items→sellers. However, geolocation is a **separate, unlinked dimension** — it cannot have enforced FKs from customers/sellers because it is not guaranteed to have a row for every ZIP prefix, and it has multiple rows per prefix.

---

## E. Constraints

### Primary Keys

```sql
-- Customers
ALTER TABLE customers ADD CONSTRAINT pk_customers PRIMARY KEY (customer_id);

-- Orders
ALTER TABLE orders ADD CONSTRAINT pk_orders PRIMARY KEY (order_id);

-- Order items (composite)
ALTER TABLE order_items ADD CONSTRAINT pk_order_items PRIMARY KEY (order_id, order_item_id);

-- Order payments (composite)
ALTER TABLE order_payments ADD CONSTRAINT pk_order_payments PRIMARY KEY (order_id, payment_sequential);

-- Order reviews (composite — critical!)
ALTER TABLE order_reviews ADD CONSTRAINT pk_order_reviews PRIMARY KEY (review_id, order_id);

-- Products
ALTER TABLE products ADD CONSTRAINT pk_products PRIMARY KEY (product_id);

-- Sellers
ALTER TABLE sellers ADD CONSTRAINT pk_sellers PRIMARY KEY (seller_id);

-- Category translation
ALTER TABLE product_category_translation ADD CONSTRAINT pk_category_translation PRIMARY KEY (product_category_name);
```

### Foreign Keys

```sql
ALTER TABLE orders ADD CONSTRAINT fk_orders_customer
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id);

ALTER TABLE order_items ADD CONSTRAINT fk_order_items_order
  FOREIGN KEY (order_id) REFERENCES orders(order_id);

ALTER TABLE order_items ADD CONSTRAINT fk_order_items_product
  FOREIGN KEY (product_id) REFERENCES products(product_id);

ALTER TABLE order_items ADD CONSTRAINT fk_order_items_seller
  FOREIGN KEY (seller_id) REFERENCES sellers(seller_id);

ALTER TABLE order_payments ADD CONSTRAINT fk_payments_order
  FOREIGN KEY (order_id) REFERENCES orders(order_id);

ALTER TABLE order_reviews ADD CONSTRAINT fk_reviews_order
  FOREIGN KEY (order_id) REFERENCES orders(order_id);

ALTER TABLE products ADD CONSTRAINT fk_products_category
  FOREIGN KEY (product_category_name) REFERENCES product_category_translation(product_category_name);
```

### Check Constraints

```sql
-- Order status
ALTER TABLE orders ADD CONSTRAINT chk_order_status
  CHECK (order_status IN ('delivered','shipped','canceled','processing','invoiced','approved','unavailable','created'));

-- Payment type
ALTER TABLE order_payments ADD CONSTRAINT chk_payment_type
  CHECK (payment_type IN ('credit_card','boleto','voucher','debit_card'));

-- Review score
ALTER TABLE order_reviews ADD CONSTRAINT chk_review_score
  CHECK (review_score BETWEEN 1 AND 5);

-- Payment installments
ALTER TABLE order_payments ADD CONSTRAINT chk_installments
  CHECK (payment_installments >= 1);

-- Positive monetary values
ALTER TABLE order_items ADD CONSTRAINT chk_item_price
  CHECK (price >= 0);
ALTER TABLE order_items ADD CONSTRAINT chk_freight_value
  CHECK (freight_value >= 0);
ALTER TABLE order_payments ADD CONSTRAINT chk_payment_value
  CHECK (payment_value >= 0);
```

### NOT NULL Constraints

Apply NOT NULL to all columns marked “No” in the nullable column above. In particular:
- `customers.customer_id`, `customers.customer_unique_id`
- `orders.order_id`, `orders.customer_id`, `orders.order_status`, `orders.order_purchase_timestamp`
- `order_items` composite key columns, `price`, `freight_value`
- `order_payments` composite key columns, `payment_type`, `payment_value`
- `order_reviews` composite key columns, `review_score`
- `products.product_id`

### Important Data Quality Decisions

| Issue | Recommendation |
|---|---|
| **Duplicate `review_id` values** | Do **not** enforce a unique constraint on `review_id` alone. Use composite PK `(review_id, order_id)`. This preserves all 99k+ rows. |
| **2 product categories without translation** | Keep `product_category_name_english` nullable. Do not reject the product rows. Use `COALESCE(pct.product_category_name_english, p.product_category_name, 'unknown')` in views. |
| **Geolocation multiple rows per ZIP** | Do not enforce a PK on `zip_code_prefix` in the raw table. Create a **deduplicated view** instead. |
| **Naive timestamps** | Store as `timestamp` (no timezone). Do not convert to `timestamptz` during ingestion — the original timezone is unknown. Derived durations (e.g., delivery days) should be calculated in views. |
| **Canceled orders with delivery timestamps** | Preserve as-is. Add a note/comment that some canceled orders have delivery dates — this is a known data quality quirk. |

---

## F. Indexes

Indexes should serve the actual query patterns of an AI SQL agent:

| Table | Index Columns | Type | Reason |
|---|---|---|---|
| `orders` | `(order_purchase_timestamp)` | B-tree | Date-range filters: “orders in Q3 2017” |
| `orders` | `(customer_id)` | B-tree | Join to customers; “orders for this customer” |
| `orders` | `(order_status)` | B-tree | Filter: “delivered orders”, “canceled orders” |
| `order_items` | `(product_id)` | B-tree | Join to products; “sales by product” |
| `order_items` | `(seller_id)` | B-tree | Join to sellers; “sales by seller” |
| `order_items` | `(order_id)` | B-tree | Already covered by PK prefix, but useful for FK enforcement |
| `order_payments` | `(order_id)` | B-tree | Join to orders; already PK prefix |
| `order_payments` | `(payment_type)` | B-tree | Filter: “revenue by payment method” |
| `order_reviews` | `(order_id)` | B-tree | Join to orders |
| `order_reviews` | `(review_score)` | B-tree | Filter: “average review score” |
| `products` | `(product_category_name)` | B-tree | Join to translation; “sales by category” |
| `customers` | `(customer_unique_id)` | B-tree | **Critical** for repeat-customer queries |
| `customers` | `(customer_state)` | B-tree | Geographic filtering |
| `sellers` | `(seller_state)` | B-tree | Geographic filtering |

**Do not index:** `order_id` on `orders` (already PK), `customer_id` on `customers` (already PK), `product_id` on `products` (already PK), `seller_id` on `sellers` (already PK).

---

## G. Recommended Agent-Facing Views

Create a small number of views that **solve real problems**:

### G.1 `v_order_sales_summary` — Order-level revenue (prevents double counting)

```sql
CREATE VIEW analytics.v_order_sales_summary AS
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    o.order_status,
    o.order_purchase_timestamp::date AS order_date,
    -- Item-level revenue: sum of price + freight for all items
    COALESCE(SUM(oi.price + oi.freight_value), 0) AS total_order_value,
    -- Payment-level revenue: sum of payment_value (true collected amount)
    (SELECT COALESCE(SUM(p.payment_value), 0)
     FROM order_payments p WHERE p.order_id = o.order_id) AS total_payment_value,
    COUNT(oi.order_item_id) AS item_count
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.customer_id
LEFT JOIN order_items oi ON o.order_id = oi.order_id
GROUP BY o.order_id, o.customer_id, c.customer_unique_id, o.order_status,
         o.order_purchase_timestamp::date;
```

**Why this helps the agent:** An LLM that joins `orders → order_items → order_payments` naively will multiply rows (fan-out). This view presents the order grain explicitly, with both item-sum and payment-sum metrics clearly labeled. The agent can simply query `SELECT SUM(total_payment_value) FROM v_order_sales_summary` for total revenue.

### G.2 `v_order_item_detail` — Item-level sales detail

```sql
CREATE VIEW analytics.v_order_item_detail AS
SELECT
    oi.order_id,
    oi.order_item_id,
    oi.product_id,
    p.product_category_name,
    COALESCE(pct.product_category_name_english, p.product_category_name, 'unknown') AS product_category_english,
    oi.seller_id,
    oi.price,
    oi.freight_value,
    oi.price + oi.freight_value AS item_total,
    o.order_purchase_timestamp::date AS order_date,
    o.order_status,
    c.customer_unique_id,
    c.customer_state
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN customers c ON o.customer_id = c.customer_id
JOIN products p ON oi.product_id = p.product_id
LEFT JOIN product_category_translation pct ON p.product_category_name = pct.product_category_name;
```

**Why this helps:** This is the workhorse view for product/seller/category analysis. The agent can group by `product_category_english`, `seller_id`, or `product_id` without needing to know the translation join.

### G.3 `v_customer_orders` — Customer-level summary

```sql
CREATE VIEW analytics.v_customer_orders AS
SELECT
    c.customer_unique_id,
    COUNT(DISTINCT o.order_id) AS total_orders,
    MIN(o.order_purchase_timestamp) AS first_order_date,
    MAX(o.order_purchase_timestamp) AS last_order_date,
    COALESCE(SUM(pv.total_payment), 0) AS lifetime_value
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
LEFT JOIN LATERAL (
    SELECT SUM(payment_value) AS total_payment
    FROM order_payments p WHERE p.order_id = o.order_id
) pv ON TRUE
GROUP BY c.customer_unique_id;
```

**Why this helps:** This view explicitly uses `customer_unique_id` and pre-aggregates to the person level. The agent can simply ask “how many repeat customers?” and the answer is `SELECT COUNT(*) FROM v_customer_orders WHERE total_orders > 1`.

### G.4 `v_geolocation_dedup` — Deduplicated geolocation

```sql
CREATE VIEW analytics.v_geolocation_dedup AS
SELECT
    geolocation_zip_code_prefix AS zip_code_prefix,
    AVG(geolocation_lat) AS latitude,
    AVG(geolocation_lng) AS longitude,
    MIN(geolocation_city) AS city,
    MIN(geolocation_state) AS state
FROM geolocation
GROUP BY geolocation_zip_code_prefix;
```

**Why this helps:** This view guarantees one row per ZIP prefix. The agent can join `customers.customer_zip_code_prefix = v_geolocation_dedup.zip_code_prefix` without risk of row multiplication.

### G.5 `v_delivery_performance` — Delivery metrics

```sql
CREATE VIEW analytics.v_delivery_performance AS
SELECT
    o.order_id,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    -- Delivery duration in days
    EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp)) / 86400 AS delivery_days,
    -- Whether delivery was late
    CASE
        WHEN o.order_delivered_customer_date IS NOT NULL
             AND o.order_estimated_delivery_date IS NOT NULL
             AND o.order_delivered_customer_date > o.order_estimated_delivery_date
        THEN TRUE ELSE FALSE
    END AS is_late
FROM orders o;
```

**Why this helps:** Derived fields (delivery days, lateness) are calculated dynamically, not stored. The agent doesn't need to figure out the interval arithmetic.

---

## H. AI SQL Agent Risks and Mitigations

| Risk | Why It Happens | Schema Mitigation |
|---|---|---|
| **Double-counting payment values** | Joining `orders → order_items → order_payments` produces one row per item × payment combination. Summing `payment_value` after this join multiplies it. | Create `v_order_sales_summary` with separate order-level and item-level metrics. Add `COMMENT ON TABLE` warning. |
| **Counting customers incorrectly** | Agent uses `customer_id` instead of `customer_unique_id` for “how many customers?” | Name the column `customer_unique_id` explicitly. Add `COMMENT ON COLUMN customers.customer_id` explaining it is per-order. Create `v_customer_orders` that uses `customer_unique_id`. |
| **Confusing order revenue with payment totals** | `SUM(price + freight_value)` ≠ `SUM(payment_value)` because of vouchers, discounts, and split payments. | Expose both metrics in `v_order_sales_summary` with clear names: `total_order_value` (item sum) vs `total_payment_value` (actual collected). |
| **Assuming `review_id` is unique** | The column name suggests a PK, but duplicates exist. | Use composite PK `(review_id, order_id)`. Add `COMMENT ON COLUMN order_reviews.review_id` explaining it is not unique. |
| **Joining geolocation directly** | Raw geolocation has multiple rows per ZIP prefix → row multiplication. | Provide `v_geolocation_dedup` with one row per ZIP prefix. Add `COMMENT ON TABLE geolocation` warning about grain. |
| **Aggregating order-level metrics after joining order_items** | `COUNT(DISTINCT order_id)` is often forgotten when joining items. | `v_order_sales_summary` is at order grain. The agent can use it for order counts without needing `DISTINCT`. |
| **Using `order_item_id` as a global ID** | It is only unique within an order. | Composite PK `(order_id, order_item_id)`. Comment explains it. |
| **Incorrectly calculating delivery durations** | Timestamps are naive; timezone conversion could shift dates. | Store as `timestamp` without timezone. Provide `v_delivery_performance` with duration already computed. |
| **Forgetting `payment_sequential` in joins** | An order can have multiple payment rows. | Composite PK `(order_id, payment_sequential)`. `v_order_sales_summary` pre-aggregates payments. |

---

## I. Final Recommended PostgreSQL Schema

Below is the complete DDL. I recommend a **single `public` schema** with prefixed table names for a portfolio project, and a `analytics` schema for views.

```sql
-- ============================================================
-- OLIST E-COMMERCE DATABASE SCHEMA FOR AI SQL AGENT
-- PostgreSQL 14+
-- ============================================================

-- ------------------------------------------------------------
-- 1. CORE ENTITY TABLES
-- ------------------------------------------------------------

CREATE TABLE customers (
    customer_id                 text        NOT NULL,
    customer_unique_id          text        NOT NULL,
    customer_zip_code_prefix    integer,
    customer_city               text,
    customer_state              char(2),
    CONSTRAINT pk_customers PRIMARY KEY (customer_id)
);

COMMENT ON TABLE customers IS
    'Customer dimension. One row per customer_id (transient per-order identity). '
    'For customer-level analysis (repeat purchases, CLTV, retention), always use customer_unique_id.';

COMMENT ON COLUMN customers.customer_id IS
    'Transient identifier generated for EACH ORDER. Do NOT use for counting unique customers.';

COMMENT ON COLUMN customers.customer_unique_id IS
    'Persistent identifier for the real human buyer. Use this for customer counts, repeat-purchase, and CLTV.';

COMMENT ON COLUMN customers.customer_zip_code_prefix IS
    'First 5 digits of the Brazilian ZIP code. Joins to geolocation/geolocation_zip_code_prefix.';


CREATE TABLE sellers (
    seller_id                   text        NOT NULL,
    seller_zip_code_prefix      integer,
    seller_city                 text,
    seller_state                char(2),
    CONSTRAINT pk_sellers PRIMARY KEY (seller_id)
);

COMMENT ON TABLE sellers IS
    'Seller dimension. One row per seller_id.';


CREATE TABLE product_category_translation (
    product_category_name           text        NOT NULL,
    product_category_name_english   text,
    CONSTRAINT pk_category_translation PRIMARY KEY (product_category_name)
);

COMMENT ON TABLE product_category_translation IS
    'Lookup table: Portuguese product category name to English translation. '
    '71 translations exist for 73 product categories; 2 categories have NULL English names.';

COMMENT ON COLUMN product_category_translation.product_category_name_english IS
    'English translation. NULL for 2 categories (pc_gamer, portateis_cozinha_e_preparadores_de_alimentos).';


CREATE TABLE products (
    product_id                  text        NOT NULL,
    product_category_name       text,
    product_name_length         integer,
    product_description_length  integer,
    product_photos_qty          integer,
    product_weight_g            numeric(10,2),
    product_length_cm           numeric(10,2),
    product_height_cm           numeric(10,2),
    product_width_cm            numeric(10,2),
    CONSTRAINT pk_products PRIMARY KEY (product_id),
    CONSTRAINT fk_products_category
        FOREIGN KEY (product_category_name)
        REFERENCES product_category_translation(product_category_name)
);

COMMENT ON TABLE products IS
    'Product dimension. One row per product_id.';

COMMENT ON COLUMN products.product_name_length IS
    'Original column name in source CSV is "product_name_lenght" (typo preserved).';

COMMENT ON COLUMN products.product_description_length IS
    'Original column name in source CSV is "product_description_lenght" (typo preserved).';


CREATE TABLE geolocation (
    geolocation_zip_code_prefix integer     NOT NULL,
    geolocation_lat             numeric(10,6) NOT NULL,
    geolocation_lng             numeric(10,6) NOT NULL,
    geolocation_city            text,
    geolocation_state           char(2),
    -- Surrogate PK for loading; logical grain is (zip_prefix, lat, lng)
    id                          bigserial   NOT NULL,
    CONSTRAINT pk_geolocation PRIMARY KEY (id)
);

COMMENT ON TABLE geolocation IS
    'Raw geolocation lookup. IMPORTANT: Multiple rows exist per zip_code_prefix because '
    'multiple lat/lng coordinate points map to the same prefix. '
    'NEVER join directly to customers/sellers — use the analytics.v_geolocation_dedup view instead.';

COMMENT ON COLUMN geolocation.geolocation_zip_code_prefix IS
    'First 5 digits of Brazilian ZIP code. Not unique — multiple coordinate points per prefix.';


-- ------------------------------------------------------------
-- 2. ORDER TABLES
-- ------------------------------------------------------------

CREATE TABLE orders (
    order_id                        text        NOT NULL,
    customer_id                     text        NOT NULL,
    order_status                    text        NOT NULL,
    order_purchase_timestamp        timestamp   NOT NULL,
    order_approved_at               timestamp,
    order_delivered_carrier_date    timestamp,
    order_delivered_customer_date   timestamp,
    order_estimated_delivery_date   timestamp,
    CONSTRAINT pk_orders PRIMARY KEY (order_id),
    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    CONSTRAINT chk_order_status
        CHECK (order_status IN (
            'delivered','shipped','canceled','processing',
            'invoiced','approved','unavailable','created'
        ))
);

COMMENT ON TABLE orders IS
    'Order fact table. One row per order_id. '
    'Timestamps are naive (no timezone) — original timezone is unknown. '
    'Derived durations (delivery days) should be calculated in views, not stored.';

COMMENT ON COLUMN orders.order_purchase_timestamp IS
    'When the order was placed. Naive timestamp (no timezone).';

COMMENT ON COLUMN orders.order_approved_at IS
    'Payment approval timestamp. Nullable — some orders lack this.';

COMMENT ON COLUMN orders.order_delivered_carrier_date IS
    'Date order was handed to the shipping carrier. Nullable.';

COMMENT ON COLUMN orders.order_delivered_customer_date IS
    'Actual delivery date. Nullable — not all orders are delivered. '
    'NOTE: Some canceled orders have delivery timestamps (known data quirk).';

COMMENT ON COLUMN orders.order_estimated_delivery_date IS
    'Delivery date promised at purchase. Nullable.';


CREATE TABLE order_items (
    order_id            text        NOT NULL,
    order_item_id       integer     NOT NULL,
    product_id          text        NOT NULL,
    seller_id           text        NOT NULL,
    shipping_limit_date timestamp,
    price               numeric(12,2) NOT NULL,
    freight_value       numeric(12,2) NOT NULL,
    CONSTRAINT pk_order_items PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id) REFERENCES orders(order_id),
    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id) REFERENCES products(product_id),
    CONSTRAINT fk_order_items_seller
        FOREIGN KEY (seller_id) REFERENCES sellers(seller_id),
    CONSTRAINT chk_item_price CHECK (price >= 0),
    CONSTRAINT chk_freight_value CHECK (freight_value >= 0)
);

COMMENT ON TABLE order_items IS
    'Order line-item fact table. One row per (order_id, order_item_id). '
    'An order with 3 products yields 3 rows. '
    'WARNING: Summing price here gives item-level revenue. '
    'For true collected revenue, use order_payments.payment_value instead.';

COMMENT ON COLUMN order_items.order_item_id IS
    'Sequential number within an order. NOT globally unique. '
    'Composite PK with order_id.';

COMMENT ON COLUMN order_items.shipping_limit_date IS
    'Seller''s shipping deadline (distinct from delivery dates in orders).';


CREATE TABLE order_payments (
    order_id                text        NOT NULL,
    payment_sequential      integer     NOT NULL,
    payment_type            text        NOT NULL,
    payment_installments    integer     NOT NULL,
    payment_value           numeric(12,2) NOT NULL,
    CONSTRAINT pk_order_payments PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT fk_payments_order
        FOREIGN KEY (order_id) REFERENCES orders(order_id),
    CONSTRAINT chk_payment_type
        CHECK (payment_type IN ('credit_card','boleto','voucher','debit_card')),
    CONSTRAINT chk_installments CHECK (payment_installments >= 1),
    CONSTRAINT chk_payment_value CHECK (payment_value >= 0)
);

COMMENT ON TABLE order_payments IS
    'Payment fact table. One row per (order_id, payment_sequential). '
    'If an order is split across two payment methods, there are two rows. '
    'TRUE REVENUE = SUM(payment_value) grouped by order_id. '
    'WARNING: Do NOT sum payment_value after joining to order_items — that double-counts.';

COMMENT ON COLUMN order_payments.payment_sequential IS
    'Sequence number for split payments. Composite PK with order_id.';

COMMENT ON COLUMN order_payments.payment_value IS
    'Amount paid in BRL. Sum per order for true revenue.';


CREATE TABLE order_reviews (
    review_id               text        NOT NULL,
    order_id                text        NOT NULL,
    review_score            smallint    NOT NULL,
    review_comment_title    text,
    review_comment_message  text,
    review_creation_date    timestamp   NOT NULL,
    review_answer_timestamp timestamp,
    CONSTRAINT pk_order_reviews PRIMARY KEY (review_id, order_id),
    CONSTRAINT fk_reviews_order
        FOREIGN KEY (order_id) REFERENCES orders(order_id),
    CONSTRAINT chk_review_score CHECK (review_score BETWEEN 1 AND 5)
);

COMMENT ON TABLE order_reviews IS
    'Review fact table. One row per (review_id, order_id). '
    'IMPORTANT: review_id is NOT unique alone — the same review_id can appear for '
    'different orders. 547 orders have more than one review. '
    'Composite PK (review_id, order_id) is required.';

COMMENT ON COLUMN order_reviews.review_id IS
    'Review identifier. NOT unique alone. Use with order_id as composite PK.';

COMMENT ON COLUMN order_reviews.review_creation_date IS
    'When the review survey was sent to the customer.';

COMMENT ON COLUMN order_reviews.review_answer_timestamp IS
    'When the customer answered the review survey. Nullable.';


-- ------------------------------------------------------------
-- 3. INDEXES
-- ------------------------------------------------------------

CREATE INDEX idx_orders_purchase_ts     ON orders (order_purchase_timestamp);
CREATE INDEX idx_orders_customer        ON orders (customer_id);
CREATE INDEX idx_orders_status          ON orders (order_status);
CREATE INDEX idx_order_items_product    ON order_items (product_id);
CREATE INDEX idx_order_items_seller     ON order_items (seller_id);
CREATE INDEX idx_order_payments_type    ON order_payments (payment_type);
CREATE INDEX idx_order_reviews_order    ON order_reviews (order_id);
CREATE INDEX idx_order_reviews_score    ON order_reviews (review_score);
CREATE INDEX idx_products_category      ON products (product_category_name);
CREATE INDEX idx_customers_unique       ON customers (customer_unique_id);
CREATE INDEX idx_customers_state        ON customers (customer_state);
CREATE INDEX idx_sellers_state          ON sellers (seller_state);
CREATE INDEX idx_geolocation_zip        ON geolocation (geolocation_zip_code_prefix);


-- ------------------------------------------------------------
-- 4. AGENT-FACING VIEWS
-- ------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS analytics;

-- Order-level revenue summary (prevents double counting)
CREATE VIEW analytics.v_order_sales_summary AS
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    o.order_status,
    o.order_purchase_timestamp::date AS order_date,
    COALESCE(SUM(oi.price + oi.freight_value), 0) AS total_order_value,
    (SELECT COALESCE(SUM(p.payment_value), 0)
     FROM order_payments p WHERE p.order_id = o.order_id) AS total_payment_value,
    COUNT(oi.order_item_id) AS item_count
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.customer_id
LEFT JOIN order_items oi ON o.order_id = oi.order_id
GROUP BY o.order_id, o.customer_id, c.customer_unique_id,
         o.order_status, o.order_purchase_timestamp::date;

COMMENT ON VIEW analytics.v_order_sales_summary IS
    'Order-level sales summary. One row per order. '
    'total_order_value = sum of item prices + freight (item-level revenue). '
    'total_payment_value = sum of payment records (true collected amount). '
    'Use total_payment_value for revenue KPIs to avoid double counting.';


-- Item-level sales detail (with category translation)
CREATE VIEW analytics.v_order_item_detail AS
SELECT
    oi.order_id,
    oi.order_item_id,
    oi.product_id,
    p.product_category_name,
    COALESCE(pct.product_category_name_english,
             p.product_category_name, 'unknown') AS product_category_english,
    oi.seller_id,
    oi.price,
    oi.freight_value,
    oi.price + oi.freight_value AS item_total,
    o.order_purchase_timestamp::date AS order_date,
    o.order_status,
    c.customer_unique_id,
    c.customer_state
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN customers c ON o.customer_id = c.customer_id
JOIN products p ON oi.product_id = p.product_id
LEFT JOIN product_category_translation pct
    ON p.product_category_name = pct.product_category_name;

COMMENT ON VIEW analytics.v_order_item_detail IS
    'Item-level sales detail with English category translation. '
    'One row per order item. Use for product/seller/category analysis.';


-- Customer-level summary
CREATE VIEW analytics.v_customer_orders AS
SELECT
    c.customer_unique_id,
    COUNT(DISTINCT o.order_id) AS total_orders,
    MIN(o.order_purchase_timestamp) AS first_order_date,
    MAX(o.order_purchase_timestamp) AS last_order_date,
    COALESCE(SUM(pv.total_payment), 0) AS lifetime_value
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
LEFT JOIN LATERAL (
    SELECT SUM(payment_value) AS total_payment
    FROM order_payments p WHERE p.order_id = o.order_id
) pv ON TRUE
GROUP BY c.customer_unique_id;

COMMENT ON VIEW analytics.v_customer_orders IS
    'Customer-level summary. One row per customer_unique_id (real person). '
    'Uses customer_unique_id (NOT customer_id). '
    'total_orders > 1 identifies repeat customers. '
    'lifetime_value = sum of all payment_value across all orders.';


-- Deduplicated geolocation
CREATE VIEW analytics.v_geolocation_dedup AS
SELECT
    geolocation_zip_code_prefix AS zip_code_prefix,
    AVG(geolocation_lat) AS latitude,
    AVG(geolocation_lng) AS longitude,
    MIN(geolocation_city) AS city,
    MIN(geolocation_state) AS state
FROM geolocation
GROUP BY geolocation_zip_code_prefix;

COMMENT ON VIEW analytics.v_geolocation_dedup IS
    'Deduplicated geolocation. One row per zip_code_prefix. '
    'Coordinates are averaged across all coordinate points for that prefix. '
    'Use this view instead of the raw geolocation table to avoid row multiplication.';


-- Delivery performance
CREATE VIEW analytics.v_delivery_performance AS
SELECT
    o.order_id,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp)) / 86400 AS delivery_days,
    CASE
        WHEN o.order_delivered_customer_date IS NOT NULL
             AND o.order_estimated_delivery_date IS NOT NULL
             AND o.order_delivered_customer_date > o.order_estimated_delivery_date
        THEN TRUE ELSE FALSE
    END AS is_late
FROM orders o;

COMMENT ON VIEW analytics.v_delivery_performance IS
    'Delivery performance metrics. One row per order. '
    'delivery_days = days between purchase and actual delivery. '
    'is_late = TRUE if delivered after estimated date.';
```

---

## J. Information that `get_schema` Should Expose

When the LangGraph agent calls `get_schema`, it should receive structured metadata that makes join paths and aggregation boundaries obvious. Here is the recommended representation:

```json
{
  "tables": [
    {
      "name": "orders",
      "comment": "Order fact table. One row per order_id. Timestamps are naive (no timezone).",
      "columns": [
        {"name": "order_id", "type": "text", "nullable": false, "pk": true, "comment": "Unique order identifier"},
        {"name": "customer_id", "type": "text", "nullable": false, "fk": "customers.customer_id", "comment": "Links to customers"},
        {"name": "order_status", "type": "text", "nullable": false, "comment": "delivered, shipped, canceled, processing, invoiced, approved, unavailable, created"},
        {"name": "order_purchase_timestamp", "type": "timestamp", "nullable": false, "comment": "When order was placed"},
        {"name": "order_approved_at", "type": "timestamp", "nullable": true},
        {"name": "order_delivered_carrier_date", "type": "timestamp", "nullable": true},
        {"name": "order_delivered_customer_date", "type": "timestamp", "nullable": true, "comment": "Actual delivery. Some canceled orders have this."},
        {"name": "order_estimated_delivery_date", "type": "timestamp", "nullable": true}
      ]
    },
    {
      "name": "order_items",
      "comment": "Order line-item fact. One row per (order_id, order_item_id).",
      "columns": [
        {"name": "order_id", "type": "text", "nullable": false, "pk": true, "fk": "orders.order_id"},
        {"name": "order_item_id", "type": "integer", "nullable": false, "pk": true, "comment": "Sequence within order. NOT globally unique."},
        {"name": "product_id", "type": "text", "nullable": false, "fk": "products.product_id"},
        {"name": "seller_id", "type": "text", "nullable": false, "fk": "sellers.seller_id"},
        {"name": "shipping_limit_date", "type": "timestamp", "nullable": true},
        {"name": "price", "type": "numeric(12,2)", "nullable": false},
        {"name": "freight_value", "type": "numeric(12,2)", "nullable": false}
      ]
    },
    {
      "name": "order_payments",
      "comment": "Payment fact. One row per (order_id, payment_sequential). TRUE REVENUE = SUM(payment_value) grouped by order_id.",
      "columns": [
        {"name": "order_id", "type": "text", "nullable": false, "pk": true, "fk": "orders.order_id"},
        {"name": "payment_sequential", "type": "integer", "nullable": false, "pk": true},
        {"name": "payment_type", "type": "text", "nullable": false, "comment": "credit_card, boleto, voucher, debit_card"},
        {"name": "payment_installments", "type": "integer", "nullable": false},
        {"name": "payment_value", "type": "numeric(12,2)", "nullable": false, "comment": "Amount in BRL. Sum per order for revenue."}
      ]
    },
    {
      "name": "order_reviews",
      "comment": "Review fact. One row per (review_id, order_id). review_id is NOT unique alone.",
      "columns": [
        {"name": "review_id", "type": "text", "nullable": false, "pk": true, "comment": "NOT unique alone. Composite PK with order_id."},
        {"name": "order_id", "type": "text", "nullable": false, "pk": true, "fk": "orders.order_id"},
        {"name": "review_score", "type": "smallint", "nullable": false, "comment": "1 to 5"},
        {"name": "review_comment_title", "type": "text", "nullable": true},
        {"name": "review_comment_message", "type": "text", "nullable": true},
        {"name": "review_creation_date", "type": "timestamp", "nullable": false},
        {"name": "review_answer_timestamp", "type": "timestamp", "nullable": true}
      ]
    },
    {
      "name": "customers",
      "comment": "Customer dimension. One row per customer_id. Use customer_unique_id for real customer counts.",
      "columns": [
        {"name": "customer_id", "type": "text", "nullable": false, "pk": true, "comment": "Transient per-order ID. Do NOT use for customer counts."},
        {"name": "customer_unique_id", "type": "text", "nullable": false, "comment": "Persistent real-person ID. USE THIS for customer counts."},
        {"name": "customer_zip_code_prefix", "type": "integer", "nullable": true},
        {"name": "customer_city", "type": "text", "nullable": true},
        {"name": "customer_state", "type": "char(2)", "nullable": true}
      ]
    },
    {
      "name": "products",
      "comment": "Product dimension. One row per product_id.",
      "columns": [
        {"name": "product_id", "type": "text", "nullable": false, "pk": true},
        {"name": "product_category_name", "type": "text", "nullable": true, "fk": "product_category_translation.product_category_name"},
        {"name": "product_name_length", "type": "integer", "nullable": true},
        {"name": "product_description_length", "type": "integer", "nullable": true},
        {"name": "product_photos_qty", "type": "integer", "nullable": true},
        {"name": "product_weight_g", "type": "numeric(10,2)", "nullable": true},
        {"name": "product_length_cm", "type": "numeric(10,2)", "nullable": true},
        {"name": "product_height_cm", "type": "numeric(10,2)", "nullable": true},
        {"name": "product_width_cm", "type": "numeric(10,2)", "nullable": true}
      ]
    },
    {
      "name": "sellers",
      "comment": "Seller dimension. One row per seller_id.",
      "columns": [
        {"name": "seller_id", "type": "text", "nullable": false, "pk": true},
        {"name": "seller_zip_code_prefix", "type": "integer", "nullable": true},
        {"name": "seller_city", "type": "text", "nullable": true},
        {"name": "seller_state", "type": "char(2)", "nullable": true}
      ]
    },
    {
      "name": "product_category_translation",
      "comment": "Portuguese to English category lookup. 71 translations for 73 categories.",
      "columns": [
        {"name": "product_category_name", "type": "text", "nullable": false, "pk": true},
        {"name": "product_category_name_english", "type": "text", "nullable": true, "comment": "NULL for 2 categories."}
      ]
    },
    {
      "name": "geolocation",
      "comment": "Raw geolocation. Multiple rows per zip_code_prefix. Use analytics.v_geolocation_dedup instead.",
      "columns": [
        {"name": "geolocation_zip_code_prefix", "type": "integer", "nullable": false},
        {"name": "geolocation_lat", "type": "numeric(10,6)", "nullable": false},
        {"name": "geolocation_lng", "type": "numeric(10,6)", "nullable": false},
        {"name": "geolocation_city", "type": "text", "nullable": true},
        {"name": "geolocation_state", "type": "char(2)", "nullable": true}
      ]
    }
  ],
  "views": [
    {
      "name": "analytics.v_order_sales_summary",
      "comment": "Order-level sales summary. One row per order. total_payment_value is true revenue.",
      "columns": [
        {"name": "order_id"}, {"name": "customer_unique_id"},
        {"name": "order_status"}, {"name": "order_date"},
        {"name": "total_order_value", "comment": "Sum of item prices + freight"},
        {"name": "total_payment_value", "comment": "Sum of payment records — USE FOR REVENUE"},
        {"name": "item_count"}
      ]
    },
    {
      "name": "analytics.v_order_item_detail",
      "comment": "Item-level sales with English category. One row per order item.",
      "columns": [
        {"name": "order_id"}, {"name": "order_item_id"}, {"name": "product_id"},
        {"name": "product_category_name"}, {"name": "product_category_english"},
        {"name": "seller_id"}, {"name": "price"}, {"name": "freight_value"},
        {"name": "item_total"}, {"name": "order_date"}, {"name": "order_status"},
        {"name": "customer_unique_id"}, {"name": "customer_state"}
      ]
    },
    {
      "name": "analytics.v_customer_orders",
      "comment": "Customer-level summary. One row per customer_unique_id.",
      "columns": [
        {"name": "customer_unique_id"}, {"name": "total_orders"},
        {"name": "first_order_date"}, {"name": "last_order_date"},
        {"name": "lifetime_value"}
      ]
    },
    {
      "name": "analytics.v_geolocation_dedup",
      "comment": "Deduplicated geolocation. One row per zip_code_prefix.",
      "columns": [
        {"name": "zip_code_prefix"}, {"name": "latitude"}, {"name": "longitude"},
        {"name": "city"}, {"name": "state"}
      ]
    },
    {
      "name": "analytics.v_delivery_performance",
      "comment": "Delivery performance. One row per order.",
      "columns": [
        {"name": "order_id"}, {"name": "order_status"},
        {"name": "order_purchase_timestamp"},
        {"name": "order_delivered_customer_date"},
        {"name": "order_estimated_delivery_date"},
        {"name": "delivery_days"}, {"name": "is_late"}
      ]
    }
  ]
}
```

---

## K. Sources

1. Kaggle – Brazilian E-Commerce Public Dataset by Olist: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
2. Olist Data Dictionary (community): https://github.com/theammarngp-makes/olist-sales-analysis/blob/main/docs/data_dictionary.md 
3. Olist Dataset Overview (RFM project): https://github.com/theammarngp-makes/ecommerce-rfm-customer-segmentation/blob/main/docs/08_Dataset_Overview.md 
4. SQL-Olist Database Creation (GitHub): https://github.com/M-Abdellah/SQL-Olist-Database-Creation-and-Data-Injection 
5. Stack Overflow – Geolocation ZIP prefix behavior: https://stackoverflow.com/questions/55201118/strange-behavior-of-query-in-pandas 
6. Community notebook – Customer ID vs. unique ID: https://raw.githubusercontent.com/rajtulluri/Olist-business-analysis/master/Notebooks/Data%20manipulation%20and%20combining.ipynb 
7. GitHub – Review_id conflict handling: https://github.com/dylanbarrett-analytics/olist-customer-satisfaction-analysis 
8. Community data dictionary (raw): https://raw.githubusercontent.com/theammarngp-makes/olist-sales-analysis/refs/heads/main/docs/data_dictionary.md 
