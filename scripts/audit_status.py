"""审计：订单状态分布 & is_pay 逻辑 & NULL pay_time"""
import sqlite3

conn = sqlite3.connect("data/flashwater.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

def fmt(row, *keys):
    parts = []
    for k in keys:
        v = row[k]
        if v is None:
            parts.append(f"{k}=None")
        elif isinstance(v, float):
            parts.append(f"{k}=¥{v:,.2f}")
        else:
            parts.append(f"{k}={v}")
    return " | ".join(parts)

# 1. Status distribution
print("=== 订单状态分布 ===")
for r in c.execute(
    """SELECT status, process_status, COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay
       FROM orders WHERE shop_name!='市场专用'
       GROUP BY status, process_status ORDER BY n DESC"""
).fetchall():
    print(f"  {fmt(r, 'status', 'process_status', 'n', 'pay')}")

# 2. is_pay=0 but has payment
print("\n=== is_pay=0 但有付款 ===")
for r in c.execute(
    """SELECT status, process_status, COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay
       FROM orders WHERE shop_name!='市场专用' AND is_pay=0 AND payment_amount > 0
       GROUP BY status, process_status ORDER BY n DESC"""
).fetchall():
    print(f"  {fmt(r, 'status', 'process_status', 'n', 'pay')}")

# 3. NULL pay_time
print("\n=== pay_time=NULL ===")
for r in c.execute(
    """SELECT is_pay, status, process_status, COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay
       FROM orders WHERE shop_name!='市场专用' AND pay_time IS NULL
       GROUP BY is_pay, status, process_status ORDER BY n DESC"""
).fetchall():
    print(f"  {fmt(r, 'is_pay', 'status', 'process_status', 'n', 'pay')}")

# 4. 明细状态原文
print("\n=== is_pay=0 订单明细状态样本 ===")
for r in c.execute(
    """SELECT trade_no, status, process_status, order_status, 
       (SELECT GROUP_CONCAT(DISTINCT detail_status) FROM order_items WHERE order_uid=o.uid) as ds
       FROM orders o WHERE shop_name!='市场专用' AND is_pay=0 AND payment_amount > 0
       GROUP BY status, process_status LIMIT 15"""
).fetchall():
    print(f"  trade={r['trade_no']} | status={r['status']} | proc={r['process_status']} | order={r['order_status']} | detail={r['ds']}")

# 5. process_status=10 汇总
print("\n=== process_status=10 ===")
r = c.execute(
    """SELECT COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay,
       ROUND(SUM(refund_amount),2) as refund,
       ROUND(SUM(net_sales_amount),2) as net
       FROM orders WHERE shop_name!='市场专用' AND process_status=10"""
).fetchone()
print(f"  {fmt(r, 'n', 'pay', 'refund', 'net')}")

# 6. 按万里牛规则: process_status NOT IN (10)
r = c.execute(
    """SELECT COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay,
       ROUND(SUM(refund_amount),2) as refund
       FROM orders WHERE shop_name!='市场专用' AND process_status NOT IN (10) AND pay_time IS NOT NULL"""
).fetchone()
print(f"\n=== 按万里牛规则: proc NOT 10, has pay_time ===")
print(f"  {fmt(r, 'n', 'pay', 'refund')}")

r = c.execute(
    """SELECT COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay,
       ROUND(SUM(refund_amount),2) as refund
       FROM orders WHERE shop_name!='市场专用' AND process_status NOT IN (10)"""
).fetchone()
print(f"\n=== 按万里牛规则: proc NOT 10, 含NULL pay_time ===")
print(f"  {fmt(r, 'n', 'pay', 'refund')}")

# 7. 全部订单（排除市场专用）的 payments
r = c.execute(
    """SELECT COUNT(*) as n,
       ROUND(SUM(payment_amount),2) as pay,
       ROUND(SUM(refund_amount),2) as refund,
       ROUND(SUM(net_sales_amount),2) as net
       FROM orders WHERE shop_name!='市场专用'"""
).fetchone()
print(f"\n=== 全部订单(排除市场专用) ===")
print(f"  {fmt(r, 'n', 'pay', 'refund', 'net')}")

conn.close()
