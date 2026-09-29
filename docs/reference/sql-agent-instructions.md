# Olist Brazilian E-Commerce Dataset: Technical Knowledge Base for an AI SQL Analyst

---

## 1. Dataset Overview

The **Brazilian E-Commerce Public Dataset by Olist** is a publicly available relational dataset hosted on Kaggle, containing anonymized commercial records of approximately 100,000 orders placed on the Olist marketplace between September 2016 and October 2018. Olist is a Brazilian e-commerce platform that connects small and medium-sized businesses to customers across Brazil.

The dataset is organized into **9 CSV files** that together form a relational schema. It covers orders from multiple dimensions: order status, price, payment, freight performance, customer location, product attributes, and customer reviews.

**Key dataset facts:**
- **Time horizon:** September 2016 – October 2018
- **Geographic coverage:** 27 Brazilian states across 4,119 municipalities
- **Total unique customers (by `customer_unique_id`):** 94,989
- **Total orders (by `order_id`):** 99,441
- **Total gross revenue analyzed:** R$ 15,737,667.52
- **Product categories:** 73 distinct categories in the products table; 71 translated categories in the translation table

**Source:** Kaggle, “Brazilian E-Commerce Public Dataset by Olist” — https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

---

## 2. Schema Reference

### 2.1 `olist_customers_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `customer_id` | string (PK) | Transient identifier generated **per order**. Key to the orders dataset. | Each order has a unique `customer_id`. Not a person-level key. |
| `customer_unique_id` | string | Persistent identifier representing the real human buyer. Generated at signup. | The correct key for customer-level analysis. |
| `customer_zip_code_prefix` | string/int | First 5 digits of the customer's ZIP code. | Approximate location, not exact address. |
| `customer_city` | string | City where the customer is located. | |
| `customer_state` | string | Brazilian state abbreviation (e.g., SP, RJ). | |

**Sources:**

### 2.2 `olist_geolocation_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `geolocation_zip_code_prefix` | string/int | First 5 digits of the ZIP code. | Multiple rows per ZIP prefix. |
| `geolocation_lat` | double | Latitude coordinate. | |
| `geolocation_lng` | double | Longitude coordinate. | |
| `geolocation_city` | string | City name. | |
| `geolocation_state` | string | State abbreviation. | |

**Sources:**

### 2.3 `olist_orders_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `order_id` | string (PK) | Unique order identifier. | |
| `customer_id` | string (FK) | Links to `olist_customers_dataset.customer_id`. | Per-order customer key. |
| `order_status` | string | Current status: delivered, shipped, canceled, unavailable, etc. | |
| `order_purchase_timestamp` | datetime | When the order was placed. | Primary timestamp for time-series analysis. |
| `order_approved_at` | datetime | Payment approval timestamp. | Can be NULL. |
| `order_delivered_carrier_date` | datetime | When the order was handed to the logistics carrier. | Can be NULL. |
| `order_delivered_customer_date` | datetime | Actual delivery date to the customer. | Can be NULL for undelivered orders. |
| `order_estimated_delivery_date` | datetime | Delivery date promised at purchase. | |

**Sources:**

### 2.4 `olist_order_items_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `order_id` | string (FK) | Links to `olist_orders_dataset.order_id`. | |
| `order_item_id` | int | Sequence number of the item within the order. | Composite PK with `order_id`. |
| `product_id` | string (FK) | Links to `olist_products_dataset.product_id`. | |
| `seller_id` | string (FK) | Links to `olist_sellers_dataset.seller_id`. | |
| `shipping_limit_date` | datetime | Seller's shipping deadline for transferring the order to the logistics partner. | |
| `price` | decimal | Item price (excludes freight). | |
| `freight_value` | decimal | Shipping cost for the item. | |

**Sources:**

