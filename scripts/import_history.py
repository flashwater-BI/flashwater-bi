#!/usr/bin/env python3
"""
Import historical order data from 万里牛 Excel export into SQLite (v6).
Key changes v6:
- Keep ALL rows including refund rows (no more filtering out)
- payment_amount = 买家实付 (付款金额维度)
- refund_amount = 退款行应收合计 (售后金额维度)
- net_sales_amount = payment_amount - refund_amount
- commission_amount = 交易佣金 + 信用卡佣金
- has_refund = 1 if order has 关联售后单
- Only non-refund rows become order_items

Usage:
    python import_history.py "全部订单源截止7月13日.xlsx"
"""

import argparse
import os
import sys
import sqlite3
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from db_init import get_conn, DB_PATH


def safe_float(v, default=0.0):
    if pd.isna(v) or v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def safe_str(v):
    if pd.isna(v) or v is None:
        return None
    return str(v).strip()


def parse_shop_name(shop_str):
    """Parse '[平台]店铺名' -> (shop_name, platform)."""
    if not shop_str:
        return (None, None)
    s = shop_str.strip()
    if s.startswith('[') and ']' in s:
        end = s.index(']')
        platform = s[1:end]
        name = s[end+1:].strip()
        return (name, platform)
    return (s, None)


def load_cost_map(conn):
    """Build SKU->cost map including platform variants."""
    cost_map = {}
    c = conn.cursor()
    for row in c.execute('SELECT sku_code, unit_cost FROM product_cost'):
        cost_map[row['sku_code']] = row['unit_cost']
    for row in c.execute('SELECT platform_sku, mapped_sku FROM sku_cost_mapping'):
        if row['mapped_sku'] in cost_map:
            cost_map[row['platform_sku']] = cost_map[row['mapped_sku']]
    return cost_map


