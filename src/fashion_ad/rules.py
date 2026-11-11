"""Declarative data integrity and data quality rules, evaluated on the bronze layer.

Row-level rules give a SQL predicate over alias `t` that is TRUE for a violating row.
Table-level rules give a scalar query returning the number of violations.
`error` rows are quarantined and never reach silver; `warn` rows pass through."""
from dataclasses import dataclass

from .generate import CATEGORIES, SEASONS, VALID_SIZES


@dataclass(frozen=True)
class Rule:
    name: str
    category: str  # integrity | quality
    severity: str  # error | warn
    table: str
    description: str
    predicate: str = ""
    scalar: str = ""


def _dup(table, key):
    return (f"t._row_id IN (SELECT _row_id FROM (SELECT _row_id, row_number() OVER "
            f"(PARTITION BY {key} ORDER BY _row_id) rn FROM {table}) WHERE rn > 1)")


def _fk(col, ref_table):
    return f"t.{col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {ref_table} r WHERE r.{col} = t.{col})"


def _set(values):
    return ", ".join(f"'{v}'" for v in values)


S, P, ST, I = "bronze_sales", "bronze_products", "bronze_stores", "bronze_inventory"

RULES = [
    # ---- integrity ----
    Rule("sales_pk_unique", "integrity", "error", S, "sale_id is unique", _dup(S, "sale_id")),
    Rule("sales_store_not_null", "integrity", "error", S, "store_id is present", "t.store_id IS NULL"),
    Rule("sales_store_fk", "integrity", "error", S, "store_id exists in stores", _fk("store_id", ST)),
    Rule("sales_product_fk", "integrity", "error", S, "product_id exists in products", _fk("product_id", P)),
    Rule("products_pk_unique", "integrity", "error", P, "product_id is unique", _dup(P, "product_id")),
    Rule("stores_pk_unique", "integrity", "error", ST, "store_id is unique", _dup(ST, "store_id")),
    Rule("inventory_store_fk", "integrity", "error", I, "store_id exists in stores", _fk("store_id", ST)),
    Rule("inventory_product_fk", "integrity", "error", I, "product_id exists in products", _fk("product_id", P)),
    # ---- quality ----
    Rule("sales_quantity_positive", "quality", "error", S, "quantity > 0", "t.quantity <= 0"),
    Rule("sales_price_positive", "quality", "error", S, "unit_price > 0", "t.unit_price <= 0"),
    Rule("sales_discount_range", "quality", "error", S, "discount_pct between 0 and 100",
         "t.discount_pct NOT BETWEEN 0 AND 100"),
    Rule("sales_size_valid", "quality", "error", S, "size is a known size", f"t.size NOT IN ({_set(VALID_SIZES)})"),
    Rule("sales_date_not_future", "quality", "error", S, "order date is not in the future",
         "t.order_ts::DATE > current_date"),
    Rule("sales_price_within_list", "quality", "warn", S, "unit_price does not exceed list price",
         f"EXISTS (SELECT 1 FROM {P} r WHERE r.product_id = t.product_id AND t.unit_price > r.list_price)"),
    Rule("products_price_valid", "quality", "error", P, "list_price is present and > 0",
         "t.list_price IS NULL OR t.list_price <= 0"),
    Rule("products_category_valid", "quality", "error", P, "category is a known category",
         f"t.category NOT IN ({_set(CATEGORIES)})"),
    Rule("products_season_valid", "quality", "error", P, "season is a known season",
         f"t.season NOT IN ({_set(SEASONS)})"),
    Rule("inventory_stock_non_negative", "quality", "error", I, "stock_qty >= 0", "t.stock_qty < 0"),
    Rule("sales_freshness", "quality", "warn", S, "latest sale is at most 2 days old", scalar=
         f"SELECT (max(order_ts)::DATE < current_date - 2)::INT FROM {S} WHERE order_ts::DATE <= current_date"),
    Rule("sales_daily_volume", "quality", "warn", S, "last day volume is at least 50% of the 28-day average", scalar=f"""
         WITH d AS (SELECT order_ts::DATE AS day, count(*) AS n FROM {S} WHERE order_ts::DATE <= current_date GROUP BY 1),
              last AS (SELECT max(day) AS day FROM d)
         SELECT ((SELECT n FROM d, last WHERE d.day = last.day)
                 < 0.5 * (SELECT avg(n) FROM d, last WHERE d.day BETWEEN last.day - 28 AND last.day - 1))::INT"""),
]
