#!/usr/bin/env python3
"""
Fetch orders from 万里牛 ERP API and store in SQLite.

Usage:
    python fetch_orders.py                    # Fetch today's data
    python fetch_orders.py --from 2026-06-01  # Fetch from a start date
    python fetch_orders.py --from 2026-06-01 --to 2026-07-14
    python fetch_orders.py --days 7           # Fetch last N days
    python fetch_orders.py --shop "FLASH WATER旗舰店"  # Filter by shop
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

# Add scripts directory to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from db_init import get_conn, DB_PATH

# Paths
CLI_PATH = os.path.join(
    os.path.expanduser("~"),
    ".workbuddy", "skills", "hupun-api-connector", "assets", "hupun-api-cli.exe"
)
CONFIG_PATH = os.path.join(
    os.path.expanduser("~"),
    ".workbuddy", "skills", "hupun-api-connector", "assets", "config.json"
)

PAGE_SIZE = 200  # Max per page
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def ts_to_str(ts_ms):
    """Convert 13-digit millisecond timestamp to datetime string."""
    if ts_ms and ts_ms > 0:
        try:
            return datetime.fromtimestamp(ts_ms / 1000).strftime(DATE_FORMAT)
        except (OSError, ValueError):
            return None
    return None


def normalize_date(d):
    """Normalize date string: replace / with - for SQLite compatibility."""
    if d and isinstance(d, str) and '/' in d:
        return d.replace('/', '-')
    return d


def run_cli(api_path, params):
    """Run hupun-api-cli and return parsed JSON response."""
    params_json = json.dumps(params, ensure_ascii=False)
    cmd = [
        CLI_PATH, "-c", CONFIG_PATH, api_path, params_json,
        "--agent", "WorkBuddy-BI-Dashboard"
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            print(f"  CLI error: {result.stderr.strip()}")
            return None
        if not result.stdout.strip():
            print("  CLI returned empty response")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print("  CLI timeout")
        return None
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        print(f"  Raw output: {result.stdout[:500]}")
        return None


def fetch_orders_page(date_start, date_end, page=1, shop_name=None):
    """Fetch one page of orders for a date range."""
    params = {
        "page": page,
        "limit": PAGE_SIZE,
        "modify_time": date_start,
        "end_time": date_end
    }
    # Note: 万里牛 API filters by shop_nick, not shop_name
    # We'll filter in Python instead
    return run_cli("erp/opentrade/list/trades", params)


def save_order(conn, order, cursor, item_cursor):
    """Save a single order and its items to the database."""
    uid = order.get("uid")
    if not uid:
        return 0

    # Check if already exists (by uid OR by trade_no to prevent duplicates from Excel+API overlap)
    cursor.execute("SELECT uid FROM orders WHERE uid = ? OR trade_no = ?", (uid, order.get("trade_no", "")))
    if cursor.fetchone():
        return 0  # Skip existing

    # Insert order - all columns explicitly listed
    cursor.execute("""
        INSERT OR IGNORE INTO orders (
            uid, trade_no, shop_name, shop_nick, sys_shop, source_platform,
            shop_type, storage_name, storage_code,
            create_time, pay_time, send_time, end_time, approve_time,
            modify_time, index_time,
            status, process_status, oln_status,
            paid_fee, real_payment, sum_sale, discount_fee, post_fee,
            post_cost, commision, service_fee,
            has_refund, is_pay,
            buyer_show, province, city, district,
            pay_type, logistic_name, trade_type, tp_tid,
            weight, volume,
            is_exception_trade, exchange_trade,
            remark, buyer_msg, seller_msg,
            raw_json
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?
        )
    """, (
        uid,
        order.get("trade_no"),
        order.get("shop_name"),
        order.get("shop_nick"),
        order.get("sys_shop"),
        order.get("source_platform"),
        order.get("shop_type"),
        order.get("storage_name"),
        order.get("storage_code"),
        normalize_date(ts_to_str(order.get("create_time"))),
        normalize_date(ts_to_str(order.get("pay_time"))),
        normalize_date(ts_to_str(order.get("send_time"))),
        normalize_date(ts_to_str(order.get("end_time"))),
        normalize_date(ts_to_str(order.get("approve_time"))),
        normalize_date(ts_to_str(order.get("modify_time"))),
        normalize_date(ts_to_str(order.get("index_time"))),
        order.get("status"),
        order.get("process_status"),
        order.get("oln_status"),
        order.get("paid_fee"),
        order.get("real_payment"),
        order.get("sum_sale"),
        order.get("discount_fee"),
        order.get("post_fee"),
        order.get("post_cost"),
        order.get("commision"),
        order.get("service_fee"),
        order.get("has_refund"),
        1 if order.get("is_pay") else 0,
        order.get("buyer_show"),
        order.get("province"),
        order.get("city"),
        order.get("district"),
        order.get("pay_type"),
        order.get("logistic_name"),
        order.get("trade_type"),
        order.get("tp_tid"),
        order.get("weight"),
        order.get("volume"),
        1 if order.get("is_exception_trade") else 0,
        1 if order.get("exchange_trade") else 0,
        order.get("remark"),
        order.get("buyer_msg"),
        order.get("seller_msg"),
        json.dumps(order, ensure_ascii=False)
    ))

    # Check if order contains any gift items
    items = order.get("orders", [])
    has_gift = any(item.get("is_gift") == 1 for item in items)

    # Update is_gift flag
    cursor.execute("UPDATE orders SET is_gift = ? WHERE uid = ?", (1 if has_gift else 0, uid))

    # Insert order items
    for item in items:
        item_cursor.execute("""
            INSERT INTO order_items (
                order_uid, order_detail_id,
                item_name, oln_item_name,
                sku_code, oln_sku_name, oln_sku_code,
                price, payment, receivable,
                discounted_unit_price, order_total_discount, origin_price,
                size, is_gift, is_package, has_refund,
                inventory_status,
                sys_goods_uid, sys_spec_uid,
                tp_tid, tp_oid,
                oln_item_id, oln_sku_id,
                bar_code,
                tax_rate, tax_payment, storage_fee,
                traffic_sources, unit, remark
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,
                ?
            )
        """, (
            uid,
            item.get("order_id"),
            item.get("item_name"),
            item.get("oln_item_name"),
            item.get("sku_code"),
            item.get("oln_sku_name"),
            item.get("oln_sku_code"),
            item.get("price"),
            item.get("payment"),
            item.get("receivable"),
            item.get("discounted_unit_price"),
            item.get("order_total_discount"),
            item.get("origin_price"),
            item.get("size"),
            1 if item.get("is_gift") else 0,
            1 if item.get("is_package") else 0,
            item.get("has_refund"),
            item.get("inventory_status"),
            item.get("sys_goods_uid"),
            item.get("sys_spec_uid"),
            item.get("tp_tid"),
            item.get("tp_oid"),
            item.get("oln_item_id"),
            item.get("oln_sku_id"),
            item.get("bar_code"),
            item.get("tax_rate"),
            item.get("tax_payment"),
            item.get("storage_fee"),
            item.get("traffic_sources"),
            item.get("unit"),
            item.get("remark"),
        ))

    return 1


def fetch_date_range(conn, date_start, date_end, shop_filter=None):
    """Fetch all orders for a date range (max 7 days)."""
    cursor = conn.cursor()
    item_cursor = conn.cursor()

    page = 1
    total_saved = 0

    print(f"  Fetching {date_start} to {date_end}...")

    while True:
        resp = fetch_orders_page(date_start, date_end, page)
        if resp is None:
            print(f"  Failed to fetch page {page}, stopping.")
            break

        if resp.get("code") != 0:
            err_msg = resp.get("error_message", resp.get("message", "Unknown error"))
            print(f"  API error on page {page}: {err_msg}")
            break

        data = resp.get("data", [])
        if not data:
            break

        page_saved = 0
        for order in data:
            # Optional shop filter (filter by shop_name after fetch)
            if shop_filter and order.get("shop_name") != shop_filter:
                continue
            page_saved += save_order(conn, order, cursor, item_cursor)

        conn.commit()
        total_saved += page_saved
        print(f"  Page {page}: {len(data)} orders, {page_saved} new, total new: {total_saved}")

        if len(data) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)  # Rate limiting

    return total_saved


def fetch_last_n_days(conn, days=1, shop_filter=None):
    """Fetch orders for the last N days, chunked into 7-day ranges."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    print(f"Fetching orders from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")

    total_saved = 0
    current_start = start_date

    while current_start < end_date:
        chunk_end = min(current_start + timedelta(days=6), end_date)
        date_start_str = current_start.strftime(DATE_FORMAT)
        date_end_str = (chunk_end + timedelta(days=1) - timedelta(seconds=1)).strftime(DATE_FORMAT)

        saved = fetch_date_range(conn, date_start_str, date_end_str, shop_filter)
        total_saved += saved

        current_start = chunk_end + timedelta(days=1)

    return total_saved


