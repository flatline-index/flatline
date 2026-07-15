# -*- coding: utf-8 -*-
"""关键词新闻监听(约束/事件层的事件锚) — watchboard 数据脚本

监控什么:你在 config.json 里配的任意关键词组合(默认示例监控 HBM 相关的英伟达认证/
份额/供应新闻,换成你自己关心的题材——监管动向、认证进度、供应商份额变动都行)。
信源:Google News RSS,按语言/地区分别查询(免费,无需 API 钥匙),各地主流媒体和
     科技媒体会自然进入对应语言/地区的 RSS 结果。
去重:已见条目记 <dashboards_dir>/news_watch_seen.csv,只报新增。
分级纪律(报出后由你自己或 AI 助手判):一级=公司财报会与公告原话;二级=行业研究机构
     报告;三级=媒体报道,一律先标"传闻级,未经公司或研究确认"。脚本只做传输,不做判断。
用法:python news_watch.py   (纯标准库;首跑只收录基线不刷屏,之后每跑报新增)

配置(config.json 的 news_watch,数组,每项一条查询):
  tag(标签,如语言代码) / query(Google News 搜索语法,支持 OR/引号短语/括号分组) /
  hl(界面语言) / gl(地区) / ceid(地区:语言,格式 "国家代码:语言代码") /
  must_contain(可选,标题必须包含其一才收录的关键词列表,不区分大小写;RSS 搜索结果
  经常混入不相关条目,这道过滤能挡掉大部分噪声;留空/不填则不过滤)
"""
import csv
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

DEFAULT_CONFIG = {
    "dashboards_dir": "./watchboard-data",
    "news_watch": [
        {"tag": "ko", "query": "HBM (엔비디아 OR NVIDIA) (인증 OR 퀄 OR 공급 OR 점유율 OR 납품)",
         "hl": "ko", "gl": "KR", "ceid": "KR:ko", "must_contain": ["hbm"]},
        {"tag": "en", "query": 'HBM NVIDIA (qualification OR certification OR "market share" OR supply)',
         "hl": "en-US", "gl": "US", "ceid": "US:en", "must_contain": ["hbm"]},
    ],
}

WINDOW_DAYS = 21
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


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


def fetch_feed(q, hl, gl, ceid):
    url = ('https://news.google.com/rss/search?q=' + urllib.parse.quote(q)
           + f'&hl={hl}&gl={gl}&ceid={ceid}')
    xml_txt = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read()
    root = ET.fromstring(xml_txt)
    out = []
    for item in root.iter('item'):
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        pub = item.findtext('pubDate') or ''
        src = item.findtext('source') or ''
        try:
            dt = parsedate_to_datetime(pub)
        except Exception:
            continue
        out.append((dt, title, src, link))
    return out


def main():
    cfg = load_config()
    dashboards_dir = resolve_dir(cfg.get('dashboards_dir', DEFAULT_CONFIG['dashboards_dir']))
    seen_csv = os.path.join(dashboards_dir, 'news_watch_seen.csv')
    queries = cfg.get('news_watch') or DEFAULT_CONFIG['news_watch']

    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    items = []
    for q_cfg in queries:
        tag = q_cfg.get('tag', '?')
        must_contain = [w.lower() for w in q_cfg.get('must_contain', [])]
        try:
            for dt, title, src, link in fetch_feed(q_cfg['query'], q_cfg.get('hl', 'en-US'),
                                                    q_cfg.get('gl', 'US'), q_cfg.get('ceid', 'US:en')):
                if dt < cutoff:
                    continue
                if must_contain and not any(w in title.lower() for w in must_contain):
                    continue
                items.append((dt, tag, title, src, link))
        except Exception as e:
            print(f'[{tag}] 源拉取失败: {type(e).__name__}: {e}')

    if not items:
        print('所有查询在窗口内都无条目(或均失败)——如均失败请检查网络,别当"无新闻"读')
        return 1

    seen = {}
    if os.path.exists(seen_csv):
        with open(seen_csv, newline='', encoding='utf-8') as f:
            seen = {r['id']: r for r in csv.DictReader(f)}
    first_run = not seen

    new = []
    for dt, tag, title, src, link in sorted(items, reverse=True):
        iid = hashlib.md5(title.encode('utf-8')).hexdigest()[:16]
        if iid in seen:
            continue
        seen[iid] = {'id': iid, 'date': dt.strftime('%Y-%m-%d'), 'lang': tag,
                     'source': src, 'title': title, 'link': link}
        new.append(seen[iid])

    os.makedirs(dashboards_dir, exist_ok=True)
    with open(seen_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['id', 'date', 'lang', 'source', 'title', 'link'])
        w.writeheader()
        w.writerows(seen.values())

    if first_run:
        print(f'首跑:收录基线 {len(new)} 条(近{WINDOW_DAYS}天),列出供人工过目;之后每跑只报新增')
    if not new:
        print(f'无新条目(库存 {len(seen)} 条)')
        return 0
    print(f'{"基线" if first_run else "新增"} {len(new)} 条(全部默认传闻级,待信源分级):')
    for r in new:
        print(f"  [{r['date']}][{r['lang']}] {r['title']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
