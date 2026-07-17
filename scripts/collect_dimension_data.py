"""
FlashWater 运营看板 — 扩展维度数据采集 v1
============================================
采集：产品数据（万里牛API）、库存数据（万里牛API）、物流数据（DB）
输出：data/dimension_data.json
"""
import sqlite3
import json
import subprocess
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'flashwater.db')
OUTPUT_PATH = os.path.join(DATA_DIR, 'dimension_data.json')

# CLI config
SKILL_DIR = os.path.expanduser(r'~\.workbuddy\skills\hupun-api-connector')
CLI = os.path.join(SKILL_DIR, 'assets', 'hupun-api-cli')
CFG = os.path.join(SKILL_DIR, 'assets', 'config.json')

PLATFORM_MAP = {
    'FLASH WATER旗舰店': '天猫',
    'FLASH WATER闪水口腔护理专卖店': '抖店',
    'Flash Water口腔护理的店': '小红书',
}


def call_api(path, params):
    """调用万里牛CLI API"""
    params_str = json.dumps(params, ensure_ascii=False)
    cmd = [CLI, '-c', CFG, path, params_str, '--agent', 'WorkBuddy']
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    
    if not stdout:
        print(f"  ⚠ API {path} 返回空响应: stderr={stderr[:200]}")
        return None
    
    # CLI sometimes outputs error info in stdout
    if 'error_message' in stdout and '"code":0' not in stdout:
        print(f"  ⚠ API {path} 返回错误: {stdout[:300]}")
        return None
    
    try:
        data = json.loads(stdout)
        return data
    except json.JSONDecodeError:
        print(f"  ⚠ API {path} JSON解析失败: {stdout[:200]}")
        return None


def fetch_products():
    """采集全部商品数据"""
    print("📦 采集商品数据...")
    result = call_api('/erp/goods/spec/open/query/goodswithspeclist', {
        'page': 1,
        'limit': 200,
        'modify_time': '2026-01-01 00:00:00',
        'end_time': '2026-07-16 23:59:59',
        'all_status': True
    })
    
    if not result or result.get('code') != 0:
        print(f"  ❌ 商品API失败: {result}")
        return []
    
    items = result.get('data', [])
    products = []
    for item in items:
        for spec in item.get('specs', []):
            products.append({
                'sku_code': spec.get('spec_code', ''),
                'goods_code': item.get('goods_code', ''),
                'goods_name': item.get('goods_name', ''),
                'spec_name': spec.get('spec2', '') or item.get('goods_name', ''),
                'barcode': spec.get('barcode', ''),
                'unit_cost': spec.get('prime_price', 0),  # 参考进价=成本均价
                'wholesale_price': spec.get('wholesale_price', 0),
                'sale_price': spec.get('sale_price', 0),
                'weight': spec.get('weight', 0),
                'status': spec.get('status', 1),
                'brand_name': item.get('brand_name', ''),
                'catagory_name': item.get('catagory_name', ''),
                'supplier_name': item.get('default_suply_name', ''),
                'modify_time': item.get('modify_time', ''),
            })
    
    print(f"  ✅ 共 {len(products)} 个SKU")
    return products


def fetch_inventory(sku_codes):
    """采集全部SKU库存"""
    print("📊 采集库存数据...")
    sku_str = ','.join(sku_codes)
    result = call_api('/erp/open/inventory/items/get/by/modifytimev2', {
        'sku_code': sku_str,
        'page_no': 1,
        'page_size': 200,
    })
    
    if not result or result.get('code') != 0:
        print(f"  ❌ 库存API失败: {result}")
        return []
    
    items = result.get('data', [])
    inventory = []
    for item in items:
        inventory.append({
            'sku_code': item.get('sku_code', ''),
            'goods_code': item.get('goods_code', ''),
            'spec_name': item.get('spec_name', ''),
            'warehouse_code': item.get('storage_code', ''),
            'quantity': item.get('quantity', 0),
            'lock_size': item.get('lock_size', 0),
            'underway': item.get('underway', 0),
            'defect_num': item.get('defect_num', 0),
            'cost_total': item.get('cost', 0),
            'unit_cost': item.get('last_stock', 0),
        })
    
    print(f"  ✅ 共 {len(inventory)} 条库存记录（{len(set(i['sku_code'] for i in inventory))} 个SKU）")
    return inventory


