"""Synthetic fashion retail data: clean baseline + dirty rows (for the DQ rules)
+ labelled business anomalies (ground truth for the detectors)."""
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config

# category -> (min price, max price, base return rate, size group)
CATEGORIES = {
    "Dresses": (90, 450, 0.14, "apparel"),
    "Outerwear": (180, 900, 0.09, "apparel"),
    "Knitwear": (70, 320, 0.08, "apparel"),
    "Denim": (60, 220, 0.10, "apparel"),
    "Tops": (30, 150, 0.07, "apparel"),
    "Shoes": (110, 520, 0.12, "shoes"),
    "Bags": (150, 1200, 0.04, "one"),
    "Accessories": (25, 180, 0.03, "one"),
}
SIZES = {
    "apparel": ["XS", "S", "M", "L", "XL"],
    "shoes": [str(s) for s in range(36, 45)],
    "one": ["OS"],
}
VALID_SIZES = sorted({s for g in SIZES.values() for s in g})
SEASONS = ["SS25", "FW25", "SS26", "FW26"]

STORES = [  # id, name, city, country, channel, avg sales lines per day
    ("S01", "Milano Montenapoleone", "Milan", "IT", "flagship", 140),
    ("S02", "Roma Condotti", "Rome", "IT", "retail", 110),
    ("S03", "Firenze Tornabuoni", "Florence", "IT", "retail", 80),
    ("S04", "Paris Le Marais", "Paris", "FR", "retail", 120),
    ("S05", "London Mayfair", "London", "GB", "retail", 115),
    ("S06", "Madrid Serrano", "Madrid", "ES", "retail", 85),
    ("S07", "Berlin Mitte", "Berlin", "DE", "retail", 75),
    ("S08", "Barcelona Gracia", "Barcelona", "ES", "retail", 70),
    ("S09", "Serravalle Outlet", "Serravalle", "IT", "outlet", 100),
    ("S10", "Online Store EU", "Online", "EU", "online", 160),
]
WEEKDAY = np.array([0.85, 0.8, 0.85, 0.95, 1.15, 1.45, 1.2])  # Mon..Sun

_COLORS = ["Black", "Ivory", "Camel", "Navy", "Burgundy", "Olive", "Blush", "Charcoal", "Cobalt", "Sand"]
_ITEMS = {
    "Dresses": ["Slip Dress", "Wrap Dress", "Midi Dress", "Shirt Dress"],
    "Outerwear": ["Trench Coat", "Wool Coat", "Puffer Jacket", "Blazer"],
    "Knitwear": ["Cashmere Crewneck", "Cardigan", "Turtleneck", "Knit Vest"],
    "Denim": ["Straight Jeans", "Wide-Leg Jeans", "Denim Jacket", "Denim Skirt"],
    "Tops": ["Silk Blouse", "Poplin Shirt", "Ribbed Tee", "Camisole"],
    "Shoes": ["Leather Loafers", "Ankle Boots", "Sneakers", "Slingback Pumps"],
    "Bags": ["Tote Bag", "Crossbody Bag", "Mini Bag", "Bucket Bag"],
    "Accessories": ["Silk Scarf", "Leather Belt", "Sunglasses", "Beanie"],
}


