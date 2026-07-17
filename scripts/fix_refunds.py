#!/usr/bin/env python3
"""
Fix refund data: import missing refund orders from Excel and update has_refund flag.

Problem: import_history.py filters out rows with '退款' in 明细状态,
so all refund orders are missing from the database.

This script:
1. Reads the Excel to find ALL orders with refund items
2. Updates has_refund=1 for existing orders that have refund items
3. Imports refund-only orders that are completely missing from DB
"""

import os
import sys
import sqlite3
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from db_init import get_conn

EXCEL_FILE = os.path.join(PROJECT_DIR, "全部订单源截止7月13日.xlsx")


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
    cost_map = {}
    c = conn.cursor()
    for row in c.execute('SELECT sku_code, unit_cost FROM product_cost'):
        cost_map[row['sku_code']] = row['unit_cost']
    for row in c.execute('SELECT platform_sku, mapped_sku FROM sku_cost_mapping'):
        if row['mapped_sku'] in cost_map:
            cost_map[row['platform_sku']] = cost_map[row['mapped_sku']]
    return cost_map


def main():
    print(f"Reading {EXCEL_FILE}...")
    df = pd.read_excel(EXCEL_FILE, engine='openpyxl')

    # Find all orders that have refund items
    refund_rows = df[df['明细状态'].str.contains('退款', na=False)]
    print(f"Total refund rows in Excel: {len(refund_rows)}")

    # Get unique trade numbers for refund orders
    refund_trades = set(refund_rows['订单号'].unique())
    print(f"Unique refund order trade_nos: {len(refund_trades)}")

    conn = get_conn()
    cost_map = load_cost_map(conn)
    cursor = conn.cursor()
    item_cursor = conn.cursor()

    # Step 1: Update has_refund for existing orders
    updated = 0
    for trade_no in refund_trades:
        cursor.execute(
            "UPDATE orders SET has_refund = 1 WHERE trade_no = ? AND has_refund = 0",
            (trade_no,)
        )
        if cursor.rowcount > 0:
            updated += 1
    conn.commit()
    print(f"Updated has_refund=1 for {updated} existing orders")

    # Step 2: Find and import refund-only orders (not in DB yet)
    cursor.execute("SELECT trade_no FROM orders")
    db_trades = {r['trade_no'] for r in cursor.fetchall()}
    missing_trades = refund_trades - db_trades
    print(f"Missing refund orders to import: {len(missing_trades)}")

    if not missing_trades:
        print("All refund orders already in DB.")
    else:
        imported_orders = 0
        imported_items = 0

        for trade_no in missing_trades:
            group = df[df['订单号'] == trade_no]
            if len(group) == 0:
                continue

            # Use first non-refund row for order-level info, or first row if all refund
            non_refund = group[~group['明细状态'].str.contains('退款', na=False)]
            first = group.iloc[0] if len(non_refund) == 0 else non_refund.iloc[0]

            shop_name, platform = parse_shop_name(first.get('店铺名称'))
            uid = safe_str(first.get('系统单号')) or trade_no
            paid_fee = safe_float(first.get('应收总计'))
            real_payment = safe_float(first.get('买家实付'))
            pay_time = safe_str(first.get('付款时间'))
            create_time = safe_str(first.get('下单时间'))
            send_time = safe_str(first.get('发货时间'))
            end_time = safe_str(first.get('完成时间'))
            province = safe_str(first.get('省'))
            city = safe_str(first.get('市'))
            district = safe_str(first.get('区'))
            buyer_show = safe_str(first.get('收货人'))

            # Calculate costs - only for non-refund items
            order_total_cost = 0.0
            items_data = []
            for _, row in group.iterrows():
                status = safe_str(row.get('明细状态'))
                sku_code = safe_str(row.get('商品编码'))
                qty = safe_float(row.get('数量'), 1.0)

                # Don't count refund items for cost
                if status and '退款' in status:
                    continue

                unit_cost = cost_map.get(sku_code, 0.0)
                item_total_cost = unit_cost * qty
                order_total_cost += item_total_cost

                items_data.append({
                    'sku_code': sku_code,
                    'item_name': safe_str(row.get('商品名称')),
                    'oln_sku_name': safe_str(row.get('线上规格')) or safe_str(row.get('规格名称')),
                    'price': safe_float(row.get('单价')),
                    'payment': safe_float(row.get('应收')),
                    'discounted_unit_price': safe_float(row.get('折后单价')),
                    'size': int(qty) if qty == int(qty) else qty,
                    'unit_cost': unit_cost,
                    'item_total_cost': item_total_cost,
                })

            gross_profit = paid_fee - order_total_cost
            gross_margin = (gross_profit / paid_fee * 100) if paid_fee > 0 else 0

            # Check if already exists
            cursor.execute("SELECT uid FROM orders WHERE trade_no = ? LIMIT 1", (trade_no,))
            if cursor.fetchone():
                continue

            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO orders (
                        uid, trade_no, shop_name, source_platform,
                        paid_fee, real_payment, pay_time, create_time, send_time, end_time,
                        province, city, district, buyer_show,
                        is_pay, has_refund, total_cost, gross_profit, gross_margin
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?,?,?)
                """, (
                    uid, trade_no, shop_name, platform,
                    paid_fee, real_payment, pay_time, create_time, send_time, end_time,
                    province, city, district, buyer_show,
                    order_total_cost, gross_profit, gross_margin
                ))

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

                imported_orders += 1
                imported_items += len(items_data)

                if imported_orders % 100 == 0:
                    conn.commit()
                    print(f"  Progress: {imported_orders} orders...")

            except sqlite3.IntegrityError:
                continue

        conn.commit()
        print(f"Imported {imported_orders} refund orders, {imported_items} items")

    # Step 3: Verify fix
    cursor.execute("SELECT COUNT(*) as cnt, SUM(paid_fee) as total FROM orders WHERE is_pay=1 AND has_refund=1")
    r = cursor.fetchone()
    print(f"\nRefund verification: {r['cnt']} orders with has_refund=1, ¥{r['total']:.2f} total paid")

    # Monthly refund breakdown
    cursor.execute("""
        SELECT strftime('%Y-%m', pay_time) AS ym,
            COUNT(DISTINCT trade_no) AS total_paid,
            SUM(CASE WHEN has_refund=1 THEN 1 ELSE 0 END) AS refund_orders,
            ROUND(SUM(CASE WHEN has_refund=1 THEN paid_fee ELSE 0 END), 2) AS refund_amount,
            ROUND(
                CAST(SUM(CASE WHEN has_refund=1 THEN 1 ELSE 0 END) AS FLOAT)
                / NULLIF(COUNT(DISTINCT trade_no), 0) * 100, 2
            ) AS refund_rate
        FROM orders
        WHERE is_pay=1 AND pay_time IS NOT NULL
        GROUP BY ym ORDER BY ym
    """)
    print("\nMonthly refund breakdown:")
    for r in cursor.fetchall():
        print(f"  {r['ym']}: {r['total_paid']} paid, {r['refund_orders']} refunds ({r['refund_rate']}%), ¥{r['refund_amount']}")

    conn.close()
    print("\nRefund fix complete!")


if __name__ == "__main__":
    main()
