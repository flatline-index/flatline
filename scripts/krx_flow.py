# -*- coding: utf-8 -*-
"""韩股投资者类型资金面(KRX官方口径,经Naver金融) — flatline 数据脚本

用途:看机构/外资是不是在买你监测的韩股标的——筹码与杠杆层的资金面腿。
输出:日期 | 收盘 | 涨跌% | 成交量 | 机构净买卖(股) | 外资净买卖(股) | 外资持股率
口径:股数(不是金额);盘后定版,当日白天看到的是前一日为止的数;金额自己按当日收盘折算。
页面 cp949 编码,不是 utf-8。

用法:
  python krx_flow.py 000660 [天数]     单只:代码从命令行参数读,天数默认 12
  python krx_flow.py                   不给代码时,批量跑 config.json 里 krx_flow_codes
                                        列出的每只(默认示例:000660, 005930)
"""
import json
import os
import re
import sys
import urllib.request

DEFAULT_CONFIG = {"krx_flow_codes": ["000660", "005930"]}


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


def naver_flow(code: str, days: int = 12):
    url = f'https://finance.naver.com/item/frgn.naver?code={code}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    html = urllib.request.urlopen(req, timeout=20).read().decode('cp949', errors='ignore')
    rows = []
    for tr in re.split(r'<tr\b', html):
        m = re.search(r'(\d{4}\.\d{2}\.\d{2})', tr)
        if not m:
            continue
        txt = re.sub(r'<[^>]+>', '|', tr)
        cells = [c.strip() for c in txt.split('|') if c.strip()]
        # 期望结构: [.., 日期, 收盘, 方向, 涨跌额, 涨跌%, 量, 机构净, 外资净, 外资持股数, 外资持股率]
        if len(cells) >= 10 and cells[1].count('.') == 2:
            rows.append(cells[1:11])
    return rows[:days]


def print_flow(code, days):
    print(f'--- {code} ---')
    print('日期 | 收盘 | 涨跌% | 量 | 机构净(股) | 外资净(股) | 外资持股率')
    try:
        for r in naver_flow(code, days):
            # r: 日期, 收盘, 方向, 涨跌额, 涨跌%, 量, 机构净, 外资净, 持股数, 持股率
            print(' | '.join([r[0], r[1], r[4], r[5], r[6], r[7], r[9]]))
    except Exception as e:
        print(f'  拉取失败: {type(e).__name__}: {e}')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        code = sys.argv[1]
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 12
        print_flow(code, days)
    else:
        cfg = load_config()
        codes = cfg.get('krx_flow_codes') or DEFAULT_CONFIG['krx_flow_codes']
        for code in codes:
            print_flow(code, 12)
