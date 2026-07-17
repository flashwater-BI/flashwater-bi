"""Step 2+3: Update order refunds and recalculate (Python loop, not SQL subquery)"""
import sqlite3

conn = sqlite3.connect("data/flashwater.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Build refund map: trade_code -> total refund
print("Building refund map...")
c.execute("SELECT trade_code, SUM(total_pay) as tp FROM refund_records WHERE total_pay > 0 GROUP BY trade_code")
refund_map = {}
for row in c.fetchall():
    refund_map[row["trade_code"]] = row["tp"]
print(f"  {len(refund_map)} trades with refunds")

# Update orders in batches
print("Updating orders...")
batch_size = 500
updated = 0
total_refund = 0.0

orders = c.execute(
    "SELECT uid, trade_no, payment_amount, total_cost FROM orders WHERE shop_name != '市场专用'"
).fetchall()

for i, order in enumerate(orders):
    refund = refund_map.get(order["trade_no"], 0.0)
    net_sales = order["payment_amount"] - refund
    gp = net_sales - order["total_cost"] if net_sales != 0 else 0
    gm = round(gp / net_sales * 100, 2) if net_sales != 0 else 0
    
    c.execute(
        "UPDATE orders SET refund_amount=?, net_sales_amount=?, gross_profit=?, gross_margin=? WHERE uid=?",
        (refund, net_sales, gp, gm, order["uid"]),
    )
    
    if refund > 0:
        updated += 1
        total_refund += refund
    
    if (i + 1) % batch_size == 0:
        conn.commit()
        print(f"  {i+1}/{len(orders)} orders processed...")

conn.commit()
print(f"  Done: {len(orders)} orders, {updated} with refund")

# Summary
r = c.execute(
    """SELECT COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay,
       ROUND(SUM(refund_amount),2) as refund,
       ROUND(SUM(net_sales_amount),2) as net,
       ROUND(SUM(total_cost),2) as cost,
       ROUND(SUM(gross_profit),2) as gp
    FROM orders WHERE shop_name != '市场专用'"""
).fetchone()

gm = round(r["gp"] / r["net"] * 100, 2) if r["net"] else 0
print(f"\n{'='*60}")
print(f"API数据同步完成 (2026-04-17 ~ 2026-07-16):")
print(f"  Orders:  {r['n']}")
print(f"  Payment: {r['pay']:,.2f}")
print(f"  Refund:  {r['refund']:,.2f}")
print(f"  Net:     {r['net']:,.2f}")
print(f"  Cost:    {r['cost']:,.2f}")
print(f"  GP:      {r['gp']:,.2f}")
print(f"  GM:      {gm}%")
print(f"{'='*60}")

conn.close()
