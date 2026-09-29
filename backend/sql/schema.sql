-- AI SQL Analyst — physical schema for the Olist Brazilian E-Commerce dataset.
-- Source-faithful: table and column names mirror the raw CSV files, including
-- the preserved source typos product_name_lenght and product_description_lenght.
-- Non-destructive: this file only creates objects (no DROP statements).

-- ============================================================
-- Independent dimension tables
-- ============================================================

CREATE TABLE customers (
    customer_id              text NOT NULL,
    customer_unique_id       text NOT NULL,
    customer_zip_code_prefix text NOT NULL,
    customer_city            text NOT NULL,
    customer_state           text NOT NULL,
    CONSTRAINT pk_customers PRIMARY KEY (customer_id)
);

COMMENT ON TABLE customers IS
    'One row per customer_id. customer_id is minted per order; '
    'customer_unique_id is the persistent identity of the real buyer '
    'and is the correct key for customer-level analysis.';

COMMENT ON COLUMN customers.customer_id IS
    'Per-order identifier. Grouping by this treats every order as a new customer.';
COMMENT ON COLUMN customers.customer_unique_id IS
    'Persistent buyer identifier that repeats across orders for the same person.';
COMMENT ON COLUMN customers.customer_zip_code_prefix IS
    '5-digit Brazilian ZIP prefix stored as text to preserve leading zeroes.';


CREATE TABLE sellers (
    seller_id              text NOT NULL,
    seller_zip_code_prefix text NOT NULL,
    seller_city            text NOT NULL,
    seller_state           text NOT NULL,
    CONSTRAINT pk_sellers PRIMARY KEY (seller_id)
);

COMMENT ON TABLE sellers IS
    'One row per seller_id.';
COMMENT ON COLUMN sellers.seller_zip_code_prefix IS
    '5-digit Brazilian ZIP prefix stored as text to preserve leading zeroes.';


CREATE TABLE products (
    product_id                text NOT NULL,
    product_category_name     text,
    product_name_lenght       integer,
    product_description_lenght integer,
    product_photos_qty        integer,
    product_weight_g          integer,
    product_length_cm         integer,
    product_height_cm         integer,
    product_width_cm          integer,
    CONSTRAINT pk_products PRIMARY KEY (product_id)
);

COMMENT ON TABLE products IS
    'One row per product_id.';
COMMENT ON COLUMN products.product_name_lenght IS
    'Source CSV column name preserved verbatim (sic: "lenght").';
COMMENT ON COLUMN products.product_description_lenght IS
    'Source CSV column name preserved verbatim (sic: "lenght").';
COMMENT ON COLUMN products.product_category_name IS
    'Portuguese category name. Not foreign-keyed to the translation table: '
    '2 of the 73 product categories have no matching translation row.';


CREATE TABLE product_category_translation (
    product_category_name         text NOT NULL,
    product_category_name_english text NOT NULL,
    CONSTRAINT pk_product_category_translation PRIMARY KEY (product_category_name)
);

COMMENT ON TABLE product_category_translation IS
    'Portuguese-to-English category lookup. Contains 71 rows and does not cover '
    'all 73 product categories, so joins from products must tolerate missing rows.';


CREATE TABLE geolocation (
    geolocation_id             bigint GENERATED ALWAYS AS IDENTITY,
    geolocation_zip_code_prefix text NOT NULL,
    geolocation_lat             numeric NOT NULL,
    geolocation_lng             numeric NOT NULL,
    geolocation_city            text NOT NULL,
    geolocation_state           text NOT NULL,
    CONSTRAINT pk_geolocation PRIMARY KEY (geolocation_id)
);

COMMENT ON TABLE geolocation IS
    'One row per source geolocation record. A single ZIP prefix can map to many '
    'rows, so joining directly to customers/sellers multiplies rows; aggregate by '
    'ZIP prefix first. The surrogate geolocation_id exists because the natural '
    'ZIP/coordinate tuples contain duplicates.';
COMMENT ON COLUMN geolocation.geolocation_zip_code_prefix IS
    '5-digit Brazilian ZIP prefix stored as text to preserve leading zeroes. '
    'Not unique — multiple coordinate points share the same prefix.';

-- ============================================================
-- Order tables
-- ============================================================

