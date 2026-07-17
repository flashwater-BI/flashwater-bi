"""Fix refund amounts from raw_json and recalculate order data"""
import sqlite3, json

conn = sqlite3.connect("data/flashwater.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 1. Extract oln_total_pay from raw_json
print("Step 1: Extracting refund amounts from raw_json...")
updated = 0
total_refund = 0.0
for row in c.execute("SELECT id, raw_json FROM refund_records").fetchall():
    try:
        raw = json.loads(row["raw_json"])
        oln_pay = float(raw.get("oln_total_pay", 0) or 0)
        refundable = float(raw.get("refundable", 0) or 0)
        actual = max(oln_pay, refundable)
        if actual > 0:
            c.execute(
                "UPDATE refund_records SET total_pay = ?, oln_total_pay = ?, refundable = ? WHERE id = ?",
                (actual, oln_pay, refundable, row["id"]),
            )
            updated += 1
            total_refund += actual
            if updated % 500 == 0:
                conn.commit()
    except Exception as e:
        print(f"  Skip id={row['id']}: {e}")
        continue

conn.commit()
print(f"  Updated {updated} refund records, total: {total_refund:,.2f}")

# 2. Update orders.refund_amount
print("Step 2: Updating order refund amounts...")
c.execute("UPDATE orders SET refund_amount = 0")
c.execute(
    """
    UPDATE orders SET refund_amount = (
        SELECT COALESCE(SUM(total_pay), 0) FROM refund_records 
        WHERE trade_code = orders.trade_no
    )
"""
)
conn.commit()

matched = c.execute(
    "SELECT COUNT(*) FROM orders WHERE refund_amount > 0"
).fetchone()[0]
print(f"  Orders with refund: {matched}")

# 3. Recalculate net_sales and gross
print("Step 3: Recalculating net sales and gross profit...")
c.execute("UPDATE orders SET net_sales_amount = payment_amount - refund_amount")
c.execute(
    """
    UPDATE orders SET gross_profit = 
        CASE WHEN net_sales_amount != 0 
        THEN net_sales_amount - total_cost ELSE 0 END
"""
)
c.execute(
    """
    UPDATE orders SET gross_margin = 
        CASE WHEN net_sales_amount != 0 
        THEN ROUND((net_sales_amount - total_cost) / net_sales_amount * 100, 2) 
        ELSE 0 END
"""
)
conn.commit()

# Summary
r = c.execute(
    """SELECT 
        COUNT(*) as n,
        ROUND(SUM(payment_amount),2) as pay,
        ROUND(SUM(refund_amount),2) as refund,
        ROUND(SUM(net_sales_amount),2) as net,
        ROUND(SUM(total_cost),2) as cost,
        ROUND(SUM(gross_profit),2) as gp
    FROM orders WHERE shop_name != '市场专用'"""
).fetchone()

gm = round(r["gp"] / r["net"] * 100, 2) if r["net"] else 0
print(f"\n{'='*60}")
print(f"Final result (4/17 - 7/16, API mode):")
print(f"  Orders: {r['n']}")
print(f"  Payment: {r['pay']:,.2f}")
print(f"  Refund:  {r['refund']:,.2f}")
print(f"  Net:     {r['net']:,.2f}")
print(f"  Cost:    {r['cost']:,.2f}")
print(f"  GP:      {r['gp']:,.2f}")
print(f"  GM:      {gm}%")
print(f"{'='*60}")

conn.close()
