# -*- coding: utf-8 -*-
"""OpenRouter 全球 Token 用量 + 中/美模型份额 — watchboard 数据脚本

用途:AI 算力需求的上游前瞻代理指标。OpenRouter 是第三方模型路由平台,口径是
"该平台上的用量",不是全球推理总量,也不含 ChatGPT/Claude 等厂商自家应用的直连流量——
看趋势与份额变化,别把绝对值当"全球 AI 用量"引用。

数据源(浏览器抓包定位):
    GET https://openrouter.ai/api/frontend/v1/rankings/models?view=day
    逐模型一行:date(最近完整UTC日), model_permaslug, total_prompt_tokens, total_completion_tokens
坑(实测,截至发布时仍然成立,平台随时可能改):view 语义 —— day/week/month 都是
  **拖尾累计**、每模型一行、日期挂"最后活跃日"——week/month 里日期早于主日期的零头行
  是死模型残行,不是逐日序列。所以日频序列只能用 view=day,每天跑一次得一天;漏跑即
  缺行,无法用 week/month 回补(那是累计和,拆不回单日)。
Token 口径 = prompt + completion(不含 reasoning 单列字段,该字段截至发布时普遍为0)。
厂商国别按 model_permaslug 前缀映射;未映射记 other 并在 stdout 提示(占比>1%时该
补充映射,自己在下面 CN/US/KNOWN_OTHER 三个集合里加)。
若 API 返回日期 = UTC 当日(不完整),本次不落行,只警告。

输出:<dashboards_dir>/openrouter_tokens.csv
  (date,total_tokens,cn_tokens,us_tokens,other_tokens,cn_share_pct,us_share_pct)
用法:python openrouter_tokens.py [--backfill]
  需要装了 scrapling 的解释器(config.json 里 python_cmd_scrapling 那个命令);
  scrapling 拉不到会自动退 urllib 裸抓再试一次,两个都失败才报错。
"""
import csv
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

DEFAULT_CONFIG = {"dashboards_dir": "./watchboard-data"}

FIELDS = ['date', 'total_tokens', 'cn_tokens', 'us_tokens', 'other_tokens',
          'cn_share_pct', 'us_share_pct']
API = 'https://openrouter.ai/api/frontend/v1/rankings/models?view=day'
MIN_HIST = 20

CN = {'deepseek', 'qwen', 'alibaba', 'moonshotai', 'z-ai', 'zhipuai', 'thudm', 'minimax',
      'bytedance', 'bytedance-research', 'baidu', 'tencent', 'hunyuan', 'stepfun', '01-ai',
      'internlm', 'kwaivgi', 'sensetime', 'iflytek', 'baichuan', 'openbmb', 'xiaomi',
      'inclusionai', 'rednote-hilab', 'shanghai-ai-lab'}
US = {'openai', 'anthropic', 'google', 'x-ai', 'meta-llama', 'meta', 'microsoft', 'amazon',
      'nvidia', 'perplexity', 'liquid', 'nousresearch', 'allenai', 'ai2', 'databricks',
      'snowflake', 'apple', 'ibm-granite', 'arcee-ai', 'inception', 'morph', 'deepcogito',
      'together', 'cerebras', 'groq', 'openevidence', 'venice', 'poolside'}
# 明确非中美(不进警告): mistralai(法) cohere(加) ai21(以) stability(英) tngtech(德) sao10k(新) eleutherai(社区)
KNOWN_OTHER = {'mistralai', 'cohere', 'ai21', 'stability', 'stabilityai', 'tngtech', 'sao10k',
               'eleutherai', 'cognitivecomputations', 'gryphe', 'undi95', 'openrouter',
               'neversleep', 'alpindale', 'anthracite-org', 'aion-labs'}


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