CREATE TABLE orders (
    order_id                      text NOT NULL,
    customer_id                   text NOT NULL,
    order_status                  text NOT NULL,
    order_purchase_timestamp      timestamp NOT NULL,
    order_approved_at             timestamp,
    order_delivered_carrier_date  timestamp,
    order_delivered_customer_date timestamp,
    order_estimated_delivery_date timestamp NOT NULL,
    CONSTRAINT pk_orders PRIMARY KEY (order_id),
    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT chk_orders_order_status CHECK (order_status IN (
        'approved', 'canceled', 'created', 'delivered',
        'invoiced', 'processing', 'shipped', 'unavailable'
    ))
);

COMMENT ON TABLE orders IS
    'One row per order_id. All timestamps are naive (no timezone); the source '
    'timezone is unknown.';
COMMENT ON COLUMN orders.order_purchase_timestamp IS
    'Naive timestamp (no timezone).';
COMMENT ON COLUMN orders.order_approved_at IS
    'Nullable: empty for some orders in the source data.';
COMMENT ON COLUMN orders.order_delivered_carrier_date IS
    'Nullable: empty for some orders in the source data.';
COMMENT ON COLUMN orders.order_delivered_customer_date IS
    'Nullable: empty for some orders in the source data.';


CREATE TABLE order_items (
    order_id            text NOT NULL,
    order_item_id       integer NOT NULL,
    product_id          text NOT NULL,
    seller_id           text NOT NULL,
    shipping_limit_date timestamp NOT NULL,
    price               numeric(12,2) NOT NULL,
    freight_value       numeric(12,2) NOT NULL,
    CONSTRAINT pk_order_items PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT fk_order_items_seller
        FOREIGN KEY (seller_id) REFERENCES sellers (seller_id),
    CONSTRAINT chk_order_items_price CHECK (price >= 0),
    CONSTRAINT chk_order_items_freight_value CHECK (freight_value >= 0)
);

COMMENT ON TABLE order_items IS
    'One row per (order_id, order_item_id). An order can have multiple items, so '
    'joining to orders duplicates order-level values and inflates row-count '
    'aggregates; use COUNT(DISTINCT order_id) for order counts.';
COMMENT ON COLUMN order_items.order_item_id IS
    'Sequence number within an order; not globally unique.';


CREATE TABLE order_payments (
    order_id             text NOT NULL,
    payment_sequential   integer NOT NULL,
    payment_type         text NOT NULL,
    payment_installments integer NOT NULL,
    payment_value        numeric(12,2) NOT NULL,
    CONSTRAINT pk_order_payments PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT fk_order_payments_order
        FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT chk_order_payments_payment_type CHECK (payment_type IN (
        'boleto', 'credit_card', 'debit_card', 'not_defined', 'voucher'
    )),
    CONSTRAINT chk_order_payments_payment_installments
        CHECK (payment_installments >= 0),
    CONSTRAINT chk_order_payments_payment_value CHECK (payment_value >= 0)
);

COMMENT ON TABLE order_payments IS
    'One row per (order_id, payment_sequential). An order can have multiple '
    'payment records; joining this table directly to order_items on order_id '
    'produces a fan-out and overstates monetary sums.';
COMMENT ON COLUMN order_payments.payment_sequential IS
    'Sequence number for multiple payment records within one order.';
COMMENT ON COLUMN order_payments.payment_value IS
    'Amount recorded at payment grain (per payment record).';


CREATE TABLE order_reviews (
    review_id               text NOT NULL,
    order_id                text NOT NULL,
    review_score            smallint NOT NULL,
    review_comment_title    text,
    review_comment_message  text,
    review_creation_date    timestamp NOT NULL,
    review_answer_timestamp timestamp NOT NULL,
    CONSTRAINT pk_order_reviews PRIMARY KEY (review_id, order_id),
    CONSTRAINT fk_order_reviews_order
        FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT chk_order_reviews_review_score CHECK (review_score BETWEEN 1 AND 5)
);

COMMENT ON TABLE order_reviews IS
    'One row per (review_id, order_id). An order can have more than one review '
    'row and review_id is not unique on its own, so both columns form the key.';
COMMENT ON COLUMN order_reviews.review_id IS
    'Not unique alone; combine with order_id.';
COMMENT ON COLUMN order_reviews.review_score IS
    'Integer score from 1 to 5.';

-- ============================================================
-- Initial indexes
-- ============================================================

CREATE INDEX idx_orders_customer_id
    ON orders (customer_id);
CREATE INDEX idx_orders_order_purchase_timestamp
    ON orders (order_purchase_timestamp);
CREATE INDEX idx_order_items_product_id
    ON order_items (product_id);
CREATE INDEX idx_order_items_seller_id
    ON order_items (seller_id);
CREATE INDEX idx_order_reviews_order_id
    ON order_reviews (order_id);
