# Databricks notebook source
# MAGIC %md
# MAGIC # Lamp Ops demo data
# MAGIC Sets up every catalog in the `catalogs` job parameter in one run, so the job starts
# MAGIC compute only once. Re-running replaces only the synthetic orders table and the two
# MAGIC demo metric views. SQL creates missing catalogs and schemas: it uses the account's
# MAGIC Default Storage, which the catalogs API (and therefore DAB catalog resources) cannot.

# COMMAND ----------

dbutils.widgets.text("catalogs", "lamp_ops_dev,lamp_ops_prod")
dbutils.widgets.text("schema", "base")
catalogs = [name.strip() for name in dbutils.widgets.get("catalogs").split(",") if name.strip()]
schema = dbutils.widgets.get("schema")
if not catalogs:
    raise ValueError("Set the catalogs job parameter")

# COMMAND ----------

ORDERS = """
CREATE OR REPLACE TABLE orders
USING DELTA
COMMENT 'Synthetic sales only; not real customers or transactions.'
AS
SELECT
  id AS order_id,
  date_sub(current_date(), 89 - CAST(id % 90 AS INT)) AS order_date,
  CASE WHEN (id DIV 90) % 10 < 6 THEN 'Europe'
       WHEN (id DIV 90) % 10 < 9 THEN 'Americas' ELSE 'APAC' END AS region,
  CASE WHEN id % 7 < 4 THEN 'Lamp'
       WHEN id % 7 < 6 THEN 'Bulb' ELSE 'Shade' END AS product,
  CAST(1 + id % 4 AS INT) AS quantity,
  CAST(
    (1 + id % 4)
    * CASE WHEN id % 7 < 4 THEN 45 WHEN id % 7 < 6 THEN 8 ELSE 18 END
    * (1 + (id % 90) / 180.0)
    * CASE WHEN id % 90 BETWEEN 55 AND 65 AND (id DIV 90) % 10 < 6
           THEN 0.5 ELSE 1 END
    AS DECIMAL(12, 2)
  ) AS revenue
FROM range(900)
"""

SALES_METRICS = """
CREATE OR REPLACE VIEW sales_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: orders
comment: "Synthetic sales metrics in USD; one source row per order."
dimensions:
  - name: order_date
    expr: order_date
  - name: region
    expr: region
  - name: product
    expr: product
measures:
  - name: total_revenue
    expr: SUM(revenue)
    comment: "Total order revenue in USD."
  - name: order_count
    expr: COUNT(*)
    comment: "Number of orders."
  - name: average_order_value
    expr: SUM(revenue) / COUNT(*)
    comment: "Revenue in USD divided by order count at the selected grain."
$$
"""

PRODUCT_METRICS = """
CREATE OR REPLACE VIEW product_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: orders
comment: "Synthetic product revenue and unit sales."
dimensions:
  - name: order_date
    expr: order_date
  - name: product
    expr: product
measures:
  - name: total_revenue
    expr: SUM(revenue)
  - name: units_sold
    expr: SUM(quantity)
$$
"""

CHECKS = [
    """
    SELECT assert_true(COUNT(*) = 900, 'Expected 900 demo orders') FROM orders
    """,
    """
    SELECT assert_true(MEASURE(order_count) = 900, 'Unexpected metric order count'),
           assert_true(MEASURE(total_revenue) > 0, 'Expected positive revenue')
    FROM sales_metrics
    """,
    """
    SELECT assert_true(MEASURE(units_sold) = 2250, 'Unexpected unit count')
    FROM product_metrics
    """,
]

# COMMAND ----------

for catalog in catalogs:
    spark.sql(
        "CREATE CATALOG IF NOT EXISTS IDENTIFIER(:catalog) "
        "COMMENT 'Synthetic data for the Lamp Ops demo.'",
        args={"catalog": catalog},
    )
    spark.sql(
        "CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:schema) "
        "COMMENT 'Orders and metric views for the Lamp Ops demo.'",
        args={"schema": f"{catalog}.{schema}"},
    )
    # Unqualified names below, including the metric views' `source: orders`, use this scope.
    spark.sql("USE CATALOG IDENTIFIER(:catalog)", args={"catalog": catalog})
    spark.sql("USE SCHEMA IDENTIFIER(:schema)", args={"schema": schema})
    for statement in (ORDERS, SALES_METRICS, PRODUCT_METRICS, *CHECKS):
        spark.sql(statement).collect()
    print(f"Set up {catalog}.{schema}")
