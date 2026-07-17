#!/usr/bin/env python3
"""
Sync inventory data from 万里牛 ERP API.

Usage:
    python sync_inventory.py              # Full sync (all pages)
    python sync_inventory.py --quick      # Quick sync (first 3 pages)

API: erp/open/inventory/items/get/by/modifytimev2
Returns per-SKU per-warehouse stock data.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from db_init import get_conn

CLI_PATH = os.path.join(
    os.path.expanduser("~"),
    ".workbuddy", "skills", "hupun-api-connector", "assets", "hupun-api-cli.exe"
)
CONFIG_PATH = os.path.join(
    os.path.expanduser("~"),
    ".workbuddy", "skills", "hupun-api-connector", "assets", "config.json"
)

PAGE_SIZE = 100


def run_cli(api_path, params):
    params_json = json.dumps(params, ensure_ascii=False)
    cmd = [CLI_PATH, "-c", CONFIG_PATH, api_path, params_json, "--agent", "WorkBuddy-BI-Dashboard"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0:
            print(f"  CLI error: {result.stderr.strip()}")
            return None
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except Exception as e:
        print(f"  Error: {e}")
        return None


def sync_inventory(conn, max_pages=10):
    """Pull full inventory from API and write to inventory_snapshot."""
    cursor = conn.cursor()

    # Clear old snapshot
    cursor.execute("DELETE FROM inventory_snapshot")
    conn.commit()

    page = 1
    total_items = 0

    while page <= max_pages:
        resp = run_cli("erp/open/inventory/items/get/by/modifytimev2", {
            "page_no": page,
            "page_size": PAGE_SIZE,
            "modify_time": "2026-01-01 00:00:00",
            "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        if resp is None or resp.get("code") != 0:
            print(f"  Page {page}: API error or empty")
            break

        data = resp.get("data", [])
        if not data:
            break

        for item in data:
            cursor.execute("""
                INSERT OR REPLACE INTO inventory_snapshot
                (sku_code, spec_name, goods_code, quantity, lock_size, underway,
                 defect_num, last_stock, storage_code, snapshot_time)
                VALUES (?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
            """, (
                item.get("sku_code"),
                item.get("spec_name"),
                item.get("goods_code"),
                item.get("quantity", 0),
                item.get("lock_size", 0),
                item.get("underway", 0),
                item.get("defect_num", 0),
                item.get("last_stock"),
                item.get("storage_code", "001"),
            ))

        total_items += len(data)
        print(f"  Page {page}: {len(data)} items")

        if len(data) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)

    conn.commit()

    # Summary
    cursor.execute("""
        SELECT COUNT(*) as total_items, COUNT(DISTINCT sku_code) as unique_skus,
               SUM(quantity) as total_qty, SUM(lock_size) as total_locked
        FROM inventory_snapshot
    """)
    s = dict(cursor.fetchone())
    print(f"\nInventory sync complete: {s['total_items']} rows, {s['unique_skus']} unique SKUs")
    print(f"Total quantity: {s['total_qty']}, Total locked: {s['total_locked']}")

    # Set low-stock alert thresholds for known products
    cursor.execute("""
        INSERT OR IGNORE INTO inventory_alert_config (sku_code, low_stock_threshold)
        SELECT sku_code, 50 FROM product_cost
    """)
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Sync inventory from 万里牛 API")
    parser.add_argument("--pages", type=int, default=10, help="Max pages to fetch")
    args = parser.parse_args()

    conn = get_conn()
    try:
        sync_inventory(conn, args.pages)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
