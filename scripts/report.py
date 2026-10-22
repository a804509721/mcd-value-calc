# -*- coding: utf-8 -*-
"""把 result.json 渲染成 markdown 榜单。"""

METHOD = {
    'exact': '菜单名与营养表同名',
    'expanded': '规格容器按默认规格',
    'combo': '套餐按默认组成拆解',
    'code_map': '官方编码映射到完整名',
}


def _f(v, digits=1, suffix=''):
    if v is None:
        return '—'
    return ('%.' + str(digits) + 'f') % v + suffix


def _rank_table(title, rows, field, value_fmt, col_name, note=None):
    L = ['### ' + title, '']
    if not rows:
        L += ['（无可用数据）', '']
        return L
    L.append('| # | 商品 | 价格 | 蛋白质 | 热量 | %s | 匹配依据 |' % col_name)
    L.append('|---|---|---|---|---|---|---|')
    for i, r in enumerate(rows, 1):
        L.append('| %d | %s | ¥%.1f%s | %.1fg | %.0fkcal | %s | %s |' % (
            i, r['name'], r['price'], '*' if r.get('price_note') else '',
            r['protein'], r['kcal'],
            value_fmt(r[field]), METHOD.get(r['matched_by'], r['matched_by'])))
    if note:
        L.append('')
        L.append('> ' + note)
    L.append('')
    return L


def render_markdown(res):
    m, s = res['match'], res['store']
    L = ['# 麦当劳性价比榜单', '']
    L.append('- 门店：**%s**（storeCode `%s`）' % (s.get('storeName', ''), s.get('storeCode')))
    if s.get('address'):
        L.append('- 地址：%s' % s['address'])
    L.append('- 取餐方式：%s' % ('外送' if res['order_type'] == 2 else '到店自取'))
    L.append('- 生成时间：%s' % res['generated_at'])
    ns = res['nutrition_source']
    L.append('- 营养数据：官方营养表 %d 条（去重后可用 %d 条，采集于 %s）'
             % (ns['raw_count'], ns['usable'], ns['fetched_at']))
    L.append('- 菜单商品：%d 件（当前门店、当前时段实际在售）' % m['total_menu'])
    ps = res.get('price_stat') or {}
    if ps.get('basis') == 'actual':
        L.append('- 价格口径：官方 `calculate-price` 接口的实际成交价（%d/%d 件取到）'
                 % (ps.get('from_api', 0), ps.get('total', 0)))
        if ps.get('differs'):
            L.append('- 其中 %d 件的菜单展示价与成交价不同，多为需持卡或随单的优惠价，'
                     '榜单一律按成交价计算，差价商品在下方标记 `*`。' % len(ps['differs']))
    else:
        L.append('- 价格口径：菜单展示价（未调用价格接口）')
    L.append('')
    L.append('> 指标口径：蛋白质按 4 kcal/g 折算；蛋白质热量占比 = 蛋白质×4 ÷ 总热量。'
             '不含优惠券。套餐的营养值按官方默认组成相加。')
    L.append('')

    L.append('## 匹配率')
    L.append('')
    L.append('**%d / %d = %.1f%%** 的商品拿到了官方营养数据。' % (m['matched'], m['total_menu'], m['rate']))
    L.append('')
    bt = m['by_type']
    if bt:
        parts = ['%s %d 件' % (METHOD.get(k, k), v) for k, v in sorted(bt.items(), key=lambda kv: -kv[1])]
        L.append('- 匹配构成：' + '；'.join(parts))
    L.append('- 未匹配 %d 件，逐条列在下方并注明原因，未做任何估算，也未用同类商品顶替。' % m['unmatched'])
    L.append('')

    L += _rank_table('一、一块钱买到最多蛋白质', res['rank_protein_per_yuan'],
                     'protein_per_yuan', lambda v: _f(v, 2, ' g/元'), '性价比')
    L += _rank_table('二、一块钱买到最多热量', res['rank_kcal_per_yuan'],
                     'kcal_per_yuan', lambda v: _f(v, 1, ' kcal/元'), '性价比',
                     note='热量性价比高不等于健康，这一项只回答"同样一块钱能买到多少能量"。')
    L += _rank_table('三、蛋白质占总热量比例最高', res['rank_protein_ratio'],
                     'protein_kcal_pct', lambda v: _f(v, 1, '%'), '蛋白占比',
                     note='这一项衡量"这个商品本身有多高蛋白"，与价格无关。'
                          '黑咖啡类因热量极低会拿到很高的占比，但每份只有 1g 蛋白，'
                          '看这一榜请结合蛋白质绝对量和价格列一起判断。')

    b = res.get('budget')
    budget = b['budget'] if b else res.get('budget_setting', 30)
    L.append('## 预算 ¥%.0f 蛋白最大化组合' % budget)
    L.append('')
    if not b:
        L.append('（无可用商品）')
    else:
        L.append('| 商品 | 单价 | 数量 | 小计 | 蛋白质 | 热量 |')
        L.append('|---|---|---|---|---|---|')
        for c in b['combo']:
            L.append('| %s | ¥%.1f%s | %d | ¥%.1f | %.1fg | %.0fkcal |' % (
                c['name'], c['price'], '*' if c.get('price_note') else '',
                c['qty'], c['price'] * c['qty'], c['protein'], c['kcal']))
        L.append('| **合计** | | | **¥%.1f** | **%.1fg** | **%.0fkcal** |'
                 % (b['cost'], b['protein'], b['kcal']))
        L.append('')
        L.append('- 总花费 ¥%.1f，剩余 ¥%.1f；总蛋白 %.1fg，折合 %.2f g 蛋白/元。'
                 % (b['cost'], b['budget'] - b['cost'], b['protein'], b['protein_per_yuan']))
        L.append('- 求解方式：按 0.1 元离散化的整数背包精确求解，得到的是这个预算下的全局最优，不是贪心近似。')
        L.append('- 只在已匹配到官方营养数据的商品里选，未匹配商品不参与组合。')
    L.append('')

    if res['unmatched']:
        L.append('## 未匹配清单（%d 件）' % len(res['unmatched']))
        L.append('')
        L.append('| 商品 | 价格 | 原因 | 营养表中的相近条目 |')
        L.append('|---|---|---|---|')
        for r in res['unmatched']:
            p = ('¥%.1f' % r['price']) if r['price'] else '—'
            cand = '、'.join(r.get('candidates') or []) or '无'
            L.append('| %s | %s | %s | %s |' % (r['name'], p, r.get('reason') or '营养表无对应条目', cand))
        L.append('')
        L.append('> 最后一列只是名字相近的条目，用于说明差在哪一步，**未被采用**。'
                 '以上商品一律不计入榜单与组合，也没有用同类商品替代其营养值。')
        L.append('')

    L.append('---')
    L.append('')
    L.append('数据来源：麦当劳官方 MCP（`query-nearby-stores` / `query-meals` / '
             '`query-meal-detail` / `list-nutrition-foods` / `calculate-price`）。'
             '本工具只读取数据，未创建任何订单。')
    return '\n'.join(L)