def main():
    parser = argparse.ArgumentParser(description="Fetch orders from 万里牛 ERP API")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=1, help="Fetch last N days (default: 1)")
    parser.add_argument("--shop", help="Filter by shop name")
    parser.add_argument("--init-db", action="store_true", help="Initialize database first")

    args = parser.parse_args()

    if args.init_db:
        import db_init
        db_init.init_db()

    conn = get_conn()

    try:
        if args.from_date:
            start = datetime.strptime(args.from_date, "%Y-%m-%d")
            end = datetime.strptime(args.to_date, "%Y-%m-%d") if args.to_date else datetime.now()

            # Chunk into 7-day ranges
            total_saved = 0
            current_start = start
            while current_start < end:
                chunk_end = min(current_start + timedelta(days=6), end)
                date_start_str = current_start.strftime(DATE_FORMAT)
                date_end_str = (chunk_end + timedelta(days=1) - timedelta(seconds=1)).strftime(DATE_FORMAT)

                saved = fetch_date_range(conn, date_start_str, date_end_str, args.shop)
                total_saved += saved
                current_start = chunk_end + timedelta(days=1)

            print(f"\nTotal new orders saved: {total_saved}")
        else:
            total_saved = fetch_last_n_days(conn, args.days, args.shop)
            print(f"\nTotal new orders saved: {total_saved}")

        # Print summary
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM orders")
        total = cursor.fetchone()["cnt"]
        cursor.execute("SELECT shop_name, COUNT(*) as cnt FROM orders GROUP BY shop_name ORDER BY cnt DESC")
        shops = cursor.fetchall()
        print(f"\nDatabase summary: {total} total orders")
        for s in shops:
            print(f"  {s['shop_name']}: {s['cnt']} orders")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