### 2.5 `olist_order_payments_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `order_id` | string (FK) | Links to `olist_orders_dataset.order_id`. | |
| `payment_sequential` | int | Sequence number for multiple payments on one order. | Composite PK with `order_id`. |
| `payment_type` | string | credit_card, boleto, voucher, debit_card. | |
| `payment_installments` | int | Number of installments chosen by the customer. | |
| `payment_value` | decimal | Total amount paid for this payment record. | |

**Sources:**

### 2.6 `olist_products_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `product_id` | string (PK) | Unique product identifier. | |
| `product_category_name` | string | Category name in Portuguese. | Can be NULL. |
| `product_name_length` | int | Character count of product name. | CSV column is misspelled as `product_name_lenght`. |
| `product_description_length` | int | Character count of product description. | Misspelled as `product_description_lenght`. |
| `product_photos_qty` | int | Number of product photos. | |
| `product_weight_g` | int | Weight in grams. | |
| `product_length_cm` | int | Length in centimeters. | |
| `product_height_cm` | int | Height in centimeters. | |
| `product_width_cm` | int | Width in centimeters. | |

**Sources:**

### 2.7 `olist_sellers_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `seller_id` | string (PK) | Unique seller identifier. | |
| `seller_zip_code_prefix` | string/int | First 5 digits of the seller's ZIP code. | |
| `seller_city` | string | Seller's city. | |
| `seller_state` | string | Seller's state abbreviation. | |

**Sources:**

### 2.8 `product_category_name_translation`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `product_category_name` | string (PK) | Category name in Portuguese. | |
| `product_category_name_english` | string | English translation. | |

**Sources:**

### 2.9 `olist_order_reviews_dataset`

| Column | Type | Semantic meaning | Notes |
|---|---|---|---|
| `review_id` | string | Review identifier. | **Not unique** — can appear multiple times. |
| `order_id` | string (FK) | Links to `olist_orders_dataset.order_id`. | |
| `review_score` | int (1–5) | Customer satisfaction score. | |
| `review_comment_title` | string | Review title. | Nullable; most reviews have no title. |
| `review_comment_message` | string | Review body text. | Nullable. |
| `review_creation_date` | datetime | Date the satisfaction survey was sent to the customer. | |
| `review_answer_timestamp` | datetime | Timestamp when the customer answered the survey. | |

**Sources:**

---

## 3. Table Relationships and Cardinalities

### 3.1 Relationship map

```
customers (1) ──< (many) orders (1) ──< (many) order_items >── (1) products
                                           │
                                           └──< (many) payments
                                           └──< (0..1) order_reviews
                                           │
                                           └── (many) ── (1) sellers
```

### 3.2 Detailed relationships

| Relationship | Join columns | Cardinality | Notes |
|---|---|---|---|
| customers → orders | `customers.customer_id = orders.customer_id` | **1:many** | One customer_id appears in exactly one order (by construction). |
| orders → order_items | `orders.order_id = order_items.order_id` | **1:many** | One order can have multiple items. |
| order_items → products | `order_items.product_id = products.product_id` | **many:1** | Many items can reference the same product. |
| order_items → sellers | `order_items.seller_id = sellers.seller_id` | **many:1** | Many items can be sold by the same seller. |
| orders → payments | `orders.order_id = payments.order_id` | **1:many** | One order can have multiple payment records. |
| orders → reviews | `orders.order_id = reviews.order_id` | **1:0..1** (nominal) | In practice, some orders have multiple review rows. |
| products → category translation | `products.product_category_name = translation.product_category_name` | **many:1** | Some products have NULL category. |
| customers → geolocation | `customers.customer_zip_code_prefix = geolocation.geolocation_zip_code_prefix` | **many:many** | Geolocation has massive duplication per ZIP prefix. |
| sellers → geolocation | `sellers.seller_zip_code_prefix = geolocation.geolocation_zip_code_prefix` | **many:many** | Same duplication issue. |

