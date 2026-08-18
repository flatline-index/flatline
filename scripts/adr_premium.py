# -*- coding: utf-8 -*-
"""ADR 相对本地上市股票的平价溢价监测 — flatline 数据脚本

监测"同一家公司在两个市场的价格是否对得上":一份 ADR(美股存托凭证)理论上应该等于
(本地股价 / 每份 ADR 对应股数 / 汇率)。两者之间的价差(溢价/折价)常被当作:
  - 跨市场情绪/资金面差异的温度计(溢价扩大 = 该市场资金更愿意为同一份资产多付钱)
  - "稀缺性"类交易的标尺:某些 ADR 因发行量有限、可转换渠道受限等原因常年溢价,
    溢价持续收敛往往意味着这层稀缺性正在消退

公式:
    溢价 = ADR价 ÷ (本地股价 ÷ 每ADR股数 ÷ 汇率) − 1

本脚本内置实现只覆盖"美股 ADR × 韩国本地股"这一路(本地股价源用 Naver),因为这是
作者实测验证过的通道。换成其他市场的 ADR/本地股配对,公式不变,但 get_krx_daily_closes()
要换成对应市场的数据源(参考 docs/data-sources.md,自己另找该市场的日线收盘源)。

配对口径:取"最近一个已收盘的美股交易日" D,配同日本地市场收盘(本地市场收盘早于美股,
若本地 D 日休市则取 ≤D 最近一个本地收盘)。汇率用取数时点即期,与 D 日收盘时点有小时级
错位,当温度计够用,精算须换成当日定盘价。

数据源:
  ADR 价格 : Naver 海外股 API https://api.stock.naver.com/stock/{TICKER}{后缀}/basic
             (后缀取决于交易所,纳斯达克通常 .O、纽交所通常 .K,自己在 Naver 海外股页面
             https://m.stock.naver.com/worldstock 确认对应代码)
  本地股价 : Naver finance.naver.com/item/frgn.naver(cp949 编码,逐日定版收盘)
  汇率     : fx_pair=USDKRW 时优先 Naver marketindex,其余/失败一律退 open.er-api.com
             (免费,无需注册,按 3 位货币代码查询任意汇率对)

口径坑:
  - Naver 页面用 cp949 编码,不是 utf-8,解码错了会拿到乱码
  - 本地股价头几小时是临时值,本地市场收盘后一段时间才定版,别把盘中价当收盘价用

输出:append/更新到 <dashboards_dir>/adr_premium.csv
  (date, pair, adr_ticker, adr_price, adr_source, local_ticker, local_price, local_date,
   fx, fx_pair, fx_source, premium_pct)
同一 (date, pair) 重跑覆盖旧行。任一腿拉不到 => 该 pair 报错跳过,不写假数,其余 pair 照跑。

用法:python adr_premium.py
建议每日目标市场美股收盘后跑一次。
历史分位:同一 pair 样本 <20 天报"分位暂缺"。

配置(config.json 的 adr_pairs,数组,可配多对):
  name / adr_ticker / adr_naver_suffix / local_market(目前只支持 "krx") /
  local_ticker / ratio(每 1 份 ADR 对应本地股数) / fx_pair(如 "USDKRW")
"""
import csv
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

MIN_HIST = 20
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

DEFAULT_CONFIG = {
    "dashboards_dir": "./flatline-data",
    "adr_pairs": [
        {"name": "SKHY_000660", "adr_ticker": "SKHY", "adr_naver_suffix": ".O",
         "local_market": "krx", "local_ticker": "000660", "ratio": 10, "fx_pair": "USDKRW"}
    ],
}

FIELDS = ['date', 'pair', 'adr_ticker', 'adr_price', 'adr_source', 'local_ticker',
          'local_price', 'local_date', 'fx', 'fx_pair', 'fx_source', 'premium_pct']


def load_config():
    """同目录 config.json 优先,其次环境变量 FLATLINE_CONFIG 指向的路径;都没有则用
    内置示例默认值(仅供跑通演示,正式使用请复制 config.example.json 改成 config.json
    并填自己的标的)。"""
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


def fetch(url, enc='utf-8'):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=20).read().decode(enc, errors='ignore')


def last_us_session_date():
    """最近一个已收盘的美股交易日(美东16:00后算当日;周末回退;联邦假日不识别,遇假日
    数据源自会对不上,人工核)"""
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)  # 夏令时 EDT;冬令时误差1小时不影响日界判定
    d = now_et.date()
    if now_et.hour < 16:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def get_adr_naver(adr_ticker, suffix):
    j = json.loads(fetch(f'https://api.stock.naver.com/stock/{adr_ticker}{suffix}/basic'))
    return float(str(j['closePrice']).replace(',', '')), 'naver_api'


def get_krx_daily_closes(code):
    """frgn 页逐日 (date, close),新到旧。当日收盘头几小时是临时值,定版以 Naver/KRX 为准。
    目前是本地市场唯一内置实现;换其他交易所需自己实现同签名的函数。"""
    html = fetch(f'https://finance.naver.com/item/frgn.naver?code={code}', enc='cp949')
    rows = []
    for tr in re.split(r'<tr\b', html):
        m = re.search(r'(\d{4}\.\d{2}\.\d{2})', tr)
        if not m:
            continue
        txt = re.sub(r'<[^>]+>', '|', tr)
        cells = [c.strip() for c in txt.split('|') if c.strip()]
        if len(cells) >= 10 and cells[1].count('.') == 2:
            d = datetime.strptime(cells[1], '%Y.%m.%d').date()
            rows.append((d, float(cells[2].replace(',', ''))))
    if not rows:
        raise RuntimeError('frgn 页无数据行')
    return rows


