#!/usr/bin/env python3
"""
增量API同步：自动检测数据库中最新日期，从上次同步截止日同步到今天
使用 UPSERT 避免重复，不清空已有数据，历史数据永久保留
用法：
  python sync_incremental.py                    # 自动补齐缺失日期
  python sync_incremental.py 2026-07-16 2026-07-17  # 指定日期范围
"""
import json
import os
import subprocess
import sys
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
from db_init import get_conn

CLI = "C:/Users/altermind/.workbuddy/skills/hupun-api-connector/assets/hupun-api-cli"
CFG = "C:/Users/altermind/.workbuddy/skills/hupun-api-connector/assets/config.json"
AGENT = "WorkBuddy"

PAGE_SIZE = 200
MAX_SYNC_DAYS = 90  # 最多往前补90天，避免API超限

PLATFORM_MAP = {
    'FLASH WATER旗舰店': '天猫',
    'FLASH WATER闪水口腔护理专卖店': '抖店',
    'Flash Water口腔护理的店': '小红书',
}


def ts_to_dt(ts):
    if ts is None or ts == 0:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return None


def call_api(path, params):
    params_str = json.dumps(params, ensure_ascii=False)
    cmd = [CLI, "-c", CFG, path, params_str, "--agent", AGENT]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"  CLI error: {result.stderr}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  JSON parse error: {result.stdout[:200]}")
        return None


def safe_float(v, default=0.0):
    if v is None: return default
    try: return float(v)
    except (ValueError, TypeError): return default


def safe_bool(v):
    if v is None: return 0
    if isinstance(v, bool): return 1 if v else 0
    return int(v)


def fetch_trades_range(start_date, end_date):
    """Fetch trades in date range using pay_time"""
    all_trades = []
    page = 1
    while True:
        params = {
            "page": page, "limit": PAGE_SIZE,
            "pay_time": f"{start_date} 00:00:00",
            "end_time": f"{end_date} 23:59:59",
            "trade_status": "0,1,2,3,4,5,8,9,10,13,15,19"
        }
        resp = call_api("/erp/opentrade/list/trades", params)
        if resp is None: break
        if resp.get("code") != 0:
            print(f"  API error: {resp.get('error_message', resp)}")
            break
        data = resp.get("data", [])
        if not data: break
        all_trades.extend(data)
        print(f"  page {page}: {len(data)} trades (total: {len(all_trades)})")
        if len(data) < PAGE_SIZE: break
        page += 1
        time.sleep(0.5)
    return all_trades


def fetch_refunds_range(start_date, end_date):
    """Fetch refunds in date range"""
    all_refunds = []
    page = 1
    while True:
        params = {
            "page": page, "limit": PAGE_SIZE,
            "start_time": f"{start_date} 00:00:00",
            "end_time": f"{end_date} 23:59:59",
            "time_type": 1
        }
        resp = call_api("/erp/open/return/order/list", params)
        if resp is None: break
        if resp.get("code") != 0:
            print(f"  API error: {resp.get('error_message', resp)}")
            break
        data = resp.get("data", [])
        if not data: break
        all_refunds.extend(data)
        print(f"  page {page}: {len(data)} refunds (total: {len(all_refunds)})")
        if len(data) < PAGE_SIZE: break
        page += 1
        time.sleep(0.5)
    return all_refunds