**Source:** The relationship summary in the GitHub data dictionary describes: customers (1) → orders (many); orders (1) → order_items (many); orders (1) → payments (many); orders (1) → reviews (0..1); order_items → products (many-to-one); order_items → sellers (many-to-one).

### 3.3 The natural grain of the joined dataset

The natural grain of a fully joined dataset is **one row per order item**. An order with 3 products yields 3 rows. Any revenue KPI must sum, not count rows, to avoid overstating order counts.

---

## 4. Business Semantics

| Concept | Meaning |
|---|---|
| **Order** | A customer's purchase transaction. Identified by `order_id`. |
| **Order item** | A single product within an order. An order can contain multiple items. Identified by `(order_id, order_item_id)`. |
| **Payment** | A payment record for an order. An order can be paid with multiple methods or in installments, generating multiple rows. |
| **Installment** | Number of installments chosen for a payment (`payment_installments`). |
| **Review** | Customer satisfaction survey response, scored 1–5. |
| **Customer** | The buyer. `customer_id` is per-order; `customer_unique_id` is the persistent person identifier. |
| **Seller** | The merchant who listed and sold the product. |
| **Product** | An item listed for sale, with a Portuguese category name and physical attributes. |
| **Freight** | Shipping cost associated with an order item (`freight_value`). |
| **Delivery** | The act of the order reaching the customer (`order_delivered_customer_date`). |
| **Estimated delivery** | The date promised at purchase (`order_estimated_delivery_date`). |
| **Purchase timestamp** | When the order was placed (`order_purchase_timestamp`). |
| **Approval timestamp** | When payment was approved (`order_approved_at`). |
| **Shipping timestamp** | When the order was handed to the carrier (`order_delivered_carrier_date`). |
| **Delivery timestamp** | When the customer received the order (`order_delivered_customer_date`). |

**Sources:**

---

## 5. Known Data Quirks and SQL Traps

### 5.1 Duplicate `review_id` values

The reviews dataset contains duplicate `review_id` values. A known discrepancy: **one order ID can be tagged to multiple reviews** even when there is only one product or item purchased, and **a common review ID can be tagged to multiple order IDs**. Analysis found that 8,545 records have duplicated `order_id`s in the reviews table, indicating that many reviews are part of multi-item orders. The percentage of redundant order IDs in the reviews table is approximately 0.55%.

**Implication for SQL:** Do not assume `review_id` is unique. Do not join reviews to orders on `order_id` alone if you need one row per order — pre-aggregate reviews or use `ROW_NUMBER()` to select the latest review per order.

### 5.2 `customer_id` vs `customer_unique_id`

This is the single most important gotcha in the dataset. `customer_id` is **minted fresh for every order**. Grouping orders by `customer_id` will make every customer appear as a one-time buyer, producing a repeat-purchase rate of 0.00%. The actual person, stable across orders, is `customer_unique_id`.

The customers table has exactly one row per `customer_id`: 99,441 rows, 99,441 IDs. The number of unique `customer_unique_id` values is 94,989.

### 5.3 Multiple payment records per order

An order can have multiple payment records (e.g., split payments, vouchers, installments). The `payment_sequential` column indicates the order of multiple payments. There are 103,886 payment records for 99,441 orders, meaning approximately 2.97% of orders have redundant payment records.

**Trap:** Joining `order_items` and `payments` directly on `order_id` creates a Cartesian fan-out. If an order has 3 items and 2 payment records, a direct join produces 6 rows instead of 3 or 2. Revenue will be dramatically inflated. The canonical fix is to pre-aggregate payments at the order level using a CTE or subquery before joining.

### 5.4 Multiple items per order

Orders can contain multiple items. The percentage of redundant order IDs in the order items table is approximately 9.97%. The natural grain of the joined dataset is one row per order item. Counting rows will overstate the number of orders. Always use `COUNT(DISTINCT order_id)` when counting orders from a joined dataset.

### 5.5 Geolocation table duplication

