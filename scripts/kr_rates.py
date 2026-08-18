# -*- coding: utf-8 -*-
"""韩国流动性利率日更(筹码与杠杆层的流动性块) — flatline 数据脚本

背景:监测韩国市场流动性松紧,常规做法是看 KOFR-OIS/KORIBOR-OIS/CP-OIS 这类利差,但
OIS 那条腿通常要付费数据终端才能拿到。这里改用自建代理利差(口径已注册,不能跟付费
终端的利差数字直接比水平,只看方向与自身历史分位):
    KOFR − 基准利率(回购资金面) / KORIBOR3M − 基准利率(银行间,含加息预期成分)
    CP91 − CD91(非银 vs 银行信用分层,两腿同期限,预期中性,最贴近"信用利差"本意)
另单列 국고채(国债)10년 收益率。

源(零注册):
  主源 ECOS(韩国央行统计API,demo key 'sample',单次≤10行,分批拉):
    GET https://ecos.bok.or.kr/api/StatisticSearch/sample/json/kr/1/10/817Y002/D/{起}/{止}/{科目}
    科目:010901000 KOFR / 010150000 KORIBOR3M / 010502000 CD91 / 010503000 CP91 / 010210000 국고채10y
    基准利率:722Y001/D/.../0101000
  备源(全历史一炮拉,回填用,本脚本未接入,自己按需扩展):
    KOFR: POST https://www.kofr.kr/websquare/engine/proworks/callServletService.jsp (XML body)
    KORIBOR: GET https://portal.kfb.or.kr/fingoods/koribor.php?SEARCH_START_DT=..&SEARCH_END_DT=..(cp949)
输出:<dashboards_dir>/kr_rates.csv
  (date,kofr,base_rate,koribor_3m,cd_91,cp_91,ktb_10y,sp_kofr_base_bp,sp_koribor_base_bp,sp_cp_cd_bp)
用法:python kr_rates.py [--backfill]   纯标准库。日常增量拉最近40天;--backfill 从2024-01起
     (ECOS demo key 配额未知,回填遇错优雅停,重跑自动续)。
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

DEFAULT_CONFIG = {"dashboards_dir": "./flatline-data"}

FIELDS = ['date', 'kofr', 'base_rate', 'koribor_3m', 'cd_91', 'cp_91', 'ktb_10y',
          'sp_kofr_base_bp', 'sp_koribor_base_bp', 'sp_cp_cd_bp']
ITEMS = [('kofr', '817Y002', '010901000'), ('koribor_3m', '817Y002', '010150000'),
         ('cd_91', '817Y002', '010502000'), ('cp_91', '817Y002', '010503000'),
         ('ktb_10y', '817Y002', '010210000'), ('base_rate', '722Y001', '0101000')]
MIN_HIST = 60
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


def load_config():
    candidates = []
    env_path = os.environ.get('FLATLINE_CONFIG')
    if env_path:
        candidates.append(env_path)
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json'))
    for path in candidates:
        if path and os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                cfg = json.load(f)
            print(f'[config] 已加载 {path}')
            return cfg
    print('[config] 未找到 config.json(同目录或 FLATLINE_CONFIG 环境变量均未命中),'
          '使用内置示例默认值')
    return DEFAULT_CONFIG


def resolve_dir(path):
    return os.path.abspath(os.path.expanduser(path))


def get(url, data=None, headers=None, enc='utf-8'):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    return urllib.request.urlopen(req, timeout=30).read().decode(enc, errors='ignore')


def ecos_series(stat, item, d_from, d_to):
    """分批拉一个科目,10行/次向前推进;配额报错时优雅返回已得部分"""
    out, cursor = {}, d_from
    while cursor <= d_to:
        url = (f'https://ecos.bok.or.kr/api/StatisticSearch/sample/json/kr/1/10/'
               f'{stat}/D/{cursor}/{d_to}/{item}')
        try:
            j = json.loads(get(url))
        except Exception as e:
            print(f'  ECOS 请求失败({item}@{cursor}): {e},保留已得 {len(out)} 天')
            break
        block = j.get('StatisticSearch')
        if not block:
            code = (j.get('RESULT') or {}).get('CODE', '?')
            if code != 'INFO-200':  # INFO-200=无数据(正常终点)
                print(f'  ECOS 返回 {code}({item}@{cursor}),保留已得 {len(out)} 天')
            break
        rows = block.get('row', [])
        if not rows:
            break
        for r in rows:
            out[r['TIME']] = float(r['DATA_VALUE'])
        last = max(r['TIME'] for r in rows)
        nxt = (datetime.strptime(last, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.3)
    return out


def main():
    cfg = load_config()
    dashboards_dir = resolve_dir(cfg.get('dashboards_dir', DEFAULT_CONFIG['dashboards_dir']))
    out_csv = os.path.join(dashboards_dir, 'kr_rates.csv')

    backfill = '--backfill' in sys.argv
    d_to = date.today().strftime('%Y%m%d')
    d_from = '20240101' if backfill else (date.today() - timedelta(days=40)).strftime('%Y%m%d')

    old = {}
    if os.path.exists(out_csv):
        with open(out_csv, newline='', encoding='utf-8') as f:
            old = {r['date']: r for r in csv.DictReader(f)}
    if backfill and old:
        print(f'已有 {len(old)} 天,回填从 20240101 重刷补洞')

    print(f'ECOS 拉取 {d_from} -> {d_to} (6 科目分批)')
    series = {}
    for name, stat, item in ITEMS:
        s = ecos_series(stat, item, d_from, d_to)
        series[name] = s
        print(f'  {name}: {len(s)} 天' + (f',最新 {max(s)}={s[max(s)]}' if s else ''))
    if not any(series.values()):
        raise RuntimeError('ECOS 全部落空,不写盘')

    all_dates = sorted(set().union(*[set(s) for s in series.values() if s]))
    base_carry = None
    for d8 in all_dates:
        iso = f'{d8[:4]}-{d8[4:6]}-{d8[6:]}'
        row = old.get(iso, {'date': iso})
        for name, _, _ in ITEMS:
            v = series[name].get(d8)
            if v is not None:
                row[name] = v
        # 基准利率是台阶函数,ECOS 只在有值日给,向前结转
        if row.get('base_rate') not in (None, ''):
            base_carry = float(row['base_rate'])
        elif base_carry is not None:
            row['base_rate'] = base_carry
        try:
            if row.get('kofr') not in (None, '') and row.get('base_rate') not in (None, ''):
                row['sp_kofr_base_bp'] = round((float(row['kofr']) - float(row['base_rate'])) * 100, 1)
            if row.get('koribor_3m') not in (None, '') and row.get('base_rate') not in (None, ''):
                row['sp_koribor_base_bp'] = round((float(row['koribor_3m']) - float(row['base_rate'])) * 100, 1)
            if row.get('cp_91') not in (None, '') and row.get('cd_91') not in (None, ''):
                row['sp_cp_cd_bp'] = round((float(row['cp_91']) - float(row['cd_91'])) * 100, 1)
        except ValueError:
            pass
        old[iso] = row

    rows = [old[k] for k in sorted(old)]
    os.makedirs(dashboards_dir, exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in FIELDS})

    full = [r for r in rows if r.get('sp_cp_cd_bp') not in ('', None)]
    if full:
        last = full[-1]
        print(f"\n最新 {last['date']}: KOFR {last.get('kofr','-')}% | 基准 {last.get('base_rate','-')}% | "
              f"KORIBOR3M {last.get('koribor_3m','-')}% | CD91 {last.get('cd_91','-')}% | "
              f"CP91 {last.get('cp_91','-')}% | 국채10y {last.get('ktb_10y','-')}%")
        print(f"代理利差: KOFR-基准 {last.get('sp_kofr_base_bp','-')}bp | KORIBOR3M-基准 "
              f"{last.get('sp_koribor_base_bp','-')}bp(含加息预期) | CP-CD {last.get('sp_cp_cd_bp','-')}bp")
        for key, label in (('sp_kofr_base_bp', 'KOFR-基准'), ('sp_koribor_base_bp', 'KORIBOR-基准'),
                           ('sp_cp_cd_bp', 'CP-CD')):
            vals = [float(r[key]) for r in rows if r.get(key) not in ('', None)]
            if len(vals) >= MIN_HIST:
                cur = float(last.get(key)) if last.get(key) not in ('', None) else None
                if cur is not None:
                    pct = sum(1 for v in sorted(vals) if v <= cur) / len(vals) * 100
                    print(f'  {label} 分位:{pct:.0f}%(样本{len(vals)}天;越高=越紧)')
            else:
                print(f'  {label}: 历史不足{MIN_HIST}天(现{len(vals)}天),分位暂缺')
    print(f'-> {out_csv} 共{len(rows)}行')


if __name__ == '__main__':
    sys.exit(main())
