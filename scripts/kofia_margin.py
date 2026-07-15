# -*- coding: utf-8 -*-
"""韩股杠杆监测:信用融资余额 + 强制平仓(반대매매) — watchboard 数据脚本

数据源:KOFIA FreeSIS(韩国金融投资协会,免费公开,截至发布时实测无需注册/无需韩国 IP)
    POST https://freesis.kofia.or.kr/meta/getMetaDataList.do
    body {"dmSearch":{"tmpV40":"1000000","tmpV41":"1","tmpV1":"D","tmpV45":起,"tmpV46":止,"OBJ_NM":对象}}
两张表(列映射已用 /meta/getSrvData.do 元数据核过。截至发布时,公开可查范围内没有找到
这条 KOFIA 取数管道的开源实现先例):
  STATSCU0100000070BO 신용공여 잔고 추이(信用供与余额):
    TMPV1=日期 TMPV2=信用融资合计 TMPV3=主板(유가증권) TMPV4=创业板(코스닥)
    TMPV5-7=信用借券卖出(대주) TMPV8=打新贷款 TMPV9=存托证券担保融资
  STATSCU0100000060BO 증시자금추이(股市资金):
    TMPV1=日期 TMPV2=投资者预托金(客户保证金,除场内衍生品) TMPV3=场内衍生品预收金
    TMPV4=对客户RP卖出余额 TMPV5=委托未收金(미수금) TMPV6=对前日未收金的实际강제매매金额
    TMPV7=未收金对比强平比重(%)
口径注意:
  - 单位:百万韩元(tmpV40=1000000);比重列为 %
  - 반대매매是"对前一日未收金"的强平 => 暴跌日 D 的强平潮体现在 D+1/D+2 的数据里
  - 2023-10-25 起按"반대매매 체결금액"口径统计(官方脚注),新旧口径绝对水平不可比,
    做历史对比/回测务必分段处理(参考 docs/backtest-method.md 的处理方式)
  - 该强平口径只覆盖未收金(T+2透支)强平,不含券商信用融资追保强平(后者无集中日频公开源)

输出:合并写 <dashboards_dir>/kofia_margin.csv
  (date,margin_total_mkrw,margin_kospi_mkrw,margin_kosdaq_mkrw,deposit_mkrw,misu_mkrw,forced_sale_mkrw,forced_ratio_pct)
首跑自动回填 2024-01-01 起的历史(建分位底仓);之后每跑重拉最近40天窗口并合并(覆盖修订)。
用法:python kofia_margin.py [--from YYYYMMDD]   (纯标准库,任一 python 可跑;建议当地市场
     收盘后数小时、当日数据定版后再跑)
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

DEFAULT_CONFIG = {"dashboards_dir": "./watchboard-data"}

FIELDS = ['date', 'margin_total_mkrw', 'margin_kospi_mkrw', 'margin_kosdaq_mkrw',
          'deposit_mkrw', 'misu_mkrw', 'forced_sale_mkrw', 'forced_ratio_pct']
BACKFILL_FROM = '20240101'
REFRESH_DAYS = 40
MIN_HIST = 60
EP = 'https://freesis.kofia.or.kr/meta/getMetaDataList.do'
HDRS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://freesis.kofia.or.kr/', 'Origin': 'https://freesis.kofia.or.kr',
        'Content-Type': 'application/json'}
OBJ_CREDIT = 'STATSCU0100000070BO'
OBJ_FUNDS = 'STATSCU0100000060BO'


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


def kofia_pull(obj_nm, d_from, d_to):
    body = {'dmSearch': {'tmpV40': '1000000', 'tmpV41': '1', 'tmpV1': 'D',
                         'tmpV45': d_from, 'tmpV46': d_to, 'OBJ_NM': obj_nm}}
    req = urllib.request.Request(EP, data=json.dumps(body).encode(), headers=HDRS, method='POST')
    j = json.loads(urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore'))
    return j.get('ds1', [])


def pull_range(d_from, d_to):
    """分年段拉两表,返回 {date: rowdict}"""
    out = {}
    f = datetime.strptime(d_from, '%Y%m%d').date()
    end = datetime.strptime(d_to, '%Y%m%d').date()
    while f <= end:
        t = min(f + timedelta(days=364), end)
        seg = (f.strftime('%Y%m%d'), t.strftime('%Y%m%d'))
        for r in kofia_pull(OBJ_CREDIT, *seg):
            d = str(r['TMPV1'])
            out.setdefault(d, {}).update(
                margin_total_mkrw=r.get('TMPV2'), margin_kospi_mkrw=r.get('TMPV3'),
                margin_kosdaq_mkrw=r.get('TMPV4'))
        for r in kofia_pull(OBJ_FUNDS, *seg):
            d = str(r['TMPV1'])
            out.setdefault(d, {}).update(
                deposit_mkrw=r.get('TMPV2'), misu_mkrw=r.get('TMPV5'),
                forced_sale_mkrw=r.get('TMPV6'), forced_ratio_pct=r.get('TMPV7'))
        print(f'  段 {seg[0]}-{seg[1]} 累计 {len(out)} 天')
        f = t + timedelta(days=1)
        time.sleep(0.5)
    return out


def pctile(vals, x):
    vals = sorted(vals)
    return sum(1 for v in vals if v <= x) / len(vals) * 100


def main():
    cfg = load_config()
    dashboards_dir = resolve_dir(cfg.get('dashboards_dir', DEFAULT_CONFIG['dashboards_dir']))
    out_csv = os.path.join(dashboards_dir, 'kofia_margin.csv')

    d_to = date.today().strftime('%Y%m%d')
    d_from = None
    if '--from' in sys.argv:
        d_from = sys.argv[sys.argv.index('--from') + 1]

    old = {}
    if os.path.exists(out_csv):
        with open(out_csv, newline='', encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                old[r['date'].replace('-', '')] = r
    if d_from is None:
        d_from = BACKFILL_FROM if not old else (date.today() - timedelta(days=REFRESH_DAYS)).strftime('%Y%m%d')

    print(f'拉取 {d_from} -> {d_to} (已有 {len(old)} 天)')
    new = pull_range(d_from, d_to)
    if not new:
        raise RuntimeError('KOFIA 未返回任何数据,不写盘')

    for d, vals in new.items():
        row = old.get(d, {'date': f'{d[:4]}-{d[4:6]}-{d[6:]}'})
        row.update({k: ('' if v is None else v) for k, v in vals.items()})
        old[d] = row
    rows = [old[k] for k in sorted(old)]
    os.makedirs(dashboards_dir, exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in FIELDS})

    # 摘要与分位
    full = [r for r in rows if r.get('margin_total_mkrw') not in ('', None)]
    if not full:
        print(f'-> {out_csv} 共{len(rows)}行(暂无有效信用融资余额数据)')
        return
    last = full[-1]
    m_now = float(last['margin_total_mkrw'])
    print(f"\n最新 {last['date']}: 信用融资余额 {m_now/1e6:.2f}兆KRW", end='')
    if len(full) >= 2:
        m_prev = float(full[-2]['margin_total_mkrw'])
        dod = (m_now / m_prev - 1) * 100
        win = [float(r['margin_total_mkrw']) for r in full[-21:-1]] or [m_now]
        drop_from_peak = (m_now / max(win) - 1) * 100
        print(f' | 日变动 {dod:+.2f}% | 距20日峰值 {drop_from_peak:+.2f}%')
        dods = [(float(full[i]['margin_total_mkrw']) / float(full[i-1]['margin_total_mkrw']) - 1) * 100
                for i in range(1, len(full))]
        if len(dods) >= MIN_HIST:
            print(f'  余额日变动分位:{pctile(dods, dod):.0f}%(样本{len(dods)}天;越低=去杠杆越急)')
        else:
            print(f'  历史不足{MIN_HIST}天(现{len(dods)}天),分位暂缺')
    else:
        print()
    fr = [r for r in rows if r.get('forced_ratio_pct') not in ('', None)]
    if fr:
        f_last = fr[-1]
        f_now = float(f_last['forced_ratio_pct'])
        amt = float(f_last['forced_sale_mkrw'])
        print(f"最新 {f_last['date']}: 强平 {amt:,.0f}百万KRW,占未收金 {f_now:.1f}%", end='')
        ratios = [float(r['forced_ratio_pct']) for r in fr]
        if len(ratios) >= MIN_HIST:
            print(f' | 分位:{pctile(ratios, f_now):.0f}%(样本{len(ratios)}天;越高=强平潮越猛)')
        else:
            print(f' | 历史不足{MIN_HIST}天(现{len(ratios)}天),分位暂缺')
        print('  (提醒:강제매매对"前一日未收金"执行,暴跌日的强平潮看之后1-2个交易日的数;'
              '2023-10-25 前后口径不可直比)')
    print(f'-> {out_csv} 共{len(rows)}行')


if __name__ == '__main__':
    sys.exit(main())