def get_fx(fx_pair):
    base, quote = fx_pair[:3].upper(), fx_pair[3:].upper()
    if fx_pair.upper() == 'USDKRW':
        try:
            html = fetch('https://finance.naver.com/marketindex/', enc='cp949')
            m = re.search(r'FX_USDKRW.*?<span class="value">([\d,.]+)</span>', html, re.S)
            if m:
                return float(m.group(1).replace(',', '')), 'naver'
        except Exception:
            pass
    j = json.loads(fetch(f'https://open.er-api.com/v6/latest/{base}'))
    return float(j['rates'][quote]), 'er-api'


def do_pair(pair_cfg):
    name = pair_cfg['name']
    adr_ticker = pair_cfg['adr_ticker']
    suffix = pair_cfg.get('adr_naver_suffix', '.O')
    local_market = pair_cfg.get('local_market', 'krx')
    local_ticker = pair_cfg['local_ticker']
    ratio = float(pair_cfg['ratio'])
    fx_pair = pair_cfg.get('fx_pair', 'USDKRW')

    if local_market != 'krx':
        print(f'[{name}] local_market={local_market} 暂无内置实现(目前只有 krx),跳过')
        return None

    us_date = last_us_session_date()

    adr_price, adr_src = get_adr_naver(adr_ticker, suffix)

    closes = get_krx_daily_closes(local_ticker)
    local_pair = next(((d, px) for d, px in closes if d <= us_date), None)
    if local_pair is None:
        print(f'[{name}] frgn 页找不到 <= {us_date} 的本地收盘,跳过')
        return None
    local_date, local_price = local_pair

    fx, fx_src = get_fx(fx_pair)
    parity = local_price / ratio / fx
    premium = (adr_price / parity - 1.0) * 100.0

    row = {'date': us_date.isoformat(), 'pair': name, 'adr_ticker': adr_ticker,
           'adr_price': f'{adr_price:.2f}', 'adr_source': adr_src,
           'local_ticker': local_ticker, 'local_price': f'{local_price:.0f}',
           'local_date': local_date.isoformat(), 'fx': f'{fx:.4f}', 'fx_pair': fx_pair,
           'fx_source': fx_src, 'premium_pct': f'{premium:.2f}'}

    print(f'[{name}] 配对美股日 {us_date} | {adr_ticker} {adr_price:.2f} ({adr_src}) | '
          f'{local_ticker} {local_price:.0f} ({local_date}) | {fx_pair} {fx:.4f} ({fx_src},取数时点即期)')
    print(f'[{name}] 平价 = {parity:.4f}, 溢价 = {premium:+.2f}%')
    if local_date != us_date:
        print(f'[{name}] 注意:本地市场 {us_date} 无收盘,配的是 {local_date}(休市错位)')
    return row


def main():
    cfg = load_config()
    dashboards_dir = resolve_dir(cfg.get('dashboards_dir', DEFAULT_CONFIG['dashboards_dir']))
    out_csv = os.path.join(dashboards_dir, 'adr_premium.csv')
    pairs = cfg.get('adr_pairs') or DEFAULT_CONFIG['adr_pairs']

    hist = []
    if os.path.exists(out_csv):
        with open(out_csv, newline='', encoding='utf-8') as f:
            hist = list(csv.DictReader(f))

    new_rows = []
    had_success = False
    for pair_cfg in pairs:
        try:
            row = do_pair(pair_cfg)
        except Exception as e:
            print(f"[{pair_cfg.get('name', '?')}] 出错,跳过: {type(e).__name__}: {e}")
            continue
        if row is None:
            continue
        had_success = True
        hist = [r for r in hist if not (r.get('date') == row['date'] and r.get('pair') == row['pair'])]
        hist.append(row)
        new_rows.append(row)

    if not had_success:
        raise RuntimeError('全部 pair 均未取到数据,不写盘')

    hist.sort(key=lambda r: (r['pair'], r['date']))
    os.makedirs(dashboards_dir, exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(hist)

    for row in new_rows:
        pair_hist = sorted((r for r in hist if r['pair'] == row['pair']), key=lambda r: r['date'])
        n = len(pair_hist)
        if n < MIN_HIST:
            print(f"[{row['pair']}] 历史不足{MIN_HIST}天(现{n}天),分位暂缺")
        else:
            vals = sorted(float(r['premium_pct']) for r in pair_hist)
            cur = float(row['premium_pct'])
            rank = sum(1 for v in vals if v <= cur) / n * 100
            print(f"[{row['pair']}] 溢价历史分位:{rank:.0f}%(样本{n}天)")
        if n >= 2:
            prev = float(pair_hist[-2]['premium_pct'])
            cur = float(row['premium_pct'])
            print(f"[{row['pair']}] 溢价日变动 {cur - prev:+.2f}pp")
    print(f'-> {out_csv} 共{len(hist)}行')


if __name__ == '__main__':
    sys.exit(main())
