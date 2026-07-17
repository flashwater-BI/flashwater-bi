"""
FlashWater 运营看板 — 数据导出引擎 v16
========================================
新增：
- 三维度（付款/净销售/出库），每日聚合含BD标记
- 订单级成本计算（product_cost 兜底）
- 目标数据导出
- 商品排行含毛利率
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'flashwater.db')
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'dashboard_data.json')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')

PLATFORM_MAP = {
    'FLASH WATER旗舰店': '天猫',
    'FLASH WATER闪水口腔护理专卖店': '抖店',
    'Flash Water口腔护理的店': '小红书',
}
PLATFORM_ORDER = ['天猫', '抖店', '小红书']
EXCLUDE_MARKET = "AND o.shop_name != '市场专用'"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_cost_map(conn):
    """加载 product_cost → {sku_code: unit_cost}"""
    rows = conn.execute("SELECT sku_code, unit_cost FROM product_cost WHERE unit_cost > 0").fetchall()
    return {r['sku_code']: r['unit_cost'] for r in rows}


def calc_order_costs(conn, cost_map):
    """
    计算每笔订单的成本（含赠品——赠品也是实际发货成本）
    仅计算净销售>0的订单（排除全额退款订单）
    优先使用 order_items.unit_cost，否则用 product_cost.unit_cost 兜底
    返回 {order_uid: total_cost}
    """
    rows = conn.execute("""
        SELECT oi.order_uid, oi.sku_code, oi.size, oi.unit_cost
        FROM order_items oi
        INNER JOIN orders o ON oi.order_uid = o.uid
        WHERE o.net_sales_amount > 0 AND o.shop_name != '市场专用'
    """).fetchall()

    order_costs = defaultdict(float)
    for r in rows:
        uid = r['order_uid']
        size = r['size'] or 1
        uc = r['unit_cost']
        if uc and uc > 0:
            order_costs[uid] += size * uc
        else:
            fallback = cost_map.get(r['sku_code'], 0)
            order_costs[uid] += size * fallback
    
    return dict(order_costs)


def get_daily_data(conn, order_costs):
    """
    获取每日聚合：付款维度 + 出库维度
    每日 × 平台 × BD标记
    """
    print("  查询订单每日数据...")
    
    # 付款维度
    rows = conn.execute(f"""
        SELECT 
            substr(o.pay_time, 1, 10) as date,
            o.shop_name,
            CASE WHEN o.order_mark LIKE '%BD%' OR o.order_mark LIKE '%Bd%' THEN 1 ELSE 0 END as is_bd,
            COUNT(*) as orders,
            SUM(CASE WHEN o.net_sales_amount > 0 THEN 1 ELSE 0 END) as net_orders,
            SUM(o.payment_amount) as sales,
            SUM(o.refund_amount) as refund,
            SUM(o.net_sales_amount) as net_sales,
            GROUP_CONCAT(o.uid) as order_uids
        FROM orders o
        WHERE o.pay_time IS NOT NULL AND o.pay_time != ''
          AND o.shop_name != '市场专用'
        GROUP BY date, o.shop_name, is_bd
        ORDER BY date, o.shop_name, is_bd
    """).fetchall()

    payment_daily = []
    for r in rows:
        plat = PLATFORM_MAP.get(r['shop_name'])
        if not plat:
            continue
        cost = 0.0
        if r['order_uids']:
            for uid in r['order_uids'].split(','):
                cost += order_costs.get(uid, 0)
        
        payment_daily.append({
            'date': r['date'],
            'platform': plat,
            'is_bd': bool(r['is_bd']),
            'orders': r['orders'],
            'net_orders': r['net_orders'],
            'sales': round(r['sales'], 2),
            'refund': round(r['refund'], 2),
            'net_sales': round(r['net_sales'], 2),
            'cost': round(cost, 2),
        })

    # 出库维度
    print("  查询出库每日数据...")
    rows2 = conn.execute(f"""
        SELECT 
            substr(o.send_time, 1, 10) as date,
            o.shop_name,
            CASE WHEN o.order_mark LIKE '%BD%' OR o.order_mark LIKE '%Bd%' THEN 1 ELSE 0 END as is_bd,
            COUNT(*) as orders,
            SUM(CASE WHEN o.net_sales_amount > 0 THEN 1 ELSE 0 END) as net_orders,
            SUM(o.payment_amount) as sales,
            SUM(o.refund_amount) as refund,
            SUM(o.net_sales_amount) as net_sales,
            GROUP_CONCAT(o.uid) as order_uids
        FROM orders o
        WHERE o.send_time IS NOT NULL AND o.send_time != ''
          AND o.shop_name != '市场专用'
        GROUP BY date, o.shop_name, is_bd
        ORDER BY date, o.shop_name, is_bd
    """).fetchall()

    shipping_daily = []
    for r in rows2:
        plat = PLATFORM_MAP.get(r['shop_name'])
        if not plat:
            continue
        cost = 0.0
        if r['order_uids']:
            for uid in r['order_uids'].split(','):
                cost += order_costs.get(uid, 0)
        
        shipping_daily.append({
            'date': r['date'],
            'platform': plat,
            'is_bd': bool(r['is_bd']),
            'orders': r['orders'],
            'net_orders': r['net_orders'],
            'sales': round(r['sales'], 2),
            'refund': round(r['refund'], 2),
            'net_sales': round(r['net_sales'], 2),
            'cost': round(cost, 2),
        })

    return payment_daily, shipping_daily


def get_targets(conn):
    """获取目标数据"""
    rows = conn.execute("""
        SELECT shop_name, year, month, target_amount, target_orders
        FROM targets ORDER BY shop_name, year, month
    """).fetchall()
    return [
        {
            'platform': PLATFORM_MAP.get(r['shop_name'], r['shop_name']),
            'year': r['year'],
            'month': r['month'],
            'target_amount': r['target_amount'],
            'target_orders': r['target_orders'],
        }
        for r in rows
        if PLATFORM_MAP.get(r['shop_name'])
    ]


def get_product_ranking(conn, order_costs):
    """
    商品排行（含毛利率）
    使用付款时间维度，aggregate by item_name
    含赠品成本（赠品也是实际出库成本）
    """
    rows = conn.execute(f"""
        SELECT 
            oi.item_name,
            oi.sku_code,
            SUM(oi.size) as qty,
            SUM(oi.payment) as sales,
            COUNT(DISTINCT oi.order_uid) as order_count,
            SUM(CASE WHEN o.order_mark LIKE '%BD%' OR o.order_mark LIKE '%Bd%' THEN 1 ELSE 0 END) as bd_orders
        FROM order_items oi
        INNER JOIN orders o ON oi.order_uid = o.uid
        WHERE o.shop_name != '市场专用'
        GROUP BY oi.item_name
        ORDER BY sales DESC
        LIMIT 15
    """).fetchall()

    products = []
    for r in rows:
        # Calculate cost for this product (含赠品)
        item_cost = 0.0
        cost_rows = conn.execute("""
            SELECT oi.sku_code, oi.size, oi.unit_cost
            FROM order_items oi
            WHERE oi.item_name = ?
        """, (r['item_name'],)).fetchall()
        
        cost_map_local = load_cost_map(conn)
        for cr in cost_rows:
            size = cr['size'] or 1
            uc = cr['unit_cost']
            if uc and uc > 0:
                item_cost += size * uc
            else:
                item_cost += size * cost_map_local.get(cr['sku_code'], 0)
        
        margin = r['sales'] - item_cost
        margin_pct = (margin / r['sales'] * 100) if r['sales'] > 0 else 0
        
        products.append({
            'name': r['item_name'] or '(未命名)',
            'sku_code': r['sku_code'] or '',
            'qty': r['qty'],
            'sales': round(r['sales'], 2),
            'cost': round(item_cost, 2),
            'gross_margin': round(margin, 2),
            'margin_pct': round(margin_pct, 1),
            'orders': r['order_count'],
            'bd_orders': r['bd_orders'],
        })
    return products


def get_product_sales_daily(conn):
    """商品每日销售汇总，供前端按时间范围联动"""
    rows = conn.execute("""
        SELECT 
            substr(o.pay_time, 1, 10) as date,
            oi.item_name,
            oi.sku_code,
            SUM(oi.size) as qty,
            SUM(oi.payment) as sales,
            COUNT(DISTINCT oi.order_uid) as orders
        FROM order_items oi
        INNER JOIN orders o ON oi.order_uid = o.uid
        WHERE o.shop_name != '市场专用'
          AND o.pay_time IS NOT NULL
        GROUP BY date, oi.item_name
        ORDER BY date, sales DESC
    """).fetchall()

    result = []
    for r in rows:
        result.append({
            'date': r['date'],
            'item_name': r['item_name'],
            'sku_code': r['sku_code'],
            'qty': r['qty'],
            'sales': round(r['sales'], 2),
            'orders': r['orders'],
        })

    return result


def get_platform_summary(conn, order_costs):
    """各平台汇总（仅付款维度，全部时间）"""
    rows = conn.execute(f"""
        SELECT 
            o.shop_name,
            COUNT(*) as total_orders,
            SUM(o.payment_amount) as total_sales,
            SUM(o.refund_amount) as total_refund,
            SUM(o.net_sales_amount) as total_net,
            SUM(CASE WHEN o.order_mark LIKE '%BD%' OR o.order_mark LIKE '%Bd%' THEN 1 ELSE 0 END) as bd_count,
            MIN(substr(o.pay_time, 1, 10)) as first_date,
            MAX(substr(o.pay_time, 1, 10)) as last_date
        FROM orders o
        WHERE o.shop_name != '市场专用'
        GROUP BY o.shop_name
    """).fetchall()

    result = {}
    total_all = {'sales': 0, 'orders': 0, 'refund': 0, 'net': 0, 'cost': 0}
    for r in rows:
        plat = PLATFORM_MAP.get(r['shop_name'])
        if not plat:
            continue
        
        # Calculate total cost for this platform
        cost_rows = conn.execute("""
            SELECT oi.order_uid, oi.sku_code, oi.size, oi.unit_cost
            FROM order_items oi
            INNER JOIN orders o ON oi.order_uid = o.uid
            WHERE o.shop_name = ?
        """, (r['shop_name'],)).fetchall()
        
        cost_map_local = load_cost_map(conn)
        plat_cost = 0.0
        for cr in cost_rows:
            size = cr['size'] or 1
            uc = cr['unit_cost']
            if uc and uc > 0:
                plat_cost += size * uc
            else:
                plat_cost += size * cost_map_local.get(cr['sku_code'], 0)
        
        result[plat] = {
            'sales': round(r['total_sales'], 2),
            'orders': r['total_orders'],
            'refund': round(r['total_refund'], 2),
            'net': round(r['total_net'], 2),
            'cost': round(plat_cost, 2),
            'bd_count': r['bd_count'],
            'first_date': r['first_date'],
            'last_date': r['last_date'],
        }
        total_all['sales'] += r['total_sales']
        total_all['orders'] += r['total_orders']
        total_all['refund'] += r['total_refund']
        total_all['net'] += r['total_net']
        total_all['cost'] += plat_cost
    
    total_all['sales'] = round(total_all['sales'], 2)
    total_all['refund'] = round(total_all['refund'], 2)
    total_all['net'] = round(total_all['net'], 2)
    total_all['cost'] = round(total_all['cost'], 2)
    
    return result, total_all


def get_period_comparison(conn, order_costs):
    """
    计算月环比和周环比，三个维度（付款/净销售/出库）分别计算
    返回 { 'mom': { payment: {current, previous}, net_sales: {...}, shipping: {...} }, 'wow': {...} }
    """
    from datetime import datetime, timedelta
    
    now = datetime.now()
    
    def compute_payment(start, end):
        """付款维度：pay_time + 全部订单"""
        row = conn.execute("""
            SELECT 
                COUNT(*) as orders,
                SUM(payment_amount) as sales,
                SUM(refund_amount) as refund,
                SUM(net_sales_amount) as net_sales,
                SUM(CASE WHEN net_sales_amount > 0 THEN 1 ELSE 0 END) as net_orders,
                GROUP_CONCAT(uid) as order_uids
            FROM orders
            WHERE shop_name != '市场专用'
              AND pay_time >= ? AND pay_time < ?
        """, (start, end)).fetchone()
        return _build_metric(row, order_costs)
    
    def compute_net_sales(start, end):
        """净销售维度：pay_time + 仅净销售>0订单"""
        row = conn.execute("""
            SELECT 
                COUNT(*) as orders,
                SUM(net_sales_amount) as sales,
                SUM(refund_amount) as refund,
                SUM(net_sales_amount) as net_sales,
                COUNT(*) as net_orders,
                GROUP_CONCAT(uid) as order_uids
            FROM orders
            WHERE shop_name != '市场专用'
              AND net_sales_amount > 0
              AND pay_time >= ? AND pay_time < ?
        """, (start, end)).fetchone()
        return _build_metric(row, order_costs)
    
    def compute_shipping(start, end):
        """出库维度：send_time"""
        row = conn.execute("""
            SELECT 
                COUNT(*) as orders,
                SUM(payment_amount) as sales,
                SUM(refund_amount) as refund,
                SUM(net_sales_amount) as net_sales,
                SUM(CASE WHEN net_sales_amount > 0 THEN 1 ELSE 0 END) as net_orders,
                GROUP_CONCAT(uid) as order_uids
            FROM orders
            WHERE shop_name != '市场专用'
              AND send_time IS NOT NULL AND send_time != ''
              AND send_time >= ? AND send_time < ?
        """, (start, end)).fetchone()
        return _build_metric(row, order_costs)
    
    def _build_metric(row, order_costs):
        if not row or not row['orders']:
            return None
        cost = 0.0
        if row['order_uids']:
            for uid in row['order_uids'].split(','):
                cost += order_costs.get(uid, 0)
        sales = row['sales'] or 0
        orders = row['orders'] or 0
        refund = row['refund'] or 0
        net_sales = row['net_sales'] or 0
        net_orders = row['net_orders'] or 0
        margin = (sales - cost) / sales if sales > 0 else 0
        asp = sales / orders if orders > 0 else 0
        refund_rate = refund / sales if sales > 0 else 0
        return {
            'orders': orders,
            'sales': round(sales, 2),
            'refund': round(refund, 2),
            'net_sales': round(net_sales, 2),
            'net_orders': net_orders,
            'cost': round(cost, 2),
            'margin': round(margin, 4),
            'asp': round(asp, 2),
            'refund_rate': round(refund_rate, 4),
        }
    
    def compute_dims(start, end):
        """计算三个维度"""
        return {
            'payment': compute_payment(start, end),
            'net_sales': compute_net_sales(start, end),
            'shipping': compute_shipping(start, end),
        }
    
    # --- 月环比 ---
    curr_month_start = now.strftime('%Y-%m-01')
    curr_month_end = (now.replace(day=28) + timedelta(days=4)).replace(day=1).strftime('%Y-%m-%d')
    prev_month_end = curr_month_start
    prev_month_start = (datetime.strptime(curr_month_start, '%Y-%m-%d').replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d')
    
    current_month = compute_dims(curr_month_start, curr_month_end)
    previous_month = compute_dims(prev_month_start, prev_month_end)
    
    # --- 周环比 ---
    today = now.date()
    curr_week_monday = today - timedelta(days=today.weekday())
    curr_week_sunday = curr_week_monday + timedelta(days=7)
    prev_week_monday = curr_week_monday - timedelta(days=7)
    prev_week_sunday = curr_week_monday
    
    current_week = compute_dims(curr_week_monday.strftime('%Y-%m-%d'), curr_week_sunday.strftime('%Y-%m-%d'))
    previous_week = compute_dims(prev_week_monday.strftime('%Y-%m-%d'), prev_week_sunday.strftime('%Y-%m-%d'))
    
    result = {}
    
    def has_data(dims):
        """任一维度有数据就可以"""
        return any(v is not None for v in (dims or {}).values())
    
    if has_data(current_month) and has_data(previous_month):
        result['mom'] = {
            'period': '月环比',
            'current_label': curr_month_start[:7],
            'prev_label': prev_month_start[:7],
            'payment': {'current': current_month.get('payment'), 'previous': previous_month.get('payment')},
            'net_sales': {'current': current_month.get('net_sales'), 'previous': previous_month.get('net_sales')},
            'shipping': {'current': current_month.get('shipping'), 'previous': previous_month.get('shipping')},
        }
    
    if has_data(current_week) and has_data(previous_week):
        result['wow'] = {
            'period': '周环比',
            'current_label': f'{curr_week_monday.strftime("%m/%d")}-{curr_week_sunday.strftime("%m/%d")}',
            'prev_label': f'{prev_week_monday.strftime("%m/%d")}-{prev_week_sunday.strftime("%m/%d")}',
            'payment': {'current': current_week.get('payment'), 'previous': previous_week.get('payment')},
            'net_sales': {'current': current_week.get('net_sales'), 'previous': previous_week.get('net_sales')},
            'shipping': {'current': current_week.get('shipping'), 'previous': previous_week.get('shipping')},
        }
    
    return result


def get_day_comparison(conn, order_costs):
    """
    日环比：昨天 vs 前天，三个维度分别计算
    返回 { 'dod': { payment: {current, previous}, net_sales: {...}, shipping: {...} } }
    """
    from datetime import datetime, timedelta
    
    def compute_payment_day(date_str):
        """付款维度：某一天的聚合"""
        row = conn.execute("""
            SELECT 
                COUNT(*) as orders,
                SUM(payment_amount) as sales,
                SUM(refund_amount) as refund,
                SUM(net_sales_amount) as net_sales,
                SUM(CASE WHEN net_sales_amount > 0 THEN 1 ELSE 0 END) as net_orders,
                GROUP_CONCAT(uid) as order_uids
            FROM orders
            WHERE shop_name != '市场专用'
              AND pay_time >= ? AND pay_time < ?
        """, (date_str, date_str + " 23:59:59")).fetchone()
        return _build_day_metric(row, order_costs)
    
    def compute_net_sales_day(date_str):
        """净销售维度"""
        row = conn.execute("""
            SELECT 
                COUNT(*) as orders,
                SUM(net_sales_amount) as sales,
                SUM(refund_amount) as refund,
                SUM(net_sales_amount) as net_sales,
                COUNT(*) as net_orders,
                GROUP_CONCAT(uid) as order_uids
            FROM orders
            WHERE shop_name != '市场专用'
              AND net_sales_amount > 0
              AND pay_time >= ? AND pay_time < ?
        """, (date_str, date_str + " 23:59:59")).fetchone()
        return _build_day_metric(row, order_costs)
    
    def compute_shipping_day(date_str):
        """出库维度"""
        row = conn.execute("""
            SELECT 
                COUNT(*) as orders,
                SUM(payment_amount) as sales,
                SUM(refund_amount) as refund,
                SUM(net_sales_amount) as net_sales,
                SUM(CASE WHEN net_sales_amount > 0 THEN 1 ELSE 0 END) as net_orders,
                GROUP_CONCAT(uid) as order_uids
            FROM orders
            WHERE shop_name != '市场专用'
              AND send_time IS NOT NULL AND send_time != ''
              AND send_time >= ? AND send_time < ?
        """, (date_str, date_str + " 23:59:59")).fetchone()
        return _build_day_metric(row, order_costs)
    
    def _build_day_metric(row, order_costs):
        if not row or not row['orders']:
            return None
        cost = 0.0
        if row['order_uids']:
            for uid in row['order_uids'].split(','):
                cost += order_costs.get(uid, 0)
        sales = row['sales'] or 0
        orders = row['orders'] or 0
        return {
            'orders': orders,
            'sales': round(sales, 2),
            'refund': round(row['refund'] or 0, 2),
            'net_sales': round(row['net_sales'] or 0, 2),
            'net_orders': row['net_orders'] or 0,
            'cost': round(cost, 2),
            'margin': round((sales - cost) / sales, 4) if sales > 0 else 0,
            'asp': round(sales / orders, 2) if orders > 0 else 0,
            'refund_rate': round((row['refund'] or 0) / sales, 4) if sales > 0 else 0,
        }
    
    def compute_day_dims(date_str):
        return {
            'payment': compute_payment_day(date_str),
            'net_sales': compute_net_sales_day(date_str),
            'shipping': compute_shipping_day(date_str),
        }
    
    # T+1: yesterday is the most recent complete day
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    day_before = yesterday - timedelta(days=1)
    
    yesterday_str = yesterday.strftime('%Y-%m-%d')
    day_before_str = day_before.strftime('%Y-%m-%d')
    
    curr = compute_day_dims(yesterday_str)
    prev = compute_day_dims(day_before_str)
    
    if any(v is not None for v in curr.values()) and any(v is not None for v in prev.values()):
        result = {
            'period': '日环比',
            'current_label': yesterday_str,
            'prev_label': day_before_str,
            'payment': {'current': curr.get('payment'), 'previous': prev.get('payment')},
            'net_sales': {'current': curr.get('net_sales'), 'previous': prev.get('net_sales')},
            'shipping': {'current': curr.get('shipping'), 'previous': prev.get('shipping')},
        }
        return result
    return None


def main():
    print("=" * 60)
    print("FlashWater 运营看板 — 数据导出 v16")
    print("=" * 60)
    
    conn = get_conn()
    
    # 获取时间范围
    row = conn.execute(
        "SELECT MIN(pay_time), MAX(pay_time) FROM orders WHERE shop_name != '市场专用'"
    ).fetchone()
    min_date = row[0][:10] if row[0] else '2026-04-01'
    max_date = row[1][:10] if row[1] else datetime.now().strftime('%Y-%m-%d')
    current_month = max_date[:7]
    
    print(f"数据范围: {min_date} ~ {max_date}")
    print(f"当前月份: {current_month}")
    
    # 1. 加载成本映射
    print("\n[1/6] 加载成本数据...")
    cost_map = load_cost_map(conn)
    print(f"  SKU成本映射: {len(cost_map)} 条")
    
    # 2. 计算订单级成本
    print("[2/6] 计算订单成本...")
    order_costs = calc_order_costs(conn, cost_map)
    orders_with_cost = sum(1 for v in order_costs.values() if v > 0)
    print(f"  订单成本计算: {len(order_costs)} 条订单, {orders_with_cost} 条有成本")
    
    # 3. 每日聚合
    print("[3/6] 每日数据聚合...")
    payment_daily, shipping_daily = get_daily_data(conn, order_costs)
    print(f"  付款维度: {len(payment_daily)} 条")
    print(f"  出库维度: {len(shipping_daily)} 条")
    
    # 4. 目标数据
    print("[4/6] 导出目标数据...")
    targets = get_targets(conn)
    print(f"  目标记录: {len(targets)} 条")
    
    # 5a. 商品排行（累计）
    print("[5a/7] 商品排行（累计）...")
    products = get_product_ranking(conn, order_costs)
    print(f"  商品数: {len(products)}")
    
    # 5b. 商品每日销售（供前端时间联动）
    print("[5b/7] 商品每日销售...")
    product_sales_daily = get_product_sales_daily(conn)
    print(f"  记录数: {len(product_sales_daily)}")
    
    # 6. 平台汇总
    print("[6/7] 平台汇总...")
    platform_summary, total_summary = get_platform_summary(conn, order_costs)
    
    # 7. 环比计算（日环比 + 周环比 + 月环比）
    print("[7/8] 计算环比数据...")
    comparison = get_period_comparison(conn, order_costs)
    for k in comparison:
        pay_cur = comparison[k].get('payment', {}).get('current', {})
        pay_prev = comparison[k].get('payment', {}).get('previous', {})
        cur_s = (pay_cur or {}).get('sales', 0) or 0
        prev_s = (pay_prev or {}).get('sales', 0) or 0
        print(f"  {k}: 当前¥{cur_s:,.0f} vs 上期¥{prev_s:,.0f}")
    # 日环比
    dod = get_day_comparison(conn, order_costs)
    if dod:
        comparison['dod'] = dod
        dod_c = (dod.get('payment', {}).get('current', {}) or {}).get('sales', 0) or 0
        dod_p = (dod.get('payment', {}).get('previous', {}) or {}).get('sales', 0) or 0
        print(f"  dod: 昨日¥{dod_c:,.0f} vs 前日¥{dod_p:,.0f}")
    
    # 8. 加载维度数据（产品/库存/物流）
    print("[8/8] 加载扩展维度数据...")
    dim_path = os.path.join(DATA_DIR, 'dimension_data.json')
    dim_data = {}
    if os.path.exists(dim_path):
        with open(dim_path, 'r', encoding='utf-8') as f:
            dim_data = json.load(f)
        print(f"  产品: {len(dim_data.get('products', []))} SKU")
        print(f"  库存: {len(dim_data.get('inventory', []))} 条记录")
        logistics = dim_data.get('logistics', {})
        print(f"  物流: {len(logistics.get('carriers', []))} 快递, {len(logistics.get('provinces', []))} 省份")
    else:
        print("  ⚠ dimension_data.json 不存在，请先运行 collect_dimension_data.py")
    
    # ===== 组装输出 =====
    dashboard_data = {
        'meta': {
            'version': 'v16',
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_range': {'from': min_date, 'to': max_date},
            'current_month': current_month,
            'platforms': PLATFORM_ORDER,
            'note': '三维度数据(付款/出库)均已含BD标记可前端切换。成本仅计算净销售>0订单(排除全额退款)。环比标签跟随周期选择器。',
        },
        'targets': targets,
        'payment_daily': payment_daily,
        'shipping_daily': shipping_daily,
        'products': products,
        'product_sales_daily': product_sales_daily,
        'platform_summary': platform_summary,
        'total_summary': total_summary,
        'dim_products': dim_data.get('products', []),
        'dim_inventory': dim_data.get('inventory', []),
        'dim_logistics': dim_data.get('logistics', {}),
        'comparison': comparison,
    }
    
    # 写入
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(dashboard_data, f, ensure_ascii=False, indent=2)
    
    file_size = os.path.getsize(OUTPUT_PATH)
    print(f"\n✓ 数据已导出: {OUTPUT_PATH}")
    print(f"  文件大小: {file_size:,} bytes")
    
    # 打印月度摘要
    print(f"\n{current_month}支付维度摘要:")
    for plat in PLATFORM_ORDER:
        month_data = [d for d in payment_daily 
                      if d['date'].startswith(current_month) and d['platform'] == plat]
        sales = sum(d['sales'] for d in month_data)
        orders = sum(d['orders'] for d in month_data)
        refund = sum(d['refund'] for d in month_data)
        print(f"  {plat}: 销售额¥{sales:,.2f} | {orders}单 | 退款¥{refund:,.2f}")
    
    conn.close()
    print("\n完成。")


if __name__ == '__main__':
    main()
