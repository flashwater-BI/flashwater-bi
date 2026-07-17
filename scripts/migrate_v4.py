#!/usr/bin/env python3
"""
v4 Migration:
1. Add order_mark column to orders (订单标记, for BD filter)
2. Add net_sales_amount column to orders (净销售金额)
3. Update order_mark from Excel by matching trade_no
4. Set net_sales_amount = paid_fee initially
"""

import os
import sys
import sqlite3
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from db_init import get_conn, DB_PATH


def migrate():
    conn = get_conn()
    c = conn.cursor()

    # 1. Add new columns
    try:
        c.execute("ALTER TABLE orders ADD COLUMN order_mark TEXT")
        print("  + Added order_mark column")
    except sqlite3.OperationalError:
        print("  - order_mark column already exists")

    try:
        c.execute("ALTER TABLE orders ADD COLUMN net_sales_amount REAL DEFAULT 0")
        print("  + Added net_sales_amount column")
    except sqlite3.OperationalError:
        print("  - net_sales_amount column already exists")

    # 2. Set net_sales_amount = paid_fee where it's 0
    c.execute("UPDATE orders SET net_sales_amount = paid_fee WHERE net_sales_amount = 0")
    print(f"  + Set net_sales_amount = paid_fee for {c.rowcount} orders")

    # 3. Import order_mark from Excel
    excel_path = os.path.join(PROJECT_DIR, '全部订单源截止7月13日.xlsx')
    if not os.path.exists(excel_path):
        print(f"  ! Excel not found: {excel_path}")
        conn.commit()
        conn.close()
        return

    print(f"\nReading Excel for 订单标记: {excel_path}")
    df = pd.read_excel(excel_path, engine='openpyxl')

    # Build trade_no -> order_mark map (每个订单取第一个非空的标记)
    marks = {}
    for _, row in df.iterrows():
        trade_no = str(row.get('订单号', '')).strip()
        if not trade_no or trade_no == 'nan':
            continue
        mark = str(row.get('订单标记', '')).strip()
        if mark and mark != 'nan' and trade_no not in marks:
            marks[trade_no] = mark

    print(f"  Found {len(marks)} orders with 订单标记")

    # Update DB
    updated = 0
    for trade_no, mark in marks.items():
        c.execute("UPDATE orders SET order_mark = ? WHERE trade_no = ?", (mark, trade_no))
        if c.rowcount > 0:
            updated += 1

    print(f"  Updated {updated} orders with order_mark")

    # 4. Show stats
    print("\n=== order_mark distribution ===")
    for row in c.execute("""
        SELECT order_mark, COUNT(*) as cnt, ROUND(SUM(paid_fee), 2) as paid
        FROM orders WHERE order_mark IS NOT NULL AND order_mark != ''
        GROUP BY order_mark ORDER BY cnt DESC
    """):
        print(f"  {row['order_mark']:30s} | {row['cnt']:4d}单 | paid={row['paid']}")

    # BD orders specifically
    print("\n=== 专用BD orders ===")
    for row in c.execute("""
        SELECT COUNT(DISTINCT trade_no) as cnt,
               ROUND(SUM(paid_fee), 2) as paid
        FROM orders
        WHERE order_mark LIKE '%Bd%' OR order_mark LIKE '%BD%'
          AND pay_time IS NOT NULL AND is_pay = 1
    """):
        print(f"  含BD标记: {row['cnt']}单 | paid={row['paid']}")

    # Net sales comparison: with BD vs without BD
    print("\n=== Net sales comparison ===")
    for row in c.execute("""
        SELECT '含BD' as label, COUNT(DISTINCT trade_no) as o, ROUND(SUM(paid_fee), 2) as paid
        FROM orders WHERE pay_time IS NOT NULL AND is_pay = 1
        UNION ALL
        SELECT '不含BD', COUNT(DISTINCT trade_no), ROUND(SUM(paid_fee), 2)
        FROM orders WHERE pay_time IS NOT NULL AND is_pay = 1
        AND (order_mark IS NULL OR order_mark = '' OR (order_mark NOT LIKE '%Bd%' AND order_mark NOT LIKE '%BD%'))
    """):
        print(f"  {row['label']}: {row['o']}单 | paid={row['paid']}")

    conn.commit()
    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