def generate(days=540, seed=42, end=None, out_dir=None):
    rng = np.random.default_rng(seed)
    out = out_dir or config.RAW_DIR
    out.mkdir(parents=True, exist_ok=True)
    end = end or date.today() - timedelta(days=1)
    calendar = pd.date_range(end=end, periods=days)
    labels = []  # ground truth: kind, entity_type, entity, day, sale_id

    # ---- dimensions ----
    cats = list(CATEGORIES)
    n_prod = 300
    p_cat = rng.choice(cats, n_prod, p=[0.16, 0.10, 0.13, 0.12, 0.17, 0.12, 0.08, 0.12])
    products = pd.DataFrame({
        "product_id": [f"P{i:04d}" for i in range(1, n_prod + 1)],
        "name": [f"{rng.choice(_COLORS)} {rng.choice(_ITEMS[c])}" for c in p_cat],
        "category": p_cat,
        "season": rng.choice(SEASONS, n_prod),
        "list_price": [float(round(rng.uniform(*CATEGORIES[c][:2]) / 5) * 5) for c in p_cat],
    })
    products["cost"] = (products.list_price * rng.uniform(0.28, 0.42, n_prod)).round(2)
    stores = [dict(zip(("store_id", "name", "city", "country", "channel"), s[:5])) for s in STORES]
    store_ids = np.array([s[0] for s in STORES])

    # ---- time-series events (applied while building the sales volume) ----
    pick_days = lambda n: rng.choice(np.arange(90, days - 1), n, replace=False)
    event_days = pick_days(26)
    event_days[:4] = days - 1 - np.array([2, 4, 6, 9])  # keep a few recent, so the dashboard looks alive
    drops = [(d, rng.integers(0, 10), rng.uniform(0.08, 0.22)) for d in event_days[[0, 4, 5, 6, 7, 8, 9, 10]]]
    spikes = [(d, rng.integers(0, 8), rng.uniform(2.6, 3.6)) for d in event_days[[1, 11, 12, 13, 14, 15]]]
    promos = [(d, rng.integers(0, 10)) for d in event_days[[2, 16, 17, 18, 19]]]
    defects = [(d, rng.integers(0, 8)) for d in event_days[[3, 20, 21, 22, 23]]]

    # ---- sales volume: day x store ----
    doy = calendar.day_of_year.to_numpy()
    season = 1 + 0.15 * np.sin(2 * np.pi * (doy - 60) / 365)
    trend = np.linspace(1.0, 1.12, days)
    lam = np.outer(WEEKDAY[calendar.day_of_week] * season * trend, [s[5] for s in STORES])
    for d, s, f in drops:
        lam[d, s] *= f
        labels.append(("revenue_drop", "store", store_ids[s], calendar[d].date(), None))
    counts = rng.poisson(lam)
    day_i = np.repeat(np.arange(days), counts.sum(axis=1))
    store_i = np.concatenate([np.repeat(np.arange(10), row) for row in counts])

    popularity = rng.dirichlet(np.full(n_prod, 3.0))
    prod_i = rng.choice(n_prod, len(day_i), p=popularity)

    # category spikes: extra lines for one category, spread over all stores
    cat_arr = products.category.to_numpy()
    for d, c, f in spikes:
        pool = np.flatnonzero(cat_arr == cats[c])
        extra = int((f - 1) * ((day_i == d) & (cat_arr[prod_i] == cats[c])).sum())
        day_i = np.append(day_i, np.full(extra, d))
        store_i = np.append(store_i, rng.choice(10, extra, p=lam[d] / lam[d].sum()))
        prod_i = np.append(prod_i, rng.choice(pool, extra))
        labels.append(("demand_spike", "category", cats[c], calendar[d].date(), None))

    n = len(day_i)
    row_cat = cat_arr[prod_i]
    list_price = products.list_price.to_numpy()[prod_i]
    quantity = rng.choice([1, 2, 3], n, p=[0.86, 0.11, 0.03])
    discount = rng.choice([0, 10, 20, 30, 50], n, p=[0.62, 0.14, 0.12, 0.08, 0.04]).astype(float)
    for d, s in promos:  # misconfigured promotion: everything heavily discounted
        m = (day_i == d) & (store_i == s)
        discount[m] = rng.choice([60, 70], m.sum())
        labels.append(("discount_misconfig", "store", store_ids[s], calendar[d].date(), None))
    return_p = np.array([CATEGORIES[c][2] for c in row_cat])
    for d, c in defects:  # quality defect: returns surge for 3 days
        for dd in range(d, min(d + 3, days)):
            return_p[(day_i == dd) & (row_cat == cats[c])] *= 4
            labels.append(("return_surge", "category", cats[c], calendar[dd].date(), None))
    size = np.empty(n, dtype=object)
    for group, values in SIZES.items():
        m = np.array([CATEGORIES[c][3] == group for c in row_cat])
        size[m] = rng.choice(values, m.sum())
    seconds = rng.integers(9 * 3600, 21 * 3600, n)

    sales = pd.DataFrame({
        "order_ts": calendar[day_i] + pd.to_timedelta(seconds, unit="s"),
        "store_id": store_ids[store_i],
        "product_id": products.product_id.to_numpy()[prod_i],
        "size": size,
        "quantity": quantity,
        "unit_price": (list_price * (1 - discount / 100)).round(2),
        "discount_pct": discount,
        "returned": rng.random(n) < return_p,
    }).sort_values("order_ts", ignore_index=True)
    sales.insert(0, "sale_id", np.arange(1, n + 1))
    lp = sales.product_id.map(products.set_index("product_id").list_price).to_numpy()

    # ---- transaction-level anomalies (labelled) and dirty rows (not labelled) ----
    free = rng.permutation(n)
    cursor = 0

    def take(k):
        nonlocal cursor
        idx = free[cursor:cursor + k]
        cursor += k
        return idx

    def label(kind, idx):
        for i in idx:
            labels.append((kind, "store", sales.store_id[i], sales.order_ts[i].date(), int(sales.sale_id[i])))

    i = take(50); sales.loc[i, "unit_price"] = (lp[i] / rng.choice([10, 100], 50)).round(2); label("price_error", i)
    i = take(30); sales.loc[i, "unit_price"] = (lp[i] * rng.uniform(1.5, 3, 30)).round(2); label("price_error", i)
    i = take(40); sales.loc[i, "quantity"] = rng.integers(25, 80, 40); label("bulk_order", i)
    i = take(40)
    sales.loc[i, "order_ts"] = sales.order_ts[i].dt.normalize() + pd.to_timedelta(rng.integers(3600, 5 * 3600, 40), unit="s")
    label("off_hours", i)

    sales["store_id"] = sales.store_id.astype(object)
    sales.loc[take(30), "store_id"] = None
    sales.loc[take(40), "product_id"] = "P9999"
    sales.loc[take(25), "quantity"] = -1
    sales.loc[take(20), "unit_price"] = rng.choice([0.0, -19.9], 20)
    sales.loc[take(15), "discount_pct"] = 120.0
    sales.loc[take(30), "size"] = "N/A"
    i = take(10); sales.loc[i, "order_ts"] = sales.order_ts[i] + pd.Timedelta(days=800)
    sales = pd.concat([sales, sales.loc[take(50)]], ignore_index=True)  # exact duplicates

    # ---- dirty dimension rows (never referenced by sales) ----
    bad_products = pd.DataFrame([
        {"product_id": "P0301", "name": "Ivory Wrap Dress", "category": "Dreses", "season": "SS26", "list_price": 240.0, "cost": 80.0},
        {"product_id": "P0302", "name": "Navy Tote Bag", "category": "Bags", "season": "FW26", "list_price": None, "cost": 95.0},
        {"product_id": "P0303", "name": "Camel Blazer", "category": "Outerwear", "season": "AW99", "list_price": 390.0, "cost": 130.0},
    ])
    products = pd.concat([products, bad_products, products.iloc[[10, 42]]], ignore_index=True)

    snaps = pd.date_range(end=end, periods=days // 30, freq="30D")
    inv = pd.MultiIndex.from_product([snaps, store_ids, products.product_id[:n_prod]],
                                     names=["snapshot_date", "store_id", "product_id"]).to_frame(index=False)
    inv["stock_qty"] = rng.poisson(18, len(inv))
    inv.loc[rng.choice(len(inv), 20, replace=False), "stock_qty"] = -rng.integers(1, 9, 20)
    inv.loc[rng.choice(len(inv), 5, replace=False), "store_id"] = "S99"

    # ---- write: one format per source, like a real landing zone ----
    products.to_csv(out / "products.csv", index=False)
    (out / "stores.json").write_text(json.dumps(stores, indent=2), encoding="utf-8")
    sales.to_parquet(out / "sales.parquet", index=False)
    inv.to_csv(out / "inventory.csv", index=False)
    pd.DataFrame(labels, columns=["kind", "entity_type", "entity", "day", "sale_id"]) \
        .astype({"sale_id": "Int64"}).to_csv(out / "injected_anomalies.csv", index=False)
    return {"sales": len(sales), "products": len(products), "inventory": len(inv), "labels": len(labels)}