def import_excel(conn, filepath):
    """Import ALL Excel rows into SQLite. Refund rows are tracked but not inserted as order_items."""
    print(f"Reading {filepath}...")
    df = pd.read_excel(filepath, engine='openpyxl')
    print(f"  Rows: {len(df)}, Columns: {len(df.columns)}")

    cost_map = load_cost_map(conn)
    cursor = conn.cursor()
    item_cursor = conn.cursor()

    # Identify order-level columns (repeated per row for same order)
    order_groups = df.groupby('订单号')

    total_orders = 0
    total_items = 0
    skipped = 0
    refund_tracked = 0

    for trade_no, group in order_groups:
        # Check if already exists
        cursor.execute("SELECT uid FROM orders WHERE trade_no = ? LIMIT 1", (trade_no,))
        if cursor.fetchone():
            skipped += 1
            continue

        first = group.iloc[0]
        shop_name_full = safe_str(first.get('店铺名称'))
        shop_name, platform = parse_shop_name(shop_name_full)

        uid = safe_str(first.get('系统单号'))
        if not uid:
            uid = trade_no

        # --- Payment dimension ---
        payment_amount = safe_float(first.get('买家实付'))
        paid_fee = safe_float(first.get('应收总计'))

        # --- Refund calculation ---
        # Refund rows: rows where 明细状态 contains '退款' AND 关联售后单 is not empty
        refund_rows = group[
            group['明细状态'].str.contains('退款', na=False) &
            group['关联售后单'].notna() & (group['关联售后单'] != '')
        ]
        has_refund = 1 if len(refund_rows) > 0 else 0

        # Refund amount: sum of 应收 on refund rows (these are positive values in the Excel)
        refund_amount = safe_float(refund_rows['应收'].sum()) if len(refund_rows) > 0 else 0.0
        if has_refund:
            refund_tracked += 1

        # --- Net sales（不再钳制负数，保持与万里牛一致）---
        net_sales_amount = payment_amount - refund_amount

        # --- Commission ---
        commission_amount = safe_float(first.get('交易佣金')) + safe_float(first.get('信用卡佣金'))

        # --- Dates ---
        pay_time = safe_str(first.get('付款时间'))
        create_time = safe_str(first.get('下单时间'))
        send_time = safe_str(first.get('发货时间'))
        end_time = safe_str(first.get('完成时间'))

        # Normalize date format (replace '/' with '-' for SQLite compatibility)
        pay_time = pay_time.replace('/', '-') if pay_time and '/' in pay_time else pay_time
        create_time = create_time.replace('/', '-') if create_time and '/' in create_time else create_time
        send_time = send_time.replace('/', '-') if send_time and '/' in send_time else send_time
        end_time = end_time.replace('/', '-') if end_time and '/' in end_time else end_time

        # pay_time fallback to create_time（否则看板日期分组会遗漏这些订单）
        if not pay_time and create_time:
            pay_time = create_time

        # --- Address ---
        province = safe_str(first.get('省'))
        city = safe_str(first.get('市'))
        district = safe_str(first.get('区'))
        buyer_show = safe_str(first.get('收货人'))

        # --- Order mark ---
        order_mark = safe_str(first.get('订单标记'))

        # --- is_pay: based on 明细状态 ---
        detail_statuses = set(group['明细状态'].dropna().astype(str))
        is_pay = 1 if not any(s in detail_statuses for s in ['待付款', '未付款']) else 0

        # --- Order items: only non-refund rows ---
        non_refund = group[~group['明细状态'].str.contains('退款', na=False)]
        order_total_cost = 0.0
        items_data = []

        for _, row in non_refund.iterrows():
            sku_code = safe_str(row.get('商品编码'))
            qty = safe_float(row.get('数量'), 1.0)
            unit_cost = cost_map.get(sku_code, 0.0)
            item_total_cost = unit_cost * qty
            order_total_cost += item_total_cost

            items_data.append({
                'sku_code': sku_code,
                'item_name': safe_str(row.get('商品名称')),
                'oln_sku_name': safe_str(row.get('【线上】规格')) or safe_str(row.get('规格名称')),
                'price': safe_float(row.get('单价')),
                'payment': safe_float(row.get('应收')),
                'discounted_unit_price': safe_float(row.get('折后单价')),
                'size': int(qty) if qty == int(qty) else qty,
                'unit_cost': unit_cost,
                'item_total_cost': item_total_cost,
            })

        # --- Profit calculation (based on net_sales_amount) ---
        gross_profit = net_sales_amount - order_total_cost
        gross_margin = (gross_profit / net_sales_amount * 100) if net_sales_amount > 0 else 0

        # --- Insert order ---
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO orders (
                    uid, trade_no, shop_name, source_platform,
                    payment_amount, refund_amount, net_sales_amount,
                    commission_amount,
                    paid_fee, real_payment,
                    pay_time, create_time, send_time, end_time,
                    province, city, district, buyer_show,
                    is_pay, has_refund,
                    order_mark,
                    total_cost, gross_profit, gross_margin
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                uid, trade_no, shop_name, platform,
                payment_amount, refund_amount, net_sales_amount,
                commission_amount,
                paid_fee, payment_amount,
                pay_time, create_time, send_time, end_time,
                province, city, district, buyer_show,
                is_pay, has_refund,
                order_mark,
                order_total_cost, gross_profit, gross_margin
            ))
        except sqlite3.IntegrityError:
            skipped += 1
            continue

        # --- Insert order items (non-refund only) ---
        for item in items_data:
            item_cursor.execute("""
                INSERT INTO order_items (
                    order_uid, item_name, sku_code, oln_sku_name,
                    price, payment, discounted_unit_price, size,
                    is_gift, unit_cost, item_total_cost
                ) VALUES (?,?,?,?,?,?,?,?,0,?,?)
            """, (
                uid, item['item_name'], item['sku_code'], item['oln_sku_name'],
                item['price'], item['payment'], item['discounted_unit_price'], item['size'],
                item['unit_cost'], item['item_total_cost']
            ))

        total_orders += 1
        total_items += len(items_data)

        if total_orders % 2000 == 0:
            conn.commit()
            print(f"  Progress: {total_orders} orders, {total_items} items, {refund_tracked} with refunds...")

    conn.commit()
    print(f"\nImport complete:")
    print(f"  Orders: {total_orders} (new), {skipped} skipped")
    print(f"  Items: {total_items}")
    print(f"  With refunds: {refund_tracked}")
    return total_orders