def query_logistics():
    """从DB查询物流分析数据"""
    print("🚚 采集物流数据...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 快递公司分布（已发货订单）
    carriers = conn.execute("""
        SELECT 
            logistic_name,
            COUNT(*) as orders,
            COUNT(DISTINCT shop_name) as shops
        FROM orders
        WHERE send_time IS NOT NULL AND send_time >= '2026-01-01'
        GROUP BY logistic_name
        ORDER BY orders DESC
    """).fetchall()
    
    carrier_data = [{
        'name': r['logistic_name'] or '未知',
        'orders': r['orders'],
        'shops': r['shops'],
    } for r in carriers]
    
    # 省份发货分布
    provinces = conn.execute("""
        SELECT 
            province,
            COUNT(*) as orders,
            SUM(payment_amount) as sales
        FROM orders
        WHERE send_time IS NOT NULL AND send_time >= '2026-06-01'
        GROUP BY province
        ORDER BY orders DESC
        LIMIT 20
    """).fetchall()
    
    province_data = [{
        'province': r['province'] or '未知',
        'orders': r['orders'],
        'sales': round(r['sales'] or 0, 2),
    } for r in provinces]
    
    # 每日发货趋势
    daily_ship = conn.execute("""
        SELECT 
            date(send_time) as dt,
            COUNT(*) as orders,
            COUNT(DISTINCT logistic_name) as carriers
        FROM orders
        WHERE send_time IS NOT NULL AND send_time >= '2026-06-01'
        GROUP BY dt
        ORDER BY dt
    """).fetchall()
    
    daily_ship_data = [{
        'date': r['dt'],
        'orders': r['orders'],
        'carriers': r['carriers'],
    } for r in daily_ship]

    # 每日快递分布（供前端按时间范围联动）
    daily_carrier = conn.execute("""
        SELECT 
            date(send_time) as dt,
            logistic_name,
            COUNT(*) as orders
        FROM orders
        WHERE send_time IS NOT NULL AND send_time >= '2026-06-01'
        GROUP BY dt, logistic_name
        ORDER BY dt, orders DESC
    """).fetchall()
    daily_carrier_data = [{
        'date': r['dt'],
        'name': r['logistic_name'] or '未知',
        'orders': r['orders'],
    } for r in daily_carrier]

    # 每日省份分布
    daily_province = conn.execute("""
        SELECT 
            date(send_time) as dt,
            province,
            COUNT(*) as orders,
            SUM(payment_amount) as sales
        FROM orders
        WHERE send_time IS NOT NULL AND send_time >= '2026-06-01'
        GROUP BY dt, province
        ORDER BY dt, orders DESC
    """).fetchall()
    daily_province_data = [{
        'date': r['dt'],
        'province': r['province'] or '未知',
        'orders': r['orders'],
        'sales': round(r['sales'] or 0, 2),
    } for r in daily_province]

    conn.close()
    
    print(f"  ✅ 物流: {len(carrier_data)} 家快递, {len(province_data)} 个省份, {len(daily_ship_data)} 天, +每日快递/省份明细")
    
    return {
        'carriers': carrier_data,
        'provinces': province_data,
        'daily_ship': daily_ship_data,
        'daily_carrier': daily_carrier_data,
        'daily_province': daily_province_data,
    }


def main():
    print("=" * 50)
    print("FlashWater 扩展维度数据采集")
    print("=" * 50)
    
    # 1. 产品数据
    products = fetch_products()
    
    # 2. 库存数据（用产品SKU列表查询）
    sku_codes = [p['sku_code'] for p in products]
    inventory = fetch_inventory(sku_codes) if sku_codes else []
    
    # 3. 物流数据
    logistics = query_logistics()
    
    # 合并输出
    output = {
        'generated_at': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'products': products,
        'inventory': inventory,
        'logistics': logistics,
    }
    
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 数据已保存到: {OUTPUT_PATH}")
    print(f"   - {len(products)} 个产品")
    print(f"   - {len(inventory)} 条库存记录")
    print(f"   - 物流: {len(logistics['carriers'])} 快递 + {len(logistics['provinces'])} 省份")


if __name__ == '__main__':
    main()
