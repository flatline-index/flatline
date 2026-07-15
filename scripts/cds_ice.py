# -*- coding: utf-8 -*-
"""公司级 5 年期 CDS(ICE 清算所官方结算价) — watchboard 数据脚本

用途:监测个股/组合背后的信用违约互换(CDS)利差走势,作为"钱从哪来(融资成本/
信用状况)"这一层的资金面代理指标——利差走阔通常领先于债券利率上行或评级下调。

源:ICE Clear Credit 每日免费公开全部已清算单名 CDS 结算价
    GET https://www.ice.com/api/cds-settlement-prices/icc-single-names
    返回 [{clearingDate, name, instrumentName, eodPrice}, ...]
    instrumentName 格式: TICKER.TIER.CCY.DOC.票息bp.到期日 (如 ALPHINC.SNRFOR.USD.XR14.100.2031-06-20)

口径(重要):
  - eodPrice 是价格(% of par),不是利差。近似换算:利差bp ≈ 票息bp − (价格−100)×100/久期,
    取 5 年期风险久期常数 4.55——**近似口径**,与专业终端的精确利差有小偏差,看方向和
    分位没问题,不要拿去跟精确报价做逐点核对
  - 价格跌 = 利差走阔 = 信用恶化
  - 只取 SNRFOR(高级无担保)+USD,票息优先 100bp 档,到期取全场众数(=当前 5 年
    on-the-run 桶)
输出:<dashboards_dir>/cds_ice.csv
  (date,company,ticker,coupon_bp,maturity,eod_price,approx_spread_bp)
用法:python cds_ice.py
  需要装了 scrapling 的解释器(config.json 里 python_cmd_scrapling 那个命令);ICE 的
  接口对裸 urllib 请求可能挑剔,建议直接用 scrapling。

配置(config.json 的 cds_companies):{ 显示名: 名称匹配正则(对 ICE 返回的 name 字段,
不区分大小写) },按需增删公司,正则支持管道 | 做多别名匹配(如 "meta platforms|facebook")。
"""
import csv
import json
import os
import re
import sys
from collections import Counter

DEFAULT_CONFIG = {
    "dashboards_dir": "./watchboard-data",
    "cds_companies": {
        "Microsoft": "microsoft",
        "Alphabet": "alphabet",
        "Amazon": "amazon",
        "Meta": "meta platforms|facebook",
        "Oracle": "oracle",
    },
}

FIELDS = ['date', 'company', 'ticker', 'coupon_bp', 'maturity', 'eod_price', 'approx_spread_bp']
API = 'https://www.ice.com/api/cds-settlement-prices/icc-single-names'
DURATION = 4.55
MIN_HIST = 20


def load_config():
    candidates = []
    env_path = os.environ.get('WATCHBOARD_CONFIG')
    if env_path:
        candidates.append(env_path)
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json'))
    for path in candidates:
        if path and os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                cfg = json.load(f)
            print(f'[config] 已加载 {path}')
            return cfg
    print('[config] 未找到 config.json(同目录或 WATCHBOARD_CONFIG 环境变量均未命中),'
          '使用内置示例默认值')
    return DEFAULT_CONFIG


def resolve_dir(path):
    return os.path.abspath(os.path.expanduser(path))


def fetch():
    from scrapling.fetchers import Fetcher
    r = Fetcher.get(API, impersonate='chrome', timeout=40)
    txt = r.html_content.strip()
    if not txt.startswith('['):
        i, j = txt.find('['), txt.rfind(']')
        if i < 0:
            raise RuntimeError('响应不是 JSON 数组')
        txt = txt[i:j + 1]
    return json.loads(txt)


def main():
    cfg = load_config()
    dashboards_dir = resolve_dir(cfg.get('dashboards_dir', DEFAULT_CONFIG['dashboards_dir']))
    out_csv = os.path.join(dashboards_dir, 'cds_ice.csv')
    targets = cfg.get('cds_companies') or DEFAULT_CONFIG['cds_companies']

    rows = fetch()
    if not rows:
        raise RuntimeError('ICE 返回空,不写盘')
    # 解析 instrumentName
    parsed = []
    for r in rows:
        m = re.match(r'([A-Za-z0-9\-\.]+)\.(SNRFOR|SNRLAC|SUBLT2)\.([A-Z]{3})\.(\w+)\.(\d+)\.(\d{4}-\d{2}-\d{2})$',
                     str(r.get('instrumentName', '')))
        if not m:
            continue
        parsed.append({'name': r.get('name', ''), 'date': r.get('clearingDate', ''),
                       'ticker': m.group(1), 'tier': m.group(2), 'ccy': m.group(3),
                       'coupon': int(m.group(5)), 'maturity': m.group(6),
                       'price': float(r.get('eodPrice', 0))})
    modal_mat = Counter(p['maturity'] for p in parsed).most_common(1)[0][0]
    print(f'全场 {len(parsed)} 条,当前 5 年桶到期日(众数):{modal_mat}')

    old = {}
    if os.path.exists(out_csv):
        with open(out_csv, newline='', encoding='utf-8') as f:
            old = {(x['date'], x['company']): x for x in csv.DictReader(f)}

    found = []
    for comp, pat in targets.items():
        cands = [p for p in parsed if re.search(pat, p['name'], re.I)
                 and p['tier'] == 'SNRFOR' and p['ccy'] == 'USD' and p['maturity'] == modal_mat]
        if not cands:
            print(f'  {comp}: 清算名单里未找到(名称正则 {pat}),如实标缺')
            continue
        cands.sort(key=lambda p: (p['coupon'] != 100, p['coupon']))
        p = cands[0]
        spread = p['coupon'] - (p['price'] - 100) * 100 / DURATION
        row = {'date': p['date'], 'company': comp, 'ticker': p['ticker'],
               'coupon_bp': p['coupon'], 'maturity': p['maturity'],
               'eod_price': f"{p['price']:.4f}", 'approx_spread_bp': f'{spread:.0f}'}
        old[(row['date'], comp)] = row
        found.append((comp, spread, p))
        print(f"  {comp:10s} {p['ticker']:10s} 票息{p['coupon']} 价格{p['price']:.3f} -> 近似利差 {spread:.0f}bp ({p['date']})")

    if not found:
        raise RuntimeError('配置的公司一个都没匹配到,不写盘(检查 cds_companies 里的名称正则)')
    out = [old[k] for k in sorted(old)]
    os.makedirs(dashboards_dir, exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    days = len({x['date'] for x in out})
    print(f'-> {out_csv} 共{len(out)}行/{days}天' + ('' if days >= MIN_HIST else f'(历史不足{MIN_HIST}天,分位暂缺)'))


if __name__ == '__main__':
    sys.exit(main())