def upsert_trades(conn, trades):
    """Upsert trades: update if exists, insert if new"""
    c = conn.cursor()
    new_count = 0
    item_count = 0
    
    c.execute("SELECT uid FROM orders")
    existing = {r[0] for r in c.fetchall()}
    
    for t in trades:
        try:
            uid = t.get("uid", "")
            pay_time = ts_to_dt(t.get("pay_time"))
            if not pay_time:
                pay_time = ts_to_dt(t.get("create_time"))
            
            if uid in existing:
                # Update refund/net_sales in case refunds changed
                c.execute("""
                    UPDATE orders SET payment_amount=?, net_sales_amount=payment_amount-refund_amount,
                    send_time=COALESCE(send_time, ?), order_mark=?, modify_time=?
                    WHERE uid=?
                """, (safe_float(t.get("paid_fee")), ts_to_dt(t.get("send_time")),
                      t.get("mark", ""), ts_to_dt(t.get("modify_time")), uid))
                continue
            
            # New order — insert
            order_data = {
                "uid": uid,
                "trade_no": t.get("trade_no", ""),
                "shop_name": t.get("shop_name", ""),
                "shop_nick": t.get("shop_nick", ""),
                "source_platform": t.get("source_platform", ""),
                "storage_name": t.get("storage_name", ""),
                "storage_code": t.get("storage_code", ""),
                "create_time": ts_to_dt(t.get("create_time")),
                "pay_time": pay_time,
                "send_time": ts_to_dt(t.get("send_time")),
                "end_time": ts_to_dt(t.get("end_time")),
                "status": safe_float(t.get("status")),
                "process_status": safe_float(t.get("process_status")),
                "payment_amount": safe_float(t.get("paid_fee")),
                "refund_amount": 0.0,
                "net_sales_amount": safe_float(t.get("paid_fee")),
                "paid_fee": safe_float(t.get("paid_fee")),
                "post_fee": safe_float(t.get("post_fee")),
                "post_cost": safe_float(t.get("post_cost")),
                "commision": safe_float(t.get("commision")),
                "buyer_show": t.get("buyer_show", ""),
                "province": t.get("province", ""),
                "city": t.get("city", ""),
                "district": t.get("district", ""),
                "pay_type": t.get("pay_type", ""),
                "logistic_name": t.get("logistic_name", ""),
                "order_mark": t.get("mark", ""),
                "raw_json": json.dumps(t, ensure_ascii=False)
            }
            
            fields = ", ".join(order_data.keys())
            placeholders = ", ".join("?" * len(order_data))
            c.execute(f"INSERT OR IGNORE INTO orders ({fields}) VALUES ({placeholders})",
                      list(order_data.values()))
            if c.rowcount > 0:
                new_count += 1
                existing.add(uid)
            
            # Insert order items
            for oi in t.get("orders", []):
                c.execute("INSERT OR IGNORE INTO order_items "
                          "(order_uid, order_detail_id, sku_code, item_name, size, price, "
                          "discounted_unit_price, payment, is_gift, has_refund, unit_cost, item_total_cost) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,0,0)",
                          (uid, oi.get("order_id",""), oi.get("sku_code",""),
                           oi.get("item_name",""), safe_float(oi.get("size"),1),
                           safe_float(oi.get("price")),
                           safe_float(oi.get("discounted_unit_price")),
                           safe_float(oi.get("payment")),
                           safe_bool(oi.get("is_gift")),
                           safe_bool(oi.get("has_refund"))))
                item_count += 1
            
        except Exception as e:
            print(f"  ERROR upsert trade {t.get('trade_no','?')}: {e}")
            continue
    
    conn.commit()
    return new_count, item_count