The geolocation dataset contains massive duplication. One analysis reports **261,831 duplicates** in the geolocation table. There are approximately 323,000 latitude/longitude coordinates related to ZIP code prefixes. One ZIP code can have multiple GPS coordinates, causing a Cartesian fan-out if joined directly to customers or sellers.

**Trap:** A direct join between customers and geolocation on ZIP code prefix will multiply rows. Always pre-aggregate geolocation (e.g., `SELECT geolocation_zip_code_prefix, AVG(geolocation_lat), AVG(geolocation_lng) ... GROUP BY 1`) before joining.

### 5.6 Missing translations

The products table has 73 distinct product categories, but the translation table contains only 71 rows. Two product categories have no English translation. Analysts should use a `LEFT JOIN` and handle NULL English names gracefully (e.g., fall back to the Portuguese name).

### 5.7 NULL values in timestamps

`order_approved_at` has NULL values (approximately 177 rows). `order_delivered_carrier_date` has approximately 2,086 NULLs. `order_delivered_customer_date` has approximately 3,421 NULLs (orders not yet delivered or canceled). `order_estimated_delivery_date` has zero NULLs.

### 5.8 Order status values

Order status values include: `delivered`, `shipped`, `canceled`, `unavailable`, `invoiced`, `processing`, `created`, `approved`. Canceled and unavailable orders make up only a small part of the dataset (approximately 625 canceled and 609 unavailable orders).

### 5.9 Date format inconsistencies

Date format inconsistencies have been reported in order items. Some timestamps may be stored as strings rather than native datetime types depending on how the CSV is loaded.

### 5.10 Anomalous payment values

An outlier with `payment_value` > 12,000 has been reported in the order payments dataset.

---

## 6. Correct Aggregation Rules

| Metric | Table(s) | Correct approach | Trap |
|---|---|---|---|
| **Number of orders** | `orders` | `COUNT(DISTINCT order_id)` | Do not count rows from joined tables. |
| **Number of unique customers** | `customers` | `COUNT(DISTINCT customer_unique_id)` | Using `customer_id` gives 99,441, which is the number of orders, not customers. |
| **Number of unique sellers** | `sellers` or `order_items` | `COUNT(DISTINCT seller_id)` | |
| **Number of products** | `products` | `COUNT(DISTINCT product_id)` | |
| **Total sales/revenue** | `order_payments` | `SUM(payment_value)` | `payment_value` summed per order is the canonical monetary value, not `price + freight_value`. It includes installment financing markup that item prices do not reflect. |
| **Average order value (AOV)** | `order_payments` | `SUM(payment_value) / COUNT(DISTINCT order_id)` | Ensure payment is pre-aggregated at order level. |
| **Average order items per order** | `order_items` | `COUNT(*) / COUNT(DISTINCT order_id)` | |
| **Total freight** | `order_items` | `SUM(freight_value)` | |
| **Average review score** | `order_reviews` | `AVG(review_score)` | Be careful with duplicate reviews; consider de-duplicating per order. |
| **Delivery time** | `orders` | `AVG(order_delivered_customer_date - order_purchase_timestamp)` | Use appropriate date functions for your SQL dialect. |
| **Late delivery rate** | `orders` | `COUNT(CASE WHEN order_delivered_customer_date > order_estimated_delivery_date THEN 1 END) / COUNT(*)` | Only for orders where both dates are non-NULL. |
| **Cancellation rate** | `orders` | `COUNT(CASE WHEN order_status = 'canceled' THEN 1 END) / COUNT(*)` | |
| **Payment totals** | `order_payments` | `SUM(payment_value)` | Pre-aggregate by `order_id` if joining with other tables. |

**Sources:**

---

## 7. Date/Time Semantics

