# -*- coding: utf-8 -*-
"""
mcd_value.py — 麦当劳性价比计算器（数据全部来自麦当劳官方 MCP）

链路：门店 -> 菜单(含售价) -> 官方营养表 -> 名称匹配 -> 三项指标排名 -> 预算最优组合 -> 价格校验

铁律：
  * 营养数据只认官方营养表，匹配不上就标"未匹配"，绝不估算、绝不用同类商品顶替。
  * 每条匹配结果都带 matched_by，可逐条溯源。
  * 只读数据，不创建订单。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SKILL_DIR, 'cache')
MCP_CONFIG = os.path.join(os.path.expanduser('~'), '.workbuddy', 'mcp.json')


# ---------------------------------------------------------------- MCP 客户端
class Mcp:
    def __init__(self, url=None, token=None):
        self.url, self.token = url, token
        if not self.url:
            self._from_config()
        self.rid = 0

    def _from_config(self):
        url = os.environ.get('MCD_MCP_URL')
        token = os.environ.get('MCD_MCP_TOKEN')
        if not (url and token) and os.path.exists(MCP_CONFIG):
            cfg = json.load(open(MCP_CONFIG, encoding='utf-8'))
            for name, c in (cfg.get('mcpServers') or {}).items():
                if 'mcd' in name.lower():
                    url = url or c.get('url')
                    h = {k.lower(): v for k, v in (c.get('headers') or {}).items()}
                    token = token or re.sub(r'^Bearer\s+', '', h.get('authorization', ''))
        self.url = url or 'https://mcp.mcd.cn'
        self.token = token or ''
        if not self.token:
            raise SystemExit('未找到麦当劳 MCP 凭证：请在 ~/.workbuddy/mcp.json 配置 mcd-mcp，'
                             '或设置环境变量 MCD_MCP_TOKEN')

    def call(self, tool, args=None, retries=3):
        self.rid += 1
        body = json.dumps({'jsonrpc': '2.0', 'id': self.rid, 'method': 'tools/call',
                           'params': {'name': tool, 'arguments': args or {}}}).encode('utf-8')
        req = urllib.request.Request(self.url, data=body, method='POST', headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
        })
        last = None
        for i in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode('utf-8'))
                if 'error' in data:
                    raise RuntimeError('MCP error: %s' % data['error'])
                res = data.get('result', {})
                sc = res.get('structuredContent')
                if sc is None:
                    txt = ''.join(c.get('text', '') for c in res.get('content', []))
                    m = re.search(r'\{"success".*\}', txt, re.S)
                    sc = json.loads(m.group(0)) if m else {'raw': txt}
                return sc
            except Exception as e:
                last = e
                time.sleep(1.2 * (i + 1))
        raise RuntimeError('%s 调用失败: %s' % (tool, last))


def cache_path(*parts):
    p = os.path.join(CACHE_DIR, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def cached_json(path, loader, max_age_h=6):
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        try:
            return json.load(open(path, encoding='utf-8'))
        except Exception:
            pass
    data = loader()
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    return data


# ---------------------------------------------------------------- 名称归一化
def norm(name):
    """字符级归一化：去空白/商标符，统一全半角括号。不做语义猜测。"""
    if not name:
        return ''
    s = name.strip()
    for w in ('麦咖啡', '™', '®', '©', ' ', '\u3000', '\u200b', '\n'):
        s = s.replace(w, '')
    out = []
    for ch in s:
        o = ord(ch)
        if o == 0xFF08 or o == 0xFF09 or o == 0x28 or o == 0x29:
            continue
        if 0xFF01 <= o <= 0xFF5E:
            ch = chr(o - 0xFEE0)
        out.append(ch)
    return ''.join(out).lower()


# 人工可核对的等价别名（菜单名 -> 营养表名）
ALIAS = {
    '纯牛奶': '纯牛奶（盒装）',
    '100%苹果汁': '100%苹果汁',
    '100% 苹果汁': '100%苹果汁',
}


def resolve(name, index):
    k = norm(name)
    if k in index:
        return index[k], k
    if name in ALIAS:
        k2 = norm(ALIAS[name])
        if k2 in index:
            return index[k2], k2
    return None, k


# ---------------------------------------------------------------- 营养表
def load_nutrition(mcp, refresh=False):
    path = cache_path('nutrition.json')

    def fetch():
        sc = mcp.call('list-nutrition-foods')
        raw = sc.get('data')
        if not isinstance(raw, str):
            raise RuntimeError('营养数据格式异常: %r' % (raw,))
        m = re.search(r'\{([^}]*)\}', raw)
        header = [x.strip() for x in m.group(1).split(',')]
        rows, zero = [], []
        for ln in raw.split('\n'):
            p = [x.strip() for x in ln.split(',')]
            if len(p) != len(header) or not p[0] or p[0][0] in '{[':
                continue
            d = dict(zip(header, p))
            try:
                d['_protein'] = float(d['protein'])
                d['_kcal'] = float(d['energyKcal'])
            except (TypeError, ValueError):
                continue
            if d['_kcal'] < 0:
                continue
            if d['_kcal'] == 0:
                zero.append(d['productName'])
            rows.append(d)
        seen, dup = {}, []
        for r in rows:
            k = norm(r['productName'])
            if k in seen:
                dup.append(r['productName'])
                continue
            seen[k] = r
        return {'rows': list(seen.values()), 'dup_dropped': dup,
                'zero_kcal': zero, 'raw_count': len(rows),
                'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S')}
    if refresh and os.path.exists(path):
        os.remove(path)
    return cached_json(path, fetch)


# ---------------------------------------------------------------- 采集
def pick_store(mcp, city, keyword, be_type=1, store_code=None):
    if store_code:
        return {'storeCode': store_code, 'storeName': '(指定 storeCode)', 'address': ''}
    sc = mcp.call('query-nearby-stores',
                  {'beType': be_type, 'searchType': 2, 'city': city, 'keyword': keyword})
    if not sc.get('success'):
        raise SystemExit('门店查询失败: %s' % sc.get('message'))
    stores = sc.get('data') or []
    if not stores:
        raise SystemExit('未找到门店：%s %s' % (city, keyword))
    s = next((x for x in stores if x.get('businessStatus')), stores[0])
    return {'storeCode': s['storeCode'], 'storeName': s.get('storeName', ''),
            'address': s.get('address', ''), 'beCode': s.get('beCode'),
            'businessStatus': s.get('businessStatus'),
            'candidates': [{'storeCode': x['storeCode'], 'storeName': x.get('storeName')}
                           for x in stores[:5]]}


def load_meals(mcp, store, order_type, be_type, refresh=False):
    path = cache_path('meals_%s_%s_%s.json' % (store['storeCode'], order_type, be_type))
    if refresh and os.path.exists(path):
        os.remove(path)

    def fetch():
        args = {'storeCode': store['storeCode'], 'orderType': order_type, 'beType': be_type}
        if be_type == 5 and store.get('beCode'):
            args['beCode'] = store['beCode']
        sc = mcp.call('query-meals', args)
        if not sc.get('success'):
            raise SystemExit('菜单查询失败: %s' % sc.get('message'))
        return sc['data']
    return cached_json(path, fetch, max_age_h=2)


def load_detail(mcp, store, code, order_type, be_type, refresh=False):
    path = cache_path('detail', '%s_%s_%s.json' % (store['storeCode'], order_type, code))

    def fetch():
        args = {'storeCode': store['storeCode'], 'orderType': order_type,
                'beType': be_type, 'code': str(code)}
        if be_type == 5 and store.get('beCode'):
            args['beCode'] = store['beCode']
        try:
            sc = mcp.call('query-meal-detail', args, retries=2)
        except Exception as e:
            return {'_error': str(e)[:100]}
        if not sc.get('success'):
            return {'_error': (sc.get('message') or 'fail')[:100]}
        return sc['data']
    if refresh and os.path.exists(path):
        os.remove(path)
    return cached_json(path, fetch, max_age_h=12)


def build_code_index(details):
    """官方数据内部自证：套餐 choices 里出现的 code -> 完整商品名。
    同一个 code 若对应多个不同名称则不采信（避免张冠李戴）。"""
    acc = {}
    for d in details.values():
        if not isinstance(d, dict) or d.get('_error'):
            continue
        for r in d.get('rounds') or []:
            for c in r.get('choices') or []:
                code, nm = str(c.get('code')), c.get('name')
                if code and nm:
                    acc.setdefault(code, set()).add(nm)
    return {k: sorted(v) for k, v in acc.items()}


# ---------------------------------------------------------------- 匹配引擎
COMBO_PAT = re.compile(r'组合|套餐|三件套|双人|家庭|桶')


def match_item(code, name, index, detail, code_index, expand):
    """返回 {status, matched_by, groups, reason}
    匹配顺序：菜单名精确 -> 详情展开(规格容器/套餐) -> code 映射 -> 未匹配
    """
    hit, _ = resolve(name, index)
    if hit:
        return {'status': 'ok', 'matched_by': 'exact',
                'groups': [{'nut': hit, 'qty': 1}]}

    if not expand:
        return {'status': 'unmatched', 'reason': '营养表无同名条目（本次未展开详情）', 'groups': []}

    if not isinstance(detail, dict) or detail.get('_error'):
        err = detail.get('_error') if isinstance(detail, dict) else 'no detail'
        return {'status': 'unmatched', 'reason': '详情获取失败：%s' % err, 'groups': []}

    rounds = detail.get('rounds') or []

    if rounds:
        is_combo = bool(COMBO_PAT.search(name)) or len(rounds) >= 2
        if not is_combo and len(rounds) == 1:
            ch = rounds[0].get('choices') or []
            chosen = [c for c in ch if c.get('isDefault') == 1] or ch[:1]
            if not chosen:
                return {'status': 'unmatched', 'reason': '规格容器但无可选规格', 'groups': []}
            c = chosen[0]
            hit2, _ = resolve(c['name'], index)
            if hit2 is None and code_index.get(str(c.get('code'))):
                nm = code_index[str(c.get('code'))]
                if len(nm) == 1:
                    hit2, _ = resolve(nm[0], index)
            if hit2:
                return {'status': 'ok', 'matched_by': 'expanded',
                        'groups': [{'nut': hit2, 'qty': 1}],
                        'note': '菜单为规格容器，按默认规格「%s」计' % c['name']}
            return {'status': 'unmatched', 'reason': '默认规格「%s」营养表无对应' % c['name'],
                    'groups': []}

        # 套餐：每个分组取默认项，逐项匹配；任一项缺失则整体不计
        groups, missing = [], []
        for r in rounds:
            ch = r.get('choices') or []
            chosen = [c for c in ch if c.get('isDefault') == 1] or ch[:1]
            if not chosen:
                missing.append('%s(无默认项)' % (r.get('name') or '?'))
                continue
            for c in chosen:
                hit2, _ = resolve(c['name'], index)
                if hit2 is None and code_index.get(str(c.get('code'))):
                    nm = code_index[str(c.get('code'))]
                    if len(nm) == 1:
                        hit2, _ = resolve(nm[0], index)
                if hit2:
                    groups.append({'nut': hit2, 'qty': 1, 'part': c['name']})
                else:
                    missing.append(c['name'])
        if missing:
            return {'status': 'unmatched', 'groups': [],
                    'reason': '套餐组成中「%s」营养表无对应' % '、'.join(missing[:3])}
        if not groups:
            return {'status': 'unmatched', 'reason': '套餐无默认组成', 'groups': []}
        return {'status': 'ok', 'matched_by': 'combo', 'groups': groups,
                'note': '套餐按各分组默认项拆解相加'}

    # rounds 为空：说明该 code 本身就是某个具体商品，用官方 choices 里的完整名反查
    names = code_index.get(str(code)) or []
    if len(names) == 1:
        hit3, _ = resolve(names[0], index)
        if hit3:
            return {'status': 'ok', 'matched_by': 'code_map',
                    'groups': [{'nut': hit3, 'qty': 1}],
                    'note': '菜单显示为统称，官方编码对应「%s」' % names[0]}
        return {'status': 'unmatched', 'groups': [],
                'reason': '官方编码对应「%s」，营养表无此条目' % names[0]}
    if len(names) > 1:
        return {'status': 'unmatched', 'groups': [],
                'reason': '同一编码对应多个名称（%s），无法判定' % '、'.join(names[:3])}
    return {'status': 'unmatched', 'groups': [],
            'reason': '菜单只给统称，详情与官方编码均未标注具体规格'}


def near_names(name, index, limit=4):
    """给未匹配项找营养表里名字相近的条目，仅作提示，绝不采用。"""
    k = norm(name)
    if not k:
        return []
    out = []
    for nk, row in index.items():
        if nk == k:
            continue
        if k in nk or nk in k:
            out.append(row['productName'])
    out.sort(key=len)
    return out[:limit]


# ---------------------------------------------------------------- 指标
def build_table(rows):
    for r in rows:
        if r['status'] != 'ok' or not r.get('price') or r['price'] <= 0:
            r['protein_per_yuan'] = r['kcal_per_yuan'] = r['protein_kcal_pct'] = None
            continue
        r['protein_per_yuan'] = r['protein'] / r['price']
        r['kcal_per_yuan'] = r['kcal'] / r['price']
        r['protein_kcal_pct'] = (r['protein'] * 4.0 / r['kcal'] * 100.0) if r['kcal'] > 0 else None
    return rows


def rank(rows, field, topn):
    ok = [r for r in rows if r['status'] == 'ok' and r.get(field) is not None]
    ok.sort(key=lambda r: r[field], reverse=True)
    return ok[:topn]


# ---------------------------------------------------------------- 预算组合
def budget_combo(rows, budget, allow_repeat=True):
    items = [r for r in rows if r['status'] == 'ok' and r['price'] > 0]
    if not items:
        return None
    W = int(round(budget * 10))
    dp = [None] * (W + 1)
    dp[0] = (0.0, 0.0, ())
    if allow_repeat:
        for w in range(W + 1):
            cur = dp[w]
            if cur is None:
                continue
            for i, it in enumerate(items):
                nw = w + int(round(it['price'] * 10))
                if nw > W:
                    continue
                val = (cur[0] + it['protein'], cur[1] + it['kcal'])
                if dp[nw] is None or val[0] > dp[nw][0] + 1e-9:
                    dp[nw] = (val[0], val[1], cur[2] + (i,))
    else:
        for i, it in enumerate(items):
            cost = int(round(it['price'] * 10))
            for w in range(W, cost - 1, -1):
                if dp[w - cost] is None:
                    continue
                prev = dp[w - cost]
                val = (prev[0] + it['protein'], prev[1] + it['kcal'])
                if dp[w] is None or val[0] > dp[w][0] + 1e-9:
                    dp[w] = (val[0], val[1], prev[2] + (i,))
    best_w, best = None, None
    for w in range(W + 1):
        if dp[w] is not None and (best is None or dp[w][0] > best[0] + 1e-9):
            best_w, best = w, dp[w]
    if best is None:
        return None
    protein, kcal, picks = best
    agg = {}
    for i in picks:
        agg[i] = agg.get(i, 0) + 1
    combo = []
    for i, n in sorted(agg.items(), key=lambda kv: -items[kv[0]]['protein'] * kv[1]):
        it = items[i]
        combo.append({'name': it['name'], 'code': it['code'], 'price': it['price'], 'qty': n,
                      'original_price': it.get('original_price') or it['price'],
                      'price_note': it.get('price_note', ''),
                      'protein': round(it['protein'] * n, 1), 'kcal': round(it['kcal'] * n, 1)})
    return {'protein': round(protein, 1), 'kcal': round(kcal, 1),
            'cost': round(best_w / 10.0, 1), 'budget': budget, 'combo': combo,
            'protein_per_yuan': round(protein / (best_w / 10.0), 2) if best_w else 0}


# ---------------------------------------------------------------- 价格（官方价格接口）
def fetch_actual_prices(mcp, store, codes, order_type, be_type, batch=12):
    """用官方 calculate-price 拿每个商品的真实成交单价。
    返回 {code: {'unit': float, 'orig_unit': float, 'ok': bool}}
    单位从"分"换算成"元"。取不到的 code 不出现在结果里，由调用方回退并标记。
    """
    out, codes = {}, list(codes)
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        args = {'storeCode': store['storeCode'], 'orderType': order_type, 'beType': be_type,
                'items': [{'productCode': str(c), 'quantity': 1} for c in chunk]}
        if be_type == 5 and store.get('beCode'):
            args['beCode'] = store['beCode']
        try:
            sc = mcp.call('calculate-price', args, retries=2)
        except Exception:
            continue
        if not sc.get('success'):
            continue
        for p in (sc.get('data', {}) or {}).get('productList', []) or []:
            q = p.get('quantity') or 1
            try:
                out[str(p['productCode'])] = {
                    'unit': round((p.get('subtotal') or 0) / 100.0 / q, 2),
                    'orig_unit': round((p.get('originalSubtotal') or 0) / 100.0 / q, 2),
                    'ok': True,
                }
            except (TypeError, ValueError, ZeroDivisionError):
                continue
    return out



# ---------------------------------------------------------------- 主流程
def run(args):
    mcp = Mcp()
    t0 = time.time()
    store = pick_store(mcp, args.city, args.keyword, args.be_type, args.store)
    nut = load_nutrition(mcp, refresh=args.refresh)
    index = {norm(r['productName']): r for r in nut['rows']}
    meals_data = load_meals(mcp, store, args.order_type, args.be_type, refresh=args.refresh)
    meals = meals_data['meals']

    expand = not args.no_expand
    actual = {}
    if args.price_basis == 'actual':
        actual = fetch_actual_prices(mcp, store, list(meals.keys()),
                                     args.order_type, args.be_type)
        if not actual:
            # 菜单缓存可能已过可售时段（时段切换后复跑/夜间复跑），
            # 价格接口会对全部过期编码报 60006「售罄或不可售」。
            # 处理：丢弃菜单缓存重拉，再试一次。
            meals_data = load_meals(mcp, store, args.order_type, args.be_type, refresh=True)
            meals = meals_data['meals']
            actual = fetch_actual_prices(mcp, store, list(meals.keys()),
                                         args.order_type, args.be_type)
    details = {}
    if expand:
        for code in meals:
            details[str(code)] = load_detail(mcp, store, code, args.order_type,
                                             args.be_type, refresh=args.refresh)
    code_index = build_code_index(details)

    rows = []
    for code, m in meals.items():
        name = m.get('name') or ''
        try:
            menu_price = float(m.get('currentPrice'))
        except (TypeError, ValueError):
            menu_price = None
        try:
            origin = float(m.get('originalPrice'))
        except (TypeError, ValueError):
            origin = menu_price
        act = actual.get(str(code))
        price = act['unit'] if act else menu_price
        dt = m.get('discountType') or ''
        conditional = bool(m.get('withOrder')) or ('早餐卡' in dt) or ('随单' in dt)
        mt = match_item(str(code), name, index, details.get(str(code)), code_index, expand)
        r = {'code': str(code), 'name': name, 'price': price,
             'price_source': 'calculate-price' if act else '菜单展示价',
             'menu_price': menu_price, 'original_price': origin,
             'price_note': ('菜单显示 ¥%.1f，官方成交价 ¥%.1f（%s）' % (menu_price, price, dt)
                            if act and menu_price is not None and abs(menu_price - price) > 0.011
                            else ''),
             'discount': dt, 'conditional_discount': conditional,
             'status': mt['status'], 'matched_by': mt.get('matched_by'),
             'note': mt.get('note', ''), 'reason': mt.get('reason', '')}
        if mt['status'] != 'ok':
            r['candidates'] = near_names(name, index)
        if mt['status'] == 'ok':
            r['protein'] = round(sum(g['nut']['_protein'] * g['qty'] for g in mt['groups']), 1)
            r['kcal'] = round(sum(g['nut']['_kcal'] * g['qty'] for g in mt['groups']), 1)
            r['parts'] = [g.get('part') or g['nut']['productName'] for g in mt['groups']]
        rows.append(r)

    rows = build_table(rows)
    rk1 = rank(rows, 'protein_per_yuan', args.top)
    rk2 = rank(rows, 'kcal_per_yuan', args.top)
    rk3 = rank(rows, 'protein_kcal_pct', args.top)
    combo = budget_combo(rows, args.budget, allow_repeat=not args.once)

    price_stat = {'basis': args.price_basis, 'total': len(rows),
                  'from_api': sum(1 for r in rows if r['price_source'] == 'calculate-price'),
                  'fallback': sum(1 for r in rows if r['price_source'] != 'calculate-price'),
                  'conditional': [r['name'] for r in rows if r['conditional_discount']],
                  'differs': [{'name': r['name'], 'menu': r['menu_price'], 'actual': r['price']}
                              for r in rows
                              if r['menu_price'] is not None and r['price'] is not None
                              and abs(r['menu_price'] - r['price']) > 0.011]}

    ok = [r for r in rows if r['status'] == 'ok']
    unmatched = [r for r in rows if r['status'] != 'ok']
    by_type = {}
    for r in ok:
        by_type[r['matched_by']] = by_type.get(r['matched_by'], 0) + 1

    return {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'store': store, 'order_type': args.order_type, 'be_type': args.be_type,
        'budget_setting': args.budget,
        'nutrition_source': {'raw_count': nut['raw_count'], 'usable': len(nut['rows']),
                             'dup_dropped': nut['dup_dropped'], 'zero_kcal': nut['zero_kcal'],
                             'fetched_at': nut['fetched_at']},
        'match': {'total_menu': len(rows), 'matched': len(ok), 'unmatched': len(unmatched),
                  'rate': round(100.0 * len(ok) / len(rows), 1) if rows else 0.0,
                  'by_type': by_type},
        'rank_protein_per_yuan': rk1, 'rank_kcal_per_yuan': rk2, 'rank_protein_ratio': rk3,
        'budget': combo, 'price_stat': price_stat,
        'items': rows, 'unmatched': unmatched, 'elapsed_s': round(time.time() - t0, 1),
    }


def main():
    ap = argparse.ArgumentParser(description='麦当劳性价比计算器（官方 MCP 数据，只读不下单）')
    ap.add_argument('--city', default='西安')
    ap.add_argument('--keyword', default='钟楼')
    ap.add_argument('--store', default=None, help='直接指定 storeCode，跳过门店搜索')
    ap.add_argument('--be-type', type=int, default=1, dest='be_type',
                    help='1 到店自取 / 5 得来速 / 2 麦乐送 / 6 团餐')
    ap.add_argument('--order-type', type=int, default=1, dest='order_type', help='1 到店 / 2 外送')
    ap.add_argument('--budget', type=float, default=30.0)
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--once', action='store_true', help='预算组合中每样最多一份')
    ap.add_argument('--no-expand', action='store_true', dest='no_expand', help='不展开详情（快，匹配率低）')
    ap.add_argument('--price-basis', choices=['actual', 'menu'], default='actual',
                    dest='price_basis',
                    help='actual=用官方 calculate-price 的成交价（默认，严谨）；menu=用菜单展示价（快）')
    ap.add_argument('--refresh', action='store_true', help='忽略缓存重新拉取')
    ap.add_argument('--out', default=None, help='结果 JSON 路径')
    args = ap.parse_args()

    res = run(args)
    out = args.out or os.path.join(CACHE_DIR, 'result.json')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(res, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    from report import render_markdown
    md = render_markdown(res)
    md_path = os.path.splitext(out)[0] + '.md'
    open(md_path, 'w', encoding='utf-8').write(md)
    print(md)
    print('\n---\nJSON: %s\nMD:   %s' % (out, md_path))


if __name__ == '__main__':
    main()
