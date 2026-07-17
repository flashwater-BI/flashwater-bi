"""Re-import order_items from orders.raw_json with correct column mapping"""
import sqlite3, json

conn = sqlite3.connect("data/flashwater.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Clear existing
c.execute("DELETE FROM order_items")
conn.commit()

# Get all orders with raw_json
orders = c.execute("SELECT uid, trade_no, raw_json FROM orders").fetchall()
print(f"Processing {len(orders)} orders...")

total_items = 0
batch = 0

for order in orders:
    try:
        raw = json.loads(order["raw_json"])
        items = raw.get("orders", [])
        
        for oi in items:
            item_data = {
                "order_uid": order["uid"],
                "order_detail_id": oi.get("order_id", ""),
                "sku_code": oi.get("sku_code", ""),
                "item_name": oi.get("item_name", ""),
                "oln_item_name": oi.get("oln_item_name", ""),
                "oln_sku_code": oi.get("oln_sku_code", ""),
                "oln_sku_name": oi.get("oln_sku_name", ""),
                "size": float(oi.get("size", 1) or 1),
                "price": float(oi.get("price", 0) or 0),
                "discounted_unit_price": float(oi.get("discounted_unit_price", 0) or 0),
                "receivable": float(oi.get("receivable", 0) or 0),
                "payment": float(oi.get("payment", 0) or 0),
                "order_total_discount": float(oi.get("order_total_discount", 0) or 0),
                "is_gift": 1 if oi.get("is_gift") else 0,
                "is_package": 1 if oi.get("is_package") else 0,
                "has_refund": 1 if oi.get("has_refund") else 0,
                "inventory_status": oi.get("inventory_status", ""),
                "sys_goods_uid": oi.get("sys_goods_uid", ""),
                "sys_spec_uid": oi.get("sys_spec_uid", ""),
                "tp_tid": oi.get("tp_tid", ""),
                "tp_oid": oi.get("tp_oid", ""),
                "oln_item_id": oi.get("oln_item_id", ""),
                "oln_sku_id": oi.get("oln_sku_id", ""),
                "bar_code": oi.get("bar_code", ""),
                "remark": oi.get("remark", ""),
                "unit_cost": 0.0,
                "item_total_cost": 0.0,
            }
            
            fields = ", ".join(item_data.keys())
            placeholders = ", ".join("?" * len(item_data))
            c.execute(
                f"INSERT INTO order_items ({fields}) VALUES ({placeholders})",
                list(item_data.values()),
            )
            total_items += 1
            
    except Exception as e:
        print(f"  ERROR {order['trade_no']}: {e}")
        continue
    
    batch += 1
    if batch % 1000 == 0:
        conn.commit()
        print(f"  {batch}/{len(orders)} orders, {total_items} items...")

conn.commit()
print(f"\nDone! {total_items} items from {len(orders)} orders")

# Now associate costs
print("\nAssociating product costs...")
cost_map = {}
for row in c.execute("SELECT sku_code, unit_cost FROM product_cost").fetchall():
    cost_map[row[0]] = row[1]
print(f"  {len(cost_map)} SKUs with costs")

updated = 0
for sku, cost in cost_map.items():
    c.execute(
        "UPDATE order_items SET unit_cost=?, item_total_cost=unit_cost*size WHERE sku_code=? AND is_gift=0",
        (cost, sku),
    )
    updated += c.rowcount

# Fallback for is_gift=1 items (should still get costs if they have SKU):
c.execute(
    "UPDATE order_items SET unit_cost = (SELECT pc.unit_cost FROM product_cost pc WHERE pc.sku_code = order_items.sku_code), item_total_cost = unit_cost * size WHERE unit_cost = 0 AND is_gift = 0"
)
conn.commit()
print(f"  Updated {updated} items with costs")

# Recalculate order costs
c.execute("""
    UPDATE orders SET total_cost = COALESCE(
        (SELECT SUM(oi.item_total_cost) FROM order_items oi 
         WHERE oi.order_uid = orders.uid AND oi.is_gift = 0), 0)
""")
c.execute("""
    UPDATE orders SET gross_profit = 
        CASE WHEN net_sales_amount != 0 THEN net_sales_amount - total_cost ELSE 0 END
""")
c.execute("""
    UPDATE orders SET gross_margin = 
        CASE WHEN net_sales_amount != 0 
        THEN ROUND((net_sales_amount - total_cost) / net_sales_amount * 100, 2) ELSE 0 END
""")
conn.commit()

# Summary
r = c.execute(
    """SELECT COUNT(*) as n, ROUND(SUM(payment_amount),2) as pay,
       ROUND(SUM(total_cost),2) as cost, ROUND(SUM(gross_profit),2) as gp
    FROM orders WHERE shop_name != '市场专用'"""
).fetchone()
gm = round((r["pay"] - r["cost"] - 145360.32) / (r["pay"] - 145360.32) * 100, 2) if r["pay"] else 0
print(f"\n{'='*60}")
print(f"  Orders: {r['n']} | Pay: {r['pay']:,.2f} | Cost: {r['cost']:,.2f}")
print(f"  Order GP: {r['gp']:,.2f}")
print(f"{'='*60}")

# By platform
for row in c.execute(
    """SELECT source_platform, COUNT(*) as n, ROUND(SUM(payment_amount),2) as pay,
       ROUND(SUM(total_cost),2) as cost, ROUND(SUM(gross_profit),2) as gp
    FROM orders WHERE shop_name != '市场专用'
    GROUP BY source_platform"""
).fetchall():
    gm_p = round((row["pay"] - row["cost"]) / row["pay"] * 100, 2) if row["pay"] else 0
    print(f"  {row['source_platform']:6} | {row['n']:>5} orders | pay={row['pay']:>10,.2f} | cost={row['cost']:>8,.2f} | gm={gm_p}%")

conn.close()