| Timestamp | Event | Use for |
|---|---|---|
| `order_purchase_timestamp` | Order placed | Time-series trends, monthly/weekly grouping, recency analysis. |
| `order_approved_at` | Payment approved | Payment processing time analysis. |
| `order_delivered_carrier_date` | Handed to carrier | Seller fulfillment speed (from purchase to carrier handoff). |
| `order_delivered_customer_date` | Actual delivery to customer | Delivery duration, late delivery analysis. |
| `order_estimated_delivery_date` | Promised delivery date | Comparison against actual delivery to determine lateness. |

**Delivery duration** is calculated as `order_delivered_customer_date - order_purchase_timestamp`. Alternatively, `order_delivered_customer_date - order_delivered_carrier_date` measures carrier transit time.

**Late delivery** is determined by `order_delivered_customer_date > order_estimated_delivery_date`. The difference in days is a common metric.

**Monthly trends** should be grouped by `DATE_TRUNC('month', order_purchase_timestamp)` or equivalent.

**Sources:**

---

## 8. Customer Identity Rules

| Use case | Correct key | Reason |
|---|---|---|
| Counting orders | `order_id` | Each order has a unique order_id. |
| Counting unique customers | `customer_unique_id` | `customer_id` is per-order; counting it gives the number of orders. |
| Repeat-purchase analysis | `customer_unique_id` | Group by `customer_unique_id` to count orders per person. |
| Customer lifetime value | `customer_unique_id` | Aggregate all orders for the same person. |
| Customer-level aggregation | `customer_unique_id` | `customer_id` will fragment a single person’s history across multiple rows. |

Grouping by `customer_id` will incorrectly treat every order as a brand-new customer (Frequency = 1). RFM metrics **must** group by `customer_unique_id` to accurately calculate multi-order frequency.

**Sources:**

---

## 9. Revenue/Sales Rules

- **`price`** is the item price, excluding freight. It is at the order-item grain.
- **`freight_value`** is the shipping cost for the item, also at the order-item grain.
- **`payment_value`** is the total amount paid for a payment record, at the payment grain. Summed per order, it is the **canonical monetary value** for downstream RFM and CLTV features — not `price + freight_value`. `payment_value` on a long installment plan includes interest that the item price never reflects.
- **`payment_installments`** is the number of installments chosen by the customer. It does not affect the total `payment_value` (which includes any interest) but is a useful categorical field.

**Double-counting warning:** Joining `order_items` and `order_payments` directly on `order_id` creates a Cartesian fan-out. If an order has 3 items and 2 payment records, a direct join produces 6 rows. Revenue will be inflated. Always pre-aggregate one side of the join (usually payments) at the order level before joining.

---

## 10. Review Rules

- **Relationship to orders:** Each review is linked to an order via `order_id`. Nominally 1:0..1, but in practice some orders have multiple review rows.
- **Duplicate reviews:** `review_id` is **not unique**. One order can have multiple reviews, and one review_id can be associated with multiple order_ids. Approximately 0.55% of orders have redundant review records.
- **`review_score`** is an integer from 1 to 5. The average review score across the dataset is approximately 4.09.
- **`review_creation_date`** is the date the satisfaction survey was sent to the customer. **`review_answer_timestamp`** is when the customer answered the survey.
- **Aggregation:** When computing average review score per order, de-duplicate reviews first. When computing average review score per category, join through order_items to products.

**Sources:**

---

## 11. Product/Category Rules

- **`product_category_name`** is the Portuguese category name. It can be NULL for some products.
- **`product_category_name_english`** is the English translation, stored in a separate table (`product_category_name_translation`).
- **Join key:** `products.product_category_name = product_category_name_translation.product_category_name`.
- **Coverage:** There are 73 distinct Portuguese category names but only 71 translations. Two categories have no English translation. Use a `LEFT JOIN` and fall back to the Portuguese name when the English translation is NULL.
- **Analysts should not invent translations** for missing categories. Display the Portuguese name or label it as “Unknown”.

---

## 12. Geographic Rules

