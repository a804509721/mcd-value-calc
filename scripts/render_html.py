# -*- coding: utf-8 -*-
"""
render_html.py — 把 result.json 渲染成静态单文件 HTML 分析报告。
零外部依赖（无 CDN / 无网络请求），可直接离线打开。
用法：python render_html.py [--result path/result.json] [--out path/report.html] [--check mcp_check.json]
"""
import argparse
import html
import json
import os
import string
import sys

sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = html.escape

METHOD_CN = {
    'exact': '同名直配',
    'expanded': '默认规格',
    'combo': '套餐拆解',
    'code_map': '编码映射',
}

RANK_DEFS = [
    ('rank_protein_per_yuan', 'protein_per_yuan', '榜单一 · 一块钱买到最多蛋白质',
     'g/元', '{:.2f}', 'p', ''),
    ('rank_kcal_per_yuan', 'kcal_per_yuan', '榜单二 · 一块钱买到最多热量',
     'kcal/元', '{:.1f}', 'e', '热量性价比只回答能量密度，不代表健康。'),
    ('rank_protein_ratio', 'protein_kcal_pct', '榜单三 · 蛋白质占总热量比例',
     '占比', '{:.1f}%', 'r', '黑咖啡类因热量极低会排很前（每份仅 1g 蛋白），请结合绝对蛋白量与价格判断。'),
]


def esc(v):
    return C(str(v))


def bar_row(i, name, price, protein, kcal, vtext, pct, has_note, kind):
    star = ' <span class="star">*</span>' if has_note else ''
    return ('<div class="brow"><div class="bidx">$i</div><div class="bmain">'
            '<div class="btop"><span class="bname">$name$star</span>'
            '<span class="bmeta">¥$price · $probg · $kcalkcal</span></div>'
            '<div class="btrack"><div class="bfill $kind" style="width:$pct%"></div></div>'
            '</div><div class="bval">$vtext</div></div>'
            ).replace('$i', str(i)).replace('$name', C(name)).replace('$star', star) \
             .replace('$price', '%.1f' % price).replace('$prob', '%.0f' % protein) \
             .replace('$kcal', '%.0f' % kcal).replace('$pct', '%.1f' % pct) \
             .replace('$vtext', vtext).replace('$kind', kind)


def rank_block(rows, field, title, unit, fmt, kind, note=''):
    top = [r for r in rows[:10] if r.get(field) is not None]
    vmax = max([r[field] for r in top] + [1e-9])
    body = []
    for i, r in enumerate(top, 1):
        body.append(bar_row(i, r['name'], r['price'], r['protein'], r['kcal'],
                            fmt.format(r[field]) + ' ' + unit,
                            100.0 * r[field] / vmax, r.get('price_note'), kind))
    more = '<p class="dim">仅展示前 10，完整榜单见 result.md / result.json</p>' if rows[10:] else ''
    note_html = '<p class="note">%s</p>' % C(note) if note else ''
    return ('<div class="card"><h3>$t</h3>$note$bars$more</div>'
            .replace('$t', C(title)).replace('$note', note_html)
            .replace('$bars', ''.join(body)).replace('$more', more))