def fetch_json():
    txt = None
    try:
        from scrapling.fetchers import Fetcher
        r = Fetcher.get(API, impersonate='chrome', timeout=30)
        if r.status != 200:
            raise RuntimeError(f'HTTP {r.status}')
        txt = r.html_content
    except Exception as e:
        print(f'scrapling 失败({type(e).__name__}: {e}),退 urllib 裸抓')
        req = urllib.request.Request(API, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://openrouter.ai/rankings', 'Accept': 'application/json'})
        txt = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')
    txt = txt.strip()
    if not txt.startswith('{'):
        i, j = txt.find('{'), txt.rfind('}')
        if i < 0 or j < 0:
            raise RuntimeError('响应里找不到 JSON')
        txt = re.sub(r'</?[a-zA-Z][^>]*>', '', txt[i:j + 1])
    return json.loads(txt)


def classify(author):
    if author in CN:
        return 'cn'
    if author in US:
        return 'us'
    return 'other'


def backfill(out_csv):
    """--backfill:用 stats/model-activity(单模型真逐日,近31天)回补历史。
    口径:只能补"当前在榜模型"的最近31天;已下架模型历史缺失=>早期总量略低估,份额影响微小。
    仅新增 csv 里没有的日期,已有日期(view=day 全量口径)不覆盖。"""
    from scrapling.fetchers import Fetcher
    rows = fetch_json().get('data', [])
    pairs = sorted({(str(r.get('variant_permaslug', '')), str(r.get('variant', 'standard')))
                    for r in rows if r.get('variant_permaslug')})
    print(f'回补:{len(pairs)} 个(模型,变体)对,逐个拉 31 天窗口(约 {len(pairs)*0.4/60:.0f} 分钟)')
    today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    days, fails = {}, 0
    for i, (slug, variant) in enumerate(pairs, 1):
        url = ('https://openrouter.ai/api/frontend/v1/stats/model-activity?permaslug='
               + urllib.request.quote(slug) + '&variant=' + urllib.request.quote(variant))
        try:
            r = Fetcher.get(url, impersonate='chrome', timeout=25)
            txt = r.html_content.strip()
            if not txt.startswith('{'):
                txt = txt[txt.find('{'):txt.rfind('}') + 1]
            analytics = json.loads(txt).get('data', {}).get('analytics', [])
        except Exception:
            fails += 1
            analytics = []
        author = slug.split('/')[0].lower()
        bucket = classify(author)
        for a in analytics:
            d = str(a.get('date', ''))[:10]
            if not d or d >= today_utc:
                continue
            toks = int(a.get('total_prompt_tokens') or 0) + int(a.get('total_completion_tokens') or 0)
            if toks <= 0:
                continue
            b = days.setdefault(d, {'cn': 0, 'us': 0, 'other': 0})
            b[bucket] += toks
        if i % 50 == 0:
            print(f'  {i}/{len(pairs)} 完成,累计 {len(days)} 天,失败 {fails}')
        time.sleep(0.25)

    old = {}
    if os.path.exists(out_csv):
        with open(out_csv, newline='', encoding='utf-8') as fh:
            old = {r['date']: r for r in csv.DictReader(fh)}
    added = 0
    for d in sorted(days):
        if d in old:
            continue  # 已有日期是 view=day 全量口径,优先保留
        b = days[d]
        total = b['cn'] + b['us'] + b['other']
        old[d] = {'date': d, 'total_tokens': total, 'cn_tokens': b['cn'], 'us_tokens': b['us'],
                  'other_tokens': b['other'], 'cn_share_pct': f"{b['cn']/total*100:.2f}",
                  'us_share_pct': f"{b['us']/total*100:.2f}"}
        added += 1
    rows_out = [old[k] for k in sorted(old)]
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows_out)
    print(f'回补完成:新增 {added} 天(失败 {fails} 个模型),csv 现 {len(rows_out)} 天 '
          f'{rows_out[0]["date"]} -> {rows_out[-1]["date"]}')
    print('口径注:回补日期的总量=当前在榜模型口径,与 view=day 全量口径有细微差(已下架模型缺失)')


def main():
    cfg = load_config()
    dashboards_dir = resolve_dir(cfg.get('dashboards_dir', DEFAULT_CONFIG['dashboards_dir']))
    out_csv = os.path.join(dashboards_dir, 'openrouter_tokens.csv')
    os.makedirs(dashboards_dir, exist_ok=True)

    if '--backfill' in sys.argv:
        return backfill(out_csv)
    data = fetch_json().get('data', [])
    if not data:
        raise RuntimeError('API 返回空 data,不写盘')

    today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    days = {}   # date -> {'cn':x,'us':x,'other':x, 'unmapped':{author:tokens}}
    for row in data:
        d = str(row.get('date', ''))[:10]
        if not d or d >= today_utc:
            continue  # UTC 当日不完整,剔除
        toks = int(row.get('total_prompt_tokens') or 0) + int(row.get('total_completion_tokens') or 0)
        if toks <= 0:
            continue
        author = str(row.get('model_permaslug', '')).split('/')[0].lower()
        b = days.setdefault(d, {'cn': 0, 'us': 0, 'other': 0, 'unmapped': {}})
        if author in CN:
            b['cn'] += toks
        elif author in US:
            b['us'] += toks
        else:
            b['other'] += toks
            if author not in KNOWN_OTHER:
                b['unmapped'][author] = b['unmapped'].get(author, 0) + toks
    if not days:
        raise RuntimeError('API 只有 UTC 当日(不完整)数据,本次不落行(晚点再跑)')

    old = {}
    if os.path.exists(out_csv):
        with open(out_csv, newline='', encoding='utf-8') as fh:
            old = {r['date']: r for r in csv.DictReader(fh)}
    for d in sorted(days):
        b = days[d]
        total = b['cn'] + b['us'] + b['other']
        old[d] = {'date': d, 'total_tokens': total, 'cn_tokens': b['cn'], 'us_tokens': b['us'],
                  'other_tokens': b['other'], 'cn_share_pct': f"{b['cn']/total*100:.2f}",
                  'us_share_pct': f"{b['us']/total*100:.2f}"}
    rows = [old[k] for k in sorted(old)]
    with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    last_d = sorted(days)[-1]
    b = days[last_d]
    total = b['cn'] + b['us'] + b['other']
    print(f"最新完整日 {last_d}: 总量 {total/1e9:.1f}B tokens | 中 {b['cn']/total*100:.1f}% | "
          f"美 {b['us']/total*100:.1f}% | 其他 {b['other']/total*100:.1f}%")
    warn = {a: t for a, t in b['unmapped'].items() if t / total > 0.01}
    if warn:
        print('  未映射厂商占比>1%,须补进 CN/US/KNOWN_OTHER 映射:',
              ', '.join(f'{a}({t/total*100:.1f}%)' for a, t in sorted(warn.items(), key=lambda x: -x[1])))
    n = len(rows)
    if n >= 2:
        prev = int(rows[-2]['total_tokens'])
        print(f"  总量环比上一记录日({rows[-2]['date']}) {(total/prev-1)*100:+.1f}%")
    if n < MIN_HIST:
        print(f'历史不足{MIN_HIST}天(现{n}天),分位暂缺')
    else:
        vals = sorted(int(r['total_tokens']) for r in rows)
        rank = sum(1 for v in vals if v <= total) / n * 100
        print(f'总量历史分位:{rank:.0f}%(样本{n}天)')
    print(f'-> {out_csv} 共{n}行')


if __name__ == '__main__':
    sys.exit(main())