- **Customer ZIP prefix** (`customer_zip_code_prefix`) and **seller ZIP prefix** (`seller_zip_code_prefix`) are the first 5 digits of Brazilian ZIP codes.
- **Geolocation table** (`olist_geolocation_dataset`) maps ZIP prefixes to latitude/longitude coordinates, city, and state.
- **Critical caveat:** The geolocation table has massive duplication. One ZIP prefix can have multiple coordinate pairs (approximately 261,831 duplicate rows). Joining customers or sellers directly to geolocation on ZIP prefix will cause a Cartesian fan-out, inflating row counts and distorting aggregations.
- **Correct approach:** Pre-aggregate geolocation by ZIP prefix (e.g., `SELECT geolocation_zip_code_prefix, AVG(geolocation_lat), AVG(geolocation_lng), MIN(geolocation_state), MIN(geolocation_city) GROUP BY 1`) before joining.

---

## 13. Example Natural-Language Questions and How They Map to SQL

| User question | Tables | Join / aggregation | Key rules |
|---|---|---|---|
| “How many orders did we have?” | `orders` | `COUNT(DISTINCT order_id)` | |
| “What were the top-selling product categories?” | `order_items` → `products` → `translation` | `GROUP BY product_category_name_english`, `COUNT(*)` or `SUM(price)` | Use LEFT JOIN for missing translations. |
| “What is the average order value?” | `order_payments` | Pre-aggregate `SUM(payment_value)` by `order_id`, then `AVG()` | Do not join to order_items without pre-aggregation. |
| “Which sellers generated the most revenue?” | `order_items` → `sellers` | `GROUP BY seller_id`, `SUM(price + freight_value)` | Revenue at item level; do not join payments. |
| “How many customers placed more than one order?” | `customers` → `orders` | Group by `customer_unique_id`, `HAVING COUNT(DISTINCT order_id) > 1` | **Must** use `customer_unique_id`, not `customer_id`. |
| “What percentage of orders were delivered late?” | `orders` | `COUNT(CASE WHEN order_delivered_customer_date > order_estimated_delivery_date THEN 1 END) / COUNT(*)` | Filter to delivered orders with non-NULL delivery dates. |
| “What payment methods were most common?” | `order_payments` | `GROUP BY payment_type`, `COUNT(DISTINCT order_id)` | |
| “What was the average review score by category?” | `order_reviews` → `order_items` → `products` → `translation` | De-duplicate reviews per order, `GROUP BY category`, `AVG(review_score)` | |
| “Which states generated the most sales?” | `orders` → `customers` | `GROUP BY customer_state`, `SUM(payment_value)` via pre-aggregated payments | Use `customer_unique_id` for customer-level analysis. |
| “How many orders were cancelled?” | `orders` | `COUNT(CASE WHEN order_status = 'canceled' THEN 1 END)` | |
| “What is the monthly revenue trend?” | `orders` → `order_payments` | `DATE_TRUNC('month', order_purchase_timestamp)`, `SUM(payment_value)` | Pre-aggregate payments by order. |

---

## 14. Agent-Specific Rules / Instructions

1. **Always use `COUNT(DISTINCT order_id)`** when counting orders from any joined table.
2. **Never use `customer_id` for customer-level aggregation.** Use `customer_unique_id` for repeat-purchase, CLTV, and customer-count metrics.
3. **Never join `order_items` and `order_payments` directly.** Pre-aggregate payments at the order level in a CTE or subquery first.
4. **Never join `customers` or `sellers` directly to `geolocation`.** Pre-aggregate geolocation by ZIP prefix first.
5. **`payment_value` is the canonical revenue metric.** Do not use `price + freight_value` as a substitute for total revenue.
6. **Reviews can have duplicate `review_id` and duplicate `order_id`.** De-duplicate per order if you need one row per order.
7. **Two product categories have no English translation.** Use a LEFT JOIN and fall back to the Portuguese name.
8. **Use `order_purchase_timestamp` for time-series analysis.** Use `order_delivered_customer_date > order_estimated_delivery_date` for late delivery.
9. **Order status values include `canceled` and `unavailable`.** Exclude these when analyzing fulfilled orders if appropriate.
10. **The dataset covers 2016–2018 only.** Do not extrapolate trends beyond this window.