def recalc_api_orders(conn):
    """Recalculate costs and net_sales for orders from API (not Excel)."""
    cost_map = load_cost_map(conn)
    c = conn.cursor()

    # Fix order_items costs
    c.execute("SELECT COUNT(*) as cnt FROM order_items WHERE item_total_cost = 0 AND size > 0")
    need_fix = c.fetchone()['cnt']
    print(f"  Items needing cost fix: {need_fix}")

    if need_fix > 0:
        items = c.execute("""
            SELECT oi.id, oi.sku_code, oi.size, oi.order_uid
            FROM order_items oi WHERE oi.item_total_cost = 0 AND oi.size > 0
        """).fetchall()

        for item in items:
            sku = item['sku_code'] or ''
            qty = item['size'] or 0
            uc = cost_map.get(sku, 0.0)
            itc = uc * qty
            c.execute("UPDATE order_items SET unit_cost=?, item_total_cost=? WHERE id=?",
                      (uc, itc, item['id']))

        conn.commit()
        print(f"  Fixed {len(items)} items")

    # Update order-level costs
    c.execute("""
        UPDATE orders SET
            total_cost = COALESCE((SELECT SUM(oi.item_total_cost) FROM order_items oi WHERE oi.order_uid = orders.uid), 0),
            gross_profit = CASE WHEN net_sales_amount > 0
                THEN net_sales_amount - COALESCE((SELECT SUM(oi.item_total_cost) FROM order_items oi WHERE oi.order_uid = orders.uid), 0)
                ELSE 0 END,
            gross_margin = CASE WHEN net_sales_amount > 0
                THEN ROUND((net_sales_amount - COALESCE((SELECT SUM(oi.item_total_cost) FROM order_items oi WHERE oi.order_uid = orders.uid), 0)) / net_sales_amount * 100, 2)
                ELSE 0 END
        WHERE total_cost = 0 OR gross_profit = 0
    """)
    conn.commit()
    print(f"  Order costs recalculated.")

    # For API orders without net_sales_amount set, use payment_amount as net_sales
    c.execute("""
        UPDATE orders SET 
            net_sales_amount = payment_amount,
            gross_profit = payment_amount - total_cost,
            gross_margin = CASE WHEN payment_amount > 0 
                THEN ROUND((payment_amount - total_cost) / payment_amount * 100, 2) 
                ELSE 0 END
        WHERE net_sales_amount = 0 AND payment_amount > 0
    """)
    conn.commit()
    print(f"  Fixed {c.rowcount} orders (net_sales = payment_amount).")


def main():
    parser = argparse.ArgumentParser(description='Import history orders from Excel (v6)')
    parser.add_argument('file', nargs='?',
                        help='Excel file path (default: 全部订单源截止7月13日.xlsx)')
    parser.add_argument('--recalc', action='store_true',
                        help='Only recalculate costs for existing orders')
    args = parser.parse_args()

    conn = get_conn()

    try:
        if args.recalc:
            recalc_api_orders(conn)
            return

        filepath = args.file or os.path.join(PROJECT_DIR, '全部订单源截止7月13日.xlsx')
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            sys.exit(1)

        total = import_excel(conn, filepath)

        # Summary
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM orders")
        db_total = c.fetchone()['cnt']
        c.execute("""
            SELECT shop_name, COUNT(*) as cnt,
                ROUND(SUM(payment_amount),2) as payment,
                ROUND(SUM(refund_amount),2) as refund,
                ROUND(SUM(net_sales_amount),2) as net_sales
            FROM orders WHERE shop_name != '市场专用'
            GROUP BY shop_name ORDER BY payment DESC
        """)
        shops = c.fetchall()
        c.execute("SELECT MIN(pay_time) as d1, MAX(pay_time) as d2 FROM orders WHERE pay_time IS NOT NULL")
        dr = c.fetchone()

        print(f"\n{'='*65}")
        print(f"Database summary: {db_total} total orders")
        print(f"Date range: {dr['d1']} ~ {dr['d2']}")
        print(f"{'店铺':<35} {'订单':>6} {'付款金额':>12} {'售后金额':>12} {'净销售':>12}")
        print(f"{'-'*65}")
        for s in shops:
            print(f"{s['shop_name']:<35} {s['cnt']:>6} ¥{s['payment']:>10,.2f} ¥{s['refund']:>10,.2f} ¥{s['net_sales']:>10,.2f}")

        # Compare with 万里牛
        c.execute("""
            SELECT ROUND(SUM(payment_amount),2) as total_payment,
                   ROUND(SUM(refund_amount),2) as total_refund,
                   ROUND(SUM(net_sales_amount),2) as total_net_sales,
                   ROUND(SUM(commission_amount),2) as total_commission,
                   COUNT(DISTINCT trade_no) as order_count
            FROM orders WHERE shop_name != '市场专用'
        """)
        r = c.fetchone()
        print(f"\n{'='*65}")
        print(f"vs 万里牛 (3/1-7/13):")
        print(f"  付款金额: ¥{r['total_payment']:,.2f} vs ¥461,359 (万里牛)")
        print(f"  售后金额: ¥{r['total_refund']:,.2f} vs ¥132,816 (万里牛)")
        print(f"  净销售:   ¥{r['total_net_sales']:,.2f} vs ¥328,543 (万里牛)")
        print(f"  订单数:   {r['order_count']}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
