#!/usr/bin/env python3
"""
全量 API 数据同步脚本
- 从万里牛 API 拉取全部订单 + 售后单
- 日期范围：2026-04-17 ~ 2026-07-16（API 支持的最早日期）
- 每批 7 天，每页 200 条
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

START_DATE = "2026-04-17"
END_DATE = "2026-07-16"
BATCH_DAYS = 7
PAGE_SIZE = 200


def ts_to_dt(ts):
    """Convert 13-digit timestamp to datetime string"""
    if ts is None or ts == 0:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return None


def call_api(path, params):
    """Call hupun-api-cli and return parsed JSON"""
    params_str = json.dumps(params, ensure_ascii=False)
    cmd = [CLI, "-c", CFG, path, params_str, "--agent", AGENT]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"  CLI error: {result.stderr}")
        return None
    try:
        resp = json.loads(result.stdout)
        return resp
    except json.JSONDecodeError:
        print(f"  JSON parse error: {result.stdout[:200]}")
        return None


def fetch_trades_batch(start_date, end_date):
    """Fetch all trades in a date range, handling pagination"""
    all_trades = []
    page = 1
    while True:
        params = {
            "page": page,
            "limit": PAGE_SIZE,
            "pay_time": f"{start_date} 00:00:00",
            "end_time": f"{end_date} 23:59:59",
            "trade_status": "0,1,2,3,4,5,8,9,10,13,15,19"
        }
        resp = call_api("/erp/opentrade/list/trades", params)
        if resp is None:
            print(f"  ERROR on page {page}")
            break
        if resp.get("code") != 0:
            print(f"  API error: {resp.get('error_message', resp)}")
            break
        
        data = resp.get("data", [])
        if not data:
            break
        
        all_trades.extend(data)
        print(f"  page {page}: {len(data)} trades (total: {len(all_trades)})")
        
        if len(data) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)  # rate limit
    
    return all_trades


def fetch_refunds_batch(start_date, end_date):
    """Fetch all refund/after-sale orders in a date range"""
    all_refunds = []
    page = 1
    while True:
        params = {
            "page": page,
            "limit": PAGE_SIZE,
            "start_time": f"{start_date} 00:00:00",
            "end_time": f"{end_date} 23:59:59",
            "time_type": 1
        }
        resp = call_api("/erp/open/return/order/list", params)
        if resp is None:
            print(f"  ERROR on page {page}")
            break
        if resp.get("code") != 0:
            print(f"  API error: {resp.get('error_message', resp)}")
            break
        
        data = resp.get("data", [])
        if not data:
            break
        
        all_refunds.extend(data)
        print(f"  page {page}: {len(data)} refunds (total: {len(all_refunds)})")
        
        if len(data) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)
    
    return all_refunds


def safe_float(v, default=0.0):
    """Safely convert to float"""
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def safe_bool(v):
    """Safely convert to int"""
    if v is None:
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    return int(v)


def import_trades_to_db(conn, trades):
    """Import trade orders into database"""
    c = conn.cursor()
    
    # Clear existing data
    c.execute("DELETE FROM order_items")
    c.execute("DELETE FROM orders")
    conn.commit()
    
    order_count = 0
    item_count = 0
    
    for t in trades:
        try:
            uid = t.get("uid", "")
            trade_no = t.get("trade_no", "")
            
            # Dates
            pay_time = ts_to_dt(t.get("pay_time"))
            if not pay_time:
                pay_time = ts_to_dt(t.get("create_time"))
            
            order_data = {
                "uid": uid,
                "trade_no": trade_no,
                "shop_name": t.get("shop_name", ""),
                "shop_nick": t.get("shop_nick", ""),
                "sys_shop": t.get("sys_shop", ""),
                "source_platform": t.get("source_platform", ""),
                "shop_type": safe_float(t.get("shop_type")),
                "storage_name": t.get("storage_name", ""),
                "storage_code": t.get("storage_code", ""),
                "create_time": ts_to_dt(t.get("create_time")),
                "pay_time": pay_time,
                "send_time": ts_to_dt(t.get("send_time")),
                "end_time": ts_to_dt(t.get("end_time")),
                "approve_time": ts_to_dt(t.get("approve_time")),
                "modify_time": ts_to_dt(t.get("modify_time")),
                "index_time": ts_to_dt(t.get("index_time")),
                "status": safe_float(t.get("status")),
                "process_status": safe_float(t.get("process_status")),
                "oln_status": safe_float(t.get("oln_status")),
                "payment_amount": safe_float(t.get("paid_fee")),
                "refund_amount": 0.0,  # will be updated from refund API
                "net_sales_amount": safe_float(t.get("paid_fee")),
                "commission_amount": safe_float(t.get("commision")),
                "paid_fee": safe_float(t.get("paid_fee")),
                "real_payment": safe_float(t.get("real_payment")),
                "sum_sale": safe_float(t.get("sum_sale")),
                "discount_fee": safe_float(t.get("discount_fee")),
                "post_fee": safe_float(t.get("post_fee")),
                "post_cost": safe_float(t.get("post_cost")),
                "commision": safe_float(t.get("commision")),
                "service_fee": safe_float(t.get("service_fee")),
                "has_refund": safe_bool(t.get("has_refund")),
                "is_pay": safe_bool(t.get("is_pay")),
                "buyer_show": t.get("buyer_show", ""),
                "province": t.get("province", ""),
                "city": t.get("city", ""),
                "district": t.get("district", ""),
                "pay_type": t.get("pay_type", ""),
                "logistic_name": t.get("logistic_name", ""),
                "trade_type": safe_float(t.get("trade_type")),
                "tp_tid": t.get("tp_tid", ""),
                "weight": safe_float(t.get("weight")),
                "volume": safe_float(t.get("volume")),
                "is_exception_trade": safe_bool(t.get("is_exception_trade")),
                "exchange_trade": safe_bool(t.get("exchange_trade")),
                "remark": t.get("remark", ""),
                "buyer_msg": t.get("buyer_msg", ""),
                "seller_msg": t.get("seller_msg", ""),
                "is_gift": 0,
                "order_mark": t.get("mark", ""),
                "total_cost": 0.0,
                "gross_profit": 0.0,
                "gross_margin": 0.0,
                "raw_json": json.dumps(t, ensure_ascii=False)
            }
            
            # Insert order
            fields = ", ".join(order_data.keys())
            placeholders = ", ".join("?" * len(order_data))
            values = list(order_data.values())
            c.execute(f"INSERT INTO orders ({fields}) VALUES ({placeholders})", values)
            order_count += 1
            
            # Insert order items
            for oi in t.get("orders", []):
                item_data = {
                    "order_uid": uid,
                    "order_detail_id": oi.get("order_id", ""),
                    "sku_code": oi.get("sku_code", ""),
                    "item_name": oi.get("item_name", ""),
                    "oln_item_name": oi.get("oln_item_name", ""),
                    "oln_sku_code": oi.get("oln_sku_code", ""),
                    "oln_sku_name": oi.get("oln_sku_name", ""),
                    "size": safe_float(oi.get("size"), 1),
                    "price": safe_float(oi.get("price")),
                    "discounted_unit_price": safe_float(oi.get("discounted_unit_price")),
                    "receivable": safe_float(oi.get("receivable")),
                    "payment": safe_float(oi.get("payment")),
                    "order_total_discount": safe_float(oi.get("order_total_discount")),
                    "is_gift": safe_bool(oi.get("is_gift")),
                    "has_refund": safe_bool(oi.get("has_refund")),
                    "is_package": safe_bool(oi.get("is_package")),
                    "tp_tid": oi.get("tp_tid", ""),
                    "tp_oid": oi.get("tp_oid", ""),
                    "sys_goods_uid": oi.get("sys_goods_uid", ""),
                    "sys_spec_uid": oi.get("sys_spec_uid", ""),
                    "bar_code": oi.get("bar_code", ""),
                    "remark": oi.get("remark", ""),
                    "unit_cost": 0.0,
                    "item_total_cost": 0.0,
                }
                item_fields = ", ".join(item_data.keys())
                item_placeholders = ", ".join("?" * len(item_data))
                item_values = list(item_data.values())
                c.execute(f"INSERT INTO order_items ({item_fields}) VALUES ({item_placeholders})", item_values)
                item_count += 1
                
        except Exception as e:
            print(f"  ERROR importing trade {t.get('trade_no', '?')}: {e}")
            continue
    
    conn.commit()
    return order_count, item_count


def import_refunds_to_db(conn, refunds):
    """Import refund orders and update order refund amounts"""
    c = conn.cursor()
    refund_count = 0
    refund_total = 0.0
    
    for r in refunds:
        try:
            trade_code = r.get("trade_code", "")
            # Use oln_total_pay (平台退款金额) — total_pay is 0 in API response
            total_pay = safe_float(r.get("oln_total_pay"))
            refund_type = safe_float(r.get("type"))
            
            # Store refund record
            c.execute("""
                INSERT INTO refund_records 
                (bill_code, trade_code, refund_type, status, refund_stage, 
                 total_pay, refundable, oln_total_pay, pay_money, reason, 
                 create_time, refund_time, shop_name, shop_nick, source_platform,
                 raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r.get("bill_code", ""),
                trade_code,
                refund_type,
                safe_float(r.get("status")),
                safe_float(r.get("refund_stage")),
                total_pay,
                safe_float(r.get("refundable")),
                safe_float(r.get("oln_total_pay")),
                safe_float(r.get("pay_money")),
                r.get("reason", ""),
                ts_to_dt(r.get("create_time")),
                ts_to_dt(r.get("refund_time")),
                r.get("shop_name", ""),
                r.get("shop_nick", ""),
                r.get("source_platform", ""),
                json.dumps(r, ensure_ascii=False)
            ))
            refund_count += 1
            refund_total += total_pay
            
            # Update order refund amount
            if trade_code and total_pay > 0:
                c.execute("""
                    UPDATE orders SET
                        refund_amount = refund_amount + ?,
                        net_sales_amount = payment_amount - (refund_amount + ?)
                    WHERE trade_no = ? AND shop_name != '市场专用'
                """, (total_pay, total_pay, trade_code))
                
        except Exception as e:
            print(f"  ERROR importing refund {r.get('bill_code', '?')}: {e}")
            continue
    
    # Final recalculation of net_sales and gross
    c.execute("""
        UPDATE orders SET net_sales_amount = payment_amount - refund_amount
    """)
    conn.commit()
    return refund_count, refund_total