def build(res, check=None):
    s, m = res['store'], res['match']
    ps = res.get('price_stat') or {}
    b = res.get('budget') or {}
    bt = m.get('by_type', {})

    top1 = res['rank_protein_per_yuan'][0] if res['rank_protein_per_yuan'] else None
    kpis = [
        ('营养匹配率', '%.1f%%' % m['rate'], '%d / %d 件商品' % (m['matched'], m['total_menu']), 'k-blue'),
        ('一块钱蛋白之王', '%.2f g/元' % top1['protein_per_yuan'],
         '%s ¥%.1f' % (top1['name'], top1['price']), 'k-blue'),
        ('30 元最优组合', '%.0f g 蛋白' % b.get('protein', 0),
         '¥%.1f / %.0f kcal' % (b.get('cost', 0), b.get('kcal', 0)), 'k-cyan'),
        ('价格已核验', '%d / %d' % (ps.get('from_api', 0), ps.get('total', 0)),
         '官方价格接口成交价', 'k-orange'),
    ]
    kpi_html = ''.join(
        '<div class="kpi $cls"><div class="lab">$lab</div><div class="val">$val</div>'
        '<div class="dim">$sub</div></div>'
        .replace('$cls', k[3]).replace('$lab', C(k[0])).replace('$val', C(k[1])).replace('$sub', C(k[2]))
        for k in kpis)

    combo_rows = ''.join(
        '<tr><td>%s</td><td>¥%.1f</td><td>×%d</td><td>¥%.1f</td><td>%.0fg</td><td>%.0fkcal</td></tr>'
        % (C(c['name']), c['price'], c['qty'], c['price'] * c['qty'], c['protein'], c['kcal'])
        for c in b.get('combo', []))

    total_m = max(m['total_menu'], 1)
    bt_items = sorted(bt.items(), key=lambda kv: -kv[1])
    seg = ''.join('<div class="seg s%d" style="width:%.2f%%" title="%s: %d"></div>'
                  % (i % 4, 100.0 * v / total_m, C(METHOD_CN.get(k, k)), v)
                  for i, (k, v) in enumerate(bt_items))
    legend = ''.join('<span class="lg"><i class="dot d%d"></i>%s %d件</span>'
                     % (i % 4, C(METHOD_CN.get(k, k)), v)
                     for i, (k, v) in enumerate(bt_items))

    un_rows = ''.join(
        '<tr><td class="un-name">%s</td><td>%s</td><td>%s</td><td class="dim">%s</td></tr>'
        % (C(r['name']), ('¥%.1f' % r['price']) if r.get('price') else '—',
           C(r.get('reason') or '—'), C('、'.join(r.get('candidates') or []) or '无'))
        for r in res['unmatched'])

    diff_rows = ''.join('<span class="chip">⚠ %s 菜单¥%.1f → 成交¥%.1f</span>'
                        % (C(d['name']), d['menu'], d['actual'])
                        for d in (ps.get('differs') or [])[:8]) or '<span class="dim">无</span>'

    cond_n = len(ps.get('conditional') or [])
    cond_txt = ('%d 件商品的菜单价为持卡/随单条件价' % cond_n) if cond_n else '无条件优惠价商品'
    cond_tail = ('，其中 %d 件为持卡/随单条件价' % cond_n) if cond_n else ''

    check_html = ''
    if check:
        rows = ''.join('<tr><td>%s</td><td class="%s">%s</td><td class="dim">%s</td></tr>'
                       % (C(i['item']), 'ok' if i['status'] == '通过' else 'warn',
                          C(i['status']), C(i['note'])) for i in check['items'])
        check_html = ('<div class="card"><h3>MCP 连接检查</h3><p class="note">%s</p>'
                      '<table class="tbl"><tr><th>检查项</th><th>结果</th><th>说明</th></tr>%s'
                      '</table></div>' % (C(check.get('summary', '')), rows))

    tpl = string.Template(TEMPLATE)
    return tpl.substitute(
        STORE=C(s.get('storeName', '')), CODE=esc(s.get('storeCode', '')),
        ADDR=C(s.get('address', '')), MODE='外送' if res['order_type'] == 2 else '到店自取',
        TIME=C(res['generated_at']), KPIS=kpi_html,
        COND=C(cond_txt), COND_TAIL=C(cond_tail), FROM_API=str(ps.get('from_api', 0)), TOTAL=str(ps.get('total', 0)),
        RATE='%.1f%%' % m['rate'], TMENU=str(m['total_menu']),
        MATCHED=str(m['matched']), UNMATCHED=str(m['unmatched']),
        SEG=seg, LEGEND=legend,
        RANKS=''.join(rank_block(res[k], f, t, u, fo, kd, n) for k, f, t, u, fo, kd, n in RANK_DEFS),
        BUDGET='%.0f' % b.get('budget', res.get('budget_setting', 30)),
        BPROT='%.0f' % b.get('protein', 0), BCOST='%.1f' % b.get('cost', 0),
        BLEFT='%.1f' % (b.get('budget', 30) - b.get('cost', 0)),
        BPPY='%.2f' % b.get('protein_per_yuan', 0), COMBO=combo_rows,
        UN_N=str(len(res['unmatched'])), UNROWS=un_rows,
        CHECK=check_html, DIFF=diff_rows, ELAPSED=esc(res.get('elapsed_s', '?')))


TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>麦当劳性价比分析报告</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#e2e8f0;font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;padding:28px 16px}
.wrap{max-width:1060px;margin:0 auto}
h1{font-size:26px;background:linear-gradient(90deg,#60a5fa,#22d3ee);-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:#94a3b8;margin:6px 0 4px}
.badges span{display:inline-block;background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.35);color:#93c5fd;border-radius:20px;padding:2px 12px;margin:4px 6px 0 0;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:18px 0}
.kpi{background:#121c30;border:1px solid rgba(59,130,246,.22);border-radius:12px;padding:14px 16px}
.kpi .lab{color:#94a3b8;font-size:12px}
.kpi .val{font-size:22px;font-weight:700;margin:4px 0;color:#f1f5f9}
.k-blue .val{color:#60a5fa}.k-cyan .val{color:#22d3ee}.k-orange .val{color:#fbbf24}
.card{background:#121c30;border:1px solid rgba(59,130,246,.22);border-radius:12px;padding:18px 20px;margin:14px 0}
.card h3{font-size:16px;color:#93c5fd;margin-bottom:10px;border-left:3px solid #3b82f6;padding-left:10px}
.note{color:#94a3b8;font-size:12.5px;margin-bottom:10px}
.dim{color:#64748b;font-size:12px}
.steps{display:flex;align-items:stretch;gap:8px;flex-wrap:wrap}
.step{flex:1;min-width:150px;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.25);border-radius:10px;padding:10px 12px}
.sname{font-weight:700;color:#60a5fa}.stool{font-family:Consolas,monospace;font-size:11px;color:#22d3ee;margin:2px 0}
.snote{font-size:12px;color:#94a3b8}
.sarrow{align-self:center;color:#3b82f6;font-size:18px}
.brow{display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px dashed rgba(148,163,184,.12)}
.bidx{width:22px;color:#64748b;font-size:12px;text-align:right}
.bmain{flex:1}.btop{display:flex;justify-content:space-between;gap:8px}
.bname{font-size:13.5px}.bmeta{color:#64748b;font-size:12px;white-space:nowrap}
.btrack{height:8px;background:rgba(148,163,184,.12);border-radius:4px;margin-top:4px;overflow:hidden}
.bfill{height:100%;border-radius:4px}
.p{background:linear-gradient(90deg,#3b82f6,#60a5fa)}
.e{background:linear-gradient(90deg,#f59e0b,#fbbf24)}
.r{background:linear-gradient(90deg,#06b6d4,#67e8f9)}
.bval{width:110px;text-align:right;font-family:Consolas,monospace;font-size:13px;color:#e2e8f0}
.star{color:#fbbf24;font-weight:700}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.tbl th{text-align:left;color:#93c5fd;border-bottom:1px solid rgba(59,130,246,.3);padding:6px 8px}
.tbl td{padding:6px 8px;border-bottom:1px dashed rgba(148,163,184,.12);vertical-align:top}
.un-name{color:#f1f5f9;white-space:nowrap}
.ok{color:#34d399;font-weight:700}.warn{color:#fbbf24;font-weight:700}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:rgba(251,191,36,.1);border:1px solid rgba(251,191,36,.35);color:#fcd34d;border-radius:6px;padding:2px 8px;font-size:11.5px}
.segwrap{height:14px;border-radius:7px;overflow:hidden;display:flex;background:rgba(148,163,184,.12)}
.seg{height:100%}
.s0{background:#3b82f6}.s1{background:#06b6d4}.s2{background:#8b5cf6}.s3{background:#f59e0b}
.lgs{margin-top:8px}.lg{margin-right:14px;font-size:12px;color:#94a3b8}
.dot{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px}
.d0{background:#3b82f6}.d1{background:#06b6d4}.d2{background:#8b5cf6}.d3{background:#f59e0b}
.combo{display:flex;gap:22px;align-items:center;flex-wrap:wrap}
.combo .big{font-size:30px;font-weight:800;color:#22d3ee}
.foot{color:#64748b;font-size:12px;margin-top:18px;line-height:1.8}
@media print{body{background:#fff;color:#111}.card,.kpi{border-color:#ccc}.kpi .val,.bval,.un-name{color:#111}}
</style></head><body><div class="wrap">
<h1>麦当劳性价比分析报告</h1>
<div class="sub">价格 × 官方营养，回答「同样的钱怎么吃最多蛋白」</div>
<div class="badges"><span>门店：$STORE</span><span>storeCode $CODE</span>
<span>$ADDR</span><span>$MODE</span><span>生成于 $TIME</span></div>

<div class="kpis">$KPIS</div>

<p class="dim" style="margin:4px 0 10px">价格口径：一律按门店官方成交价计算（$FROM_API/$TOTAL 件取到）$COND_TAIL，报告里标 <span class="star">*</span> 的商品是菜单标价与成交价不同。</p>

<div class="card"><h3>营养匹配率</h3>
<div class="val k-blue" style="font-size:28px;color:#60a5fa">$RATE</div>
<div class="dim">$TMENU 件在售商品中 $MATCHED 件拿到官方营养数据，$UNMATCHED 件未匹配（原因逐条列在底部，未做任何估算）</div>
<div class="segwrap" style="margin-top:10px">$SEG</div><div class="lgs">$LEGEND</div></div>

$RANKS

<div class="card"><h3>预算 ¥$BUDGET 蛋白最大化组合</h3>
<div class="combo"><div><div class="big">$BPROT g</div><div class="dim">总蛋白质</div></div>
<div><div class="big">¥$BCOST</div><div class="dim">总花费（预算内剩 ¥$BLEFT）</div></div>
<div><div class="big">$BPPY</div><div class="dim">g 蛋白 / 元</div></div></div>
<table class="tbl" style="margin-top:12px"><tr><th>商品</th><th>单价</th><th>数量</th><th>小计</th><th>蛋白</th><th>热量</th></tr>$COMBO</table>
<p class="note" style="margin-top:8px">整数背包精确求解（0.1 元离散化），仅在已匹配商品内选取；未匹配商品不参与。</p></div>

<div class="card"><h3>未匹配清单（$UN_N 件）</h3>
<table class="tbl"><tr><th>商品</th><th>价格</th><th>原因</th><th>营养表相近条目（仅提示，未采用）</th></tr>$UNROWS</table></div>

$CHECK

<div class="card"><h3>菜单价 ≠ 成交价的商品（已按成交价计算）</h3>
<div class="chips">$DIFF</div>
<p class="note" style="margin-top:8px">多为「早餐卡专享 / 随单购」条件优惠；持卡用户按菜单价买会更便宜，本报告按无卡可成交价保守计算。</p></div>

<div class="foot">数据来源：麦当劳门店菜单价格与官方营养数据。<br>
营养口径：蛋白质按 4 kcal/g 折算，蛋白占比 = 蛋白×4 ÷ 总热量；套餐营养为官方默认组成之和。<br>
本工具只读数据，未创建任何订单。生成耗时 $ELAPSED s。</div>
</div></body></html>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--result', default=os.path.join(SKILL_DIR, 'cache', 'result.json'))
    ap.add_argument('--out', default=os.path.join(SKILL_DIR, 'cache', 'report.html'))
    ap.add_argument('--check', default=None, help='MCP 检查结果 JSON（可选）')
    a = ap.parse_args()
    res = json.load(open(a.result, encoding='utf-8'))
    check = json.load(open(a.check, encoding='utf-8')) if a.check else None
    html_str = build(res, check)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, 'w', encoding='utf-8').write(html_str)
    print('HTML written: %s (%.1f KB)' % (a.out, len(html_str.encode('utf-8')) / 1024))


if __name__ == '__main__':
    main()