def upsert_refunds(conn, refunds):
    """Upsert refund records and update order refund amounts"""
    c = conn.cursor()
    new_count = 0
    
    c.execute("SELECT bill_code FROM refund_records")
    existing = {r[0] for r in c.fetchall()}
    
    for r in refunds:
        try:
            bill_code = r.get("bill_code", "")
            trade_code = r.get("trade_code", "")
            total_pay = safe_float(r.get("oln_total_pay"))
            
            if bill_code in existing:
                continue
            
            c.execute("""INSERT OR IGNORE INTO refund_records
                (bill_code, trade_code, refund_type, status, refund_stage,
                 total_pay, refundable, oln_total_pay, pay_money, reason,
                 create_time, refund_time, shop_name, shop_nick, source_platform, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (bill_code, trade_code, safe_float(r.get("type")),
                 safe_float(r.get("status")), safe_float(r.get("refund_stage")),
                 total_pay, safe_float(r.get("refundable")),
                 safe_float(r.get("oln_total_pay")), safe_float(r.get("pay_money")),
                 r.get("reason",""), ts_to_dt(r.get("create_time")),
                 ts_to_dt(r.get("refund_time")), r.get("shop_name",""),
                 r.get("shop_nick",""), r.get("source_platform",""),
                 json.dumps(r, ensure_ascii=False)))
            
            if c.rowcount > 0:
                new_count += 1
                existing.add(bill_code)
            
            # Update order refund amount
            if trade_code and total_pay > 0:
                c.execute("""UPDATE orders SET refund_amount = refund_amount + ?,
                    net_sales_amount = payment_amount - (refund_amount + ?)
                    WHERE trade_no = ? AND shop_name != '市场专用'""",
                    (total_pay, total_pay, trade_code))
                
        except Exception as e:
            print(f"  ERROR upsert refund {r.get('bill_code','?')}: {e}")
            continue
    
    conn.commit()
    return new_count


def get_sync_range(conn):
    """
    自动检测同步范围：
    - 找到数据库中最新的 pay_time 日期
    - 从该日期的下一天开始，同步到昨天（T+1：今天数据不完整）
    - 如果数据库为空，从 MAX_SYNC_DAYS 天前开始
    """
    row = conn.execute(
        "SELECT MAX(date(pay_time)) FROM orders WHERE pay_time IS NOT NULL AND shop_name != '市场专用'"
    ).fetchone()
    
    if row and row[0]:
        last_date = datetime.strptime(row[0], '%Y-%m-%d')
        start = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        # 空数据库：从90天前开始
        start = (datetime.now() - timedelta(days=MAX_SYNC_DAYS)).strftime('%Y-%m-%d')
    
    # T+1: 今天数据不完整，同步到昨天
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    if start > yesterday:
        return None, None  # 已是最新，无需同步
    
    return start, yesterday


def main():
    # 解析命令行参数
    if len(sys.argv) == 3:
        START_DATE = sys.argv[1]
        END_DATE = sys.argv[2]
    else:
        conn = get_conn()
        START_DATE, END_DATE = get_sync_range(conn)
        conn.close()
        if START_DATE is None:
            print("✓ 数据已是最新，无需同步")
            return
    
    print("=" * 60)
    print(f"FlashWater API 增量同步: {START_DATE} ~ {END_DATE}")
    print("=" * 60)
    
    # Phase 1: Fetch new trades
    print("\n[Phase 1] 拉取增量订单...")
    trades = fetch_trades_range(START_DATE, END_DATE)
    print(f"  拉取订单: {len(trades)}")
    
    # Phase 2: Upsert trades
    print(f"\n[Phase 2] 增量导入订单...")
    conn = get_conn()
    new_orders, item_count = upsert_trades(conn, trades)
    print(f"  新增订单: {new_orders}, 新增商品行: {item_count}")
    
    # Phase 3: Fetch new refunds
    print(f"\n[Phase 3] 拉取增量售后单...")
    refunds = fetch_refunds_range(START_DATE, END_DATE)
    print(f"  拉取售后单: {len(refunds)}")
    
    # Phase 4: Upsert refunds
    print(f"\n[Phase 4] 增量导入售后单...")
    new_refunds = upsert_refunds(conn, refunds)
    print(f"  新增售后单: {new_refunds}")
    
    # Phase 5: Update costs for new items
    print(f"\n[Phase 5] 更新新产品成本...")
    c = conn.cursor()
    cost_map = {}
    for row in c.execute("SELECT sku_code, unit_cost FROM product_cost").fetchall():
        cost_map[row[0]] = row[1]
    
    if cost_map:
        for sku, cost in cost_map.items():
            c.execute("""UPDATE order_items SET unit_cost=?,
                item_total_cost=?*size WHERE sku_code=? AND unit_cost=0""",
                (cost, cost, sku))
        
        c.execute("""UPDATE orders SET total_cost = COALESCE(
            (SELECT SUM(oi.item_total_cost) FROM order_items oi
             WHERE oi.order_uid = orders.uid), 0)
            WHERE total_cost = 0""")
        c.execute("""UPDATE orders SET gross_profit = net_sales_amount - total_cost,
            gross_margin = CASE WHEN net_sales_amount != 0
            THEN ROUND((net_sales_amount - total_cost)/net_sales_amount*100, 2) ELSE 0 END""")
        conn.commit()
        print(f"  成本映射: {len(cost_map)} SKUs")
    
    # Summary
    r = c.execute("""SELECT COUNT(*), ROUND(SUM(payment_amount),2),
        ROUND(SUM(refund_amount),2), ROUND(SUM(net_sales_amount),2)
        FROM orders WHERE shop_name != '市场专用'""").fetchone()
    print(f"\n{'='*60}")
    print(f"增量同步完成! 数据摘要:")
    print(f"  总订单: {r[0]} | 付款: ¥{r[1]:,.2f} | 退款: ¥{r[2]:,.2f} | 净销售: ¥{r[3]:,.2f}")
    
    # 最近同步日期的明细
    print(f"\n{END_DATE} 数据:")
    for row in c.execute("""SELECT shop_name, COUNT(*), ROUND(SUM(payment_amount),2),
        ROUND(SUM(net_sales_amount),2) FROM orders
        WHERE shop_name != '市场专用' AND substr(pay_time,1,10)=?
        GROUP BY shop_name""", (END_DATE,)).fetchall():
        print(f"  {row[0]}: {row[1]}单 | 付款¥{row[2]:,.2f} | 净销售¥{row[3]:,.2f}")
    
    conn.close()


if __name__ == "__main__":
    main()