def main():
    print("=" * 60)
    print("FlashWater API 全量数据同步")
    print(f"日期范围: {START_DATE} ~ {END_DATE}")
    print("=" * 60)
    
    # Phase 1: Fetch trades
    print("\n[Phase 1] 拉取订单数据...")
    all_trades = []
    start = datetime.strptime(START_DATE, "%Y-%m-%d")
    end = datetime.strptime(END_DATE, "%Y-%m-%d")
    
    batch_start = start
    while batch_start < end:
        batch_end = min(batch_start + timedelta(days=BATCH_DAYS - 1), end)
        bs = batch_start.strftime("%Y-%m-%d")
        be = batch_end.strftime("%Y-%m-%d")
        print(f"\n  Batch: {bs} ~ {be}")
        
        trades = fetch_trades_batch(bs, be)
        all_trades.extend(trades)
        
        batch_start = batch_end + timedelta(days=1)
    
    print(f"\n  Total trades fetched: {len(all_trades)}")
    
    # Phase 2: Import trades to DB
    print(f"\n[Phase 2] 导入订单到数据库...")
    conn = get_conn()
    order_count, item_count = import_trades_to_db(conn, all_trades)
    print(f"  Orders: {order_count}, Items: {item_count}")
    
    # Phase 3: Fetch refunds
    print(f"\n[Phase 3] 拉取售后单数据...")
    all_refunds = []
    batch_start = start
    while batch_start < end:
        batch_end = min(batch_start + timedelta(days=BATCH_DAYS - 1), end)
        bs = batch_start.strftime("%Y-%m-%d")
        be = batch_end.strftime("%Y-%m-%d")
        print(f"\n  Batch: {bs} ~ {be}")
        
        refunds = fetch_refunds_batch(bs, be)
        all_refunds.extend(refunds)
        
        batch_start = batch_end + timedelta(days=1)
    
    print(f"\n  Total refunds fetched: {len(all_refunds)}")
    
    # Phase 4: Import refunds
    print(f"\n[Phase 4] 导入售后单...")
    refund_count, refund_total = import_refunds_to_db(conn, all_refunds)
    print(f"  Refund records: {refund_count}, Total refund: ¥{refund_total:,.2f}")
    
    # Phase 5: Recalculate costs with gift exclusion
    print(f"\n[Phase 5] 关联产品成本...")
    c = conn.cursor()
    
    # Load product costs
    cost_map = {}
    for row in c.execute("SELECT sku_code, unit_cost FROM product_cost").fetchall():
        cost_map[row[0]] = row[1]
    
    if cost_map:
        for sku, cost in cost_map.items():
            c.execute("""
                UPDATE order_items SET
                    unit_cost = ?,
                    item_total_cost = ? * size
                WHERE sku_code = ? AND is_gift = 0
            """, (cost, cost, sku))
        
        c.execute("""
            UPDATE orders SET
                total_cost = COALESCE(
                    (SELECT SUM(oi.item_total_cost) FROM order_items oi 
                     WHERE oi.order_uid = orders.uid AND oi.is_gift = 0), 0)
        """)
        c.execute("""
            UPDATE orders SET
                gross_profit = CASE WHEN net_sales_amount != 0 
                    THEN net_sales_amount - total_cost ELSE 0 END
        """)
        c.execute("""
            UPDATE orders SET
                gross_margin = CASE WHEN net_sales_amount != 0 
                    THEN ROUND((net_sales_amount - total_cost) / net_sales_amount * 100, 2) 
                    ELSE 0 END
        """)
        conn.commit()
        print(f"  Cost mapping: {len(cost_map)} SKUs")
    
    # Summary
    r = c.execute("""
        SELECT COUNT(*) as n,
               ROUND(SUM(payment_amount),2) as pay,
               ROUND(SUM(refund_amount),2) as refund,
               ROUND(SUM(net_sales_amount),2) as net
        FROM orders WHERE shop_name != '市场专用'
    """).fetchone()
    
    print(f"\n{'='*60}")
    print(f"同步完成! 数据摘要:")
    print(f"  订单数: {r[0]}")
    print(f"  付款金额: ¥{r[1]:,.2f}")
    print(f"  售后金额: ¥{r[2]:,.2f}")
    print(f"  净销售: ¥{r[3]:,.2f}")
    print(f"{'='*60}")
    
    conn.close()


if __name__ == "__main__":
    main()
