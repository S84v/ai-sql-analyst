# Data

## Purpose

AI SQL Analyst uses the official **Brazilian E-Commerce Public Dataset by Olist**
as its real-world relational dataset for natural-language analytical queries.

## Source and attribution

- Dataset: *Brazilian E-Commerce Public Dataset by Olist*
- Provided by Olist, published on Kaggle:
  <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce>
- Licensed under
  [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
- This repository does **not** redistribute the raw dataset. Please download it
  yourself using the steps below.

## Expected files

The dataset consists of 9 CSV files. After extraction, `data/raw/` should
contain exactly these files:

- `olist_customers_dataset.csv`
- `olist_geolocation_dataset.csv`
- `olist_order_items_dataset.csv`
- `olist_order_payments_dataset.csv`
- `olist_order_reviews_dataset.csv`
- `olist_orders_dataset.csv`
- `olist_products_dataset.csv`
- `olist_sellers_dataset.csv`
- `product_category_name_translation.csv`

## Local layout

Place the downloaded CSV files directly under `data/raw/`:

```text
data/
└── raw/
    ├── olist_customers_dataset.csv
    ├── olist_geolocation_dataset.csv
    ├── olist_order_items_dataset.csv
    ├── olist_order_payments_dataset.csv
    ├── olist_order_reviews_dataset.csv
    ├── olist_orders_dataset.csv
    ├── olist_products_dataset.csv
    ├── olist_sellers_dataset.csv
    └── product_category_name_translation.csv
```

The contents of `data/raw/` are gitignored (only `.gitkeep` is tracked), so raw
dataset files are never committed to this repository.

## Acquisition

No Kaggle CLI or API credentials are required.

1. Open the official Kaggle dataset page:
   <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce>
2. Download the dataset.
3. Extract the CSV files from the downloaded archive.
4. Place the CSV files directly under `data/raw/`.

## Important dataset notes

- The dataset contains approximately 100,000 orders.
- Coverage spans roughly 2016–2018.
- An order may contain multiple items.
- `customer_id` and `customer_unique_id` have different semantics:
  `customer_id` is associated with an order, while `customer_unique_id`
  identifies the same customer across orders. Do not treat them as
  interchangeable.
- Relational joins can change row cardinality and inflate aggregates if handled
  incorrectly (for example, joining one order to its multiple items).