---

## 15. Sources

| # | Source | URL |
|---|---|---|
| 1 | Kaggle — Brazilian E-Commerce Public Dataset by Olist | https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce |
| 2 | GitHub — Olist Sales Analysis Data Dictionary | https://github.com/theammarngp-makes/olist-sales-analysis/blob/main/docs/data_dictionary.md |
| 3 | GitHub — E-commerce RFM Customer Segmentation Dataset Overview | https://github.com/theammarngp-makes/ecommerce-rfm-customer-segmentation/blob/main/docs/08_Dataset_Overview.md |
| 4 | DEV Community — The customer_id that isn’t a customer | https://dev.to/gaur_data_c6511af396c4bce/the-customerid-that-isnt-a-customer-3a00 |
| 5 | NCI Library — Data Preprocessing PDF | https://norma.ncirl.ie/6100/1/pratikshaarvindchate.pdf |
| 6 | GitHub — Olist Data Dictionary (Chinese) | https://github.com/fengeer97-debug/DataWarehouse/blob/main/ecommerce_warehouse/data/raw/olist/Olist_数据字典_中文.pdf |
| 7 | DeepWiki — Revenue Analytics Queries | https://deepwiki.com/codecsrayo/proyecto_integrador_IV |
| 8 | LinkedIn — Data Modeling Error Cost Me $2M | https://www.linkedin.com/posts/prajna-p- |
| 9 | Medium — 5 Mistakes That Cost Me Hours | https://medium.com/ |
| 10 | Medium — Modeling ~100,000 E-Commerce Orders | https://medium.com/ |
| 11 | arXiv — StarQA: Question Answering Dataset | https://arxiv.org/ |
| 12 | Hugging Face — Olist text-to-SQL dataset | https://huggingface.co/datasets/ |
| 13 | Codeberg — Retail Customer Lifecycle Analytics | https://codeberg.org/ |

---

## Critical Knowledge the SQL Agent Must Know Before Writing SQL

1. **`customer_id` is per-order, not per-person.** The persistent customer key is `customer_unique_id`. Grouping by `customer_id` makes every customer look like a one-time buyer and produces a repeat-purchase rate of 0.00%.

2. **`review_id` is not unique.** The reviews table contains duplicate `review_id` values, and some orders have multiple review rows. De-duplicate per order before aggregating review metrics.

3. **Never join `order_items` and `order_payments` directly.** This creates a Cartesian fan-out that inflates revenue. Pre-aggregate payments at the `order_id` level in a CTE first.

4. **Never join customers or sellers directly to geolocation.** The geolocation table has ~261,831 duplicate rows per ZIP prefix. Pre-aggregate by ZIP prefix before joining.

5. **`payment_value` summed per order is the canonical revenue metric.** Do not use `price + freight_value` as a substitute — `payment_value` includes installment financing markup that item prices do not reflect.

6. **The natural grain of a fully joined dataset is one row per order item.** Always use `COUNT(DISTINCT order_id)` when counting orders from a joined dataset.

7. **Two product categories have no English translation.** Use a `LEFT JOIN` to the translation table and fall back to the Portuguese category name.

8. **Use `order_purchase_timestamp` for time-series grouping.** Use `order_delivered_customer_date > order_estimated_delivery_date` for late delivery. `order_approved_at` has NULLs; `order_delivered_customer_date` has NULLs for undelivered/canceled orders.

9. **Order status includes `canceled` and `unavailable`.** Exclude these when analyzing fulfilled orders if the question implies completed transactions.

10. **The dataset covers only 2016–2018.** Do not extrapolate trends beyond this window.
