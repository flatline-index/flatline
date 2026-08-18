# -*- coding: utf-8 -*-
"""GPU 租赁价快照 — flatline 数据脚本

用途:算力需求侧的边缘温度计。公开租赁市场的 GPU 时租 = 算力稀缺性的市场实价。跟踪
数据中心卡型谱系(如 A100/H100/H200/B200):某代卡租金坚挺=推理需求外溢,租金崩=
算力过剩的先行信号。这类数据没有现成的历史序列可买,只能每天自己拉一次快照慢慢攒。

源(免鉴权):
  vast.ai  GET https://console.vast.ai/api/v0/bundles/?q=<urlencoded json>(verified ask 报价)
  RunPod   POST https://api.runpod.io/graphql  { gpuTypes { displayName securePrice communityPrice } }
输出:<dashboards_dir>/gpu_rent.csv
  (date,gpu,vast_min_dph,vast_med_dph,vast_n,runpod_secure_dph,runpod_community_dph)
同日期重跑覆盖。任一源挂:该源列留空并提示,不编数。
用法:python gpu_rent.py   (纯标准库)

配置(config.json 的 gpu_cards):{ 卡型分组名: { vast: [vast.ai 上的原始型号名...],
runpod: [RunPod 上的原始型号名...] } },分组名自己起,原始型号名要跟数据源返回的
displayName/gpu_name 精确匹配(大小写不敏感),可以从脚本输出里看到实际抓到的型号名
再回填。默认只跟踪数据中心卡型(消费卡如 3090/4090 挂牌噪声太大,不建议纳入历史序列)。
"""
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date

DEFAULT_CONFIG = {
    "dashboards_dir": "./flatline-data",
    "gpu_cards": {
        "A100": {"vast": ["A100 SXM4 80GB", "A100 PCIE", "A100 SXM4"], "runpod": ["A100 SXM", "A100 PCIe"]},
        "H100": {"vast": ["H100 SXM", "H100 NVL", "H100 PCIE"], "runpod": ["H100 SXM", "H100 NVL", "H100 PCIe"]},
        "H200": {"vast": ["H200", "H200 SXM", "H200 NVL"], "runpod": ["H200 SXM", "H200"]},
        "B200": {"vast": ["B200"], "runpod": ["B200"]},
    },
}

FIELDS = ['date', 'gpu', 'vast_min_dph', 'vast_med_dph', 'vast_n',
          'runpod_secure_dph', 'runpod_community_dph']
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Content-Type': 'application/json'}


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


def vast(groups, vast_all):
    q = {'verified': {'eq': True}, 'rentable': {'eq': True}, 'num_gpus': {'eq': 1},
         'gpu_name': {'in': vast_all}, 'type': 'ask', 'limit': 800}
    url = 'https://console.vast.ai/api/v0/bundles/?q=' + urllib.parse.quote(json.dumps(q))
    j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read())
    raw = {}
    for o in j.get('offers', []):
        g, p = str(o.get('gpu_name', '')), o.get('dph_total')
        if p:
            raw.setdefault(g.upper(), []).append(float(p))
    out = {}
    for grp, names in groups.items():
        ps = [p for n in names['vast'] for p in raw.get(n.upper(), [])]
        if ps:
            ps.sort()
            out[grp] = (ps[0], ps[len(ps) // 2], len(ps))
    return out


def runpod():
    gql = json.dumps({'query': '{ gpuTypes { displayName securePrice communityPrice } }'}).encode()
    req = urllib.request.Request('https://api.runpod.io/graphql', headers=UA, data=gql, method='POST')
    j = json.loads(urllib.request.urlopen(req, timeout=30).read())
    out = {}
    for t in j.get('data', {}).get('gpuTypes', []):
        out[t.get('displayName', '')] = (t.get('securePrice'), t.get('communityPrice'))
    return out


def main():
    cfg = load_config()
    dashboards_dir = resolve_dir(cfg.get('dashboards_dir', DEFAULT_CONFIG['dashboards_dir']))
    out_csv = os.path.join(dashboards_dir, 'gpu_rent.csv')
    groups = cfg.get('gpu_cards') or DEFAULT_CONFIG['gpu_cards']
    cards = list(groups)
    vast_all = sorted({n for g in groups.values() for n in g['vast']})

    today = date.today().isoformat()
    try:
        v = vast(groups, vast_all)
    except Exception as e:
        print(f'vast.ai 拉取失败({type(e).__name__}: {e}),该源本次留空')
        v = {}
    try:
        r = runpod()
    except Exception as e:
        print(f'RunPod 拉取失败({type(e).__name__}: {e}),该源本次留空')
        r = {}
    if not v and not r:
        raise RuntimeError('两个源都挂了,不写盘')

    old = {}
    if os.path.exists(out_csv):
        with open(out_csv, newline='', encoding='utf-8') as f:
            old = {(x['date'], x['gpu']): x for x in csv.DictReader(f)}
    for g in cards:
        vm = v.get(g)
        rp = (None, None)
        for cand in groups[g]['runpod']:
            if r.get(cand) and any(x not in (None, 0) for x in r[cand]):
                rp = r[cand]
                break
        row = {'date': today, 'gpu': g,
               'vast_min_dph': f'{vm[0]:.3f}' if vm else '',
               'vast_med_dph': f'{vm[1]:.3f}' if vm else '',
               'vast_n': vm[2] if vm else '',
               'runpod_secure_dph': rp[0] if rp[0] not in (None, 0) else '',
               'runpod_community_dph': rp[1] if rp[1] not in (None, 0) else ''}
        old[(today, g)] = row
        print(f"{g:16s} vast中位 ${row['vast_med_dph'] or '-'}/h (n={row['vast_n'] or '-'}) | "
              f"runpod 社区 ${row['runpod_community_dph'] or '-'}/h 安全 ${row['runpod_secure_dph'] or '-'}/h")
    rows = [old[k] for k in sorted(old)]
    os.makedirs(dashboards_dir, exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    days = len({x['date'] for x in rows})
    print(f'-> {out_csv} 共{len(rows)}行/{days}天' + ('' if days >= 20 else '(历史不足20天,分位暂缺)'))


if __name__ == '__main__':
    sys.exit(main())
