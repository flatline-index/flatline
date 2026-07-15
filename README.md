# watchboard

**English TL;DR:** watchboard is an open-source "monitoring dashboard factory" for individual investors, packaged as a Claude Code / Cowork skill. You pre-register your own investment thesis as an observable, falsifiable condition (an invalidation line plus re-entry signals, evaluated on a closing-price basis only), and the AI assistant runs a set of free, zero-auth data pipelines on a schedule, checks your registered conditions against fresh data, and reports back as a five-layer scorecard: who pays (demand), is there enough cash (cash flow/profitability), where does the funding come from, who can cut it off (constraints/policy), and positioning/leverage. It ships eight ready-to-run data scripts (company-level CDS, Korean rates and margin financing, AI token usage share, GPU rental prices, ADR premium, keyword news monitoring), a free-data-source manual documenting every endpoint's quirks and failure modes, a discipline guide, a minimal backtesting method with a worked example, and a fabricated demo dashboard so you can see a filled-out board before building your own. This is a research tool, not investment advice: it does not recommend any security, any scraped data channel may break without notice, and a freshly built dashboard needs roughly 3-4 weeks of accumulated history before its historical percentiles mean anything. MIT licensed.

---

## 一句话定位

给个人投资者的开源"监测仪表盘工场"。把"该不该买"从一种感觉,变成一张每天自动刷新、
按五个问题分层亮灯的记分卡:你先把自己的投资论点翻译成可观测、可证伪的条件登记下来,
AI 每天负责跑数据、对照你登记的条件判定状态、按层结构讲给你听。

## 这是什么,不是什么

它不是一个"买卖信号推送"工具,不会告诉你买什么、卖什么。它做的是把你自己的投资纪律
外置成一套系统,逼着你在下判断之前,把"我感觉还能涨"这种模糊直觉,翻译成"外资连续
两天净买且收盘不破某价"这种可以被数据验证或证伪的具体条件,然后每天诚实地告诉你这些
条件现在是什么状态。

作者自己每天早上就用它看盘。仓库里的每条数据管道、每个信号阈值,都在真实行情里跑过。
它先是一个自用工具,然后才是一个开源项目。

## 核心框架:五层问法 + 跨资产组合

任何一只股票的"该不该买",都可以拆成同一组五个问题,换行业换指标、不换层:

1. **谁付钱(需求)**:终端需求在增长还是在放缓
2. **钱够不够(现金流/盈利)**:公司自己的现金流能不能撑住,还是要靠外部输血
3. **钱从哪来(资金/融资)**:公司融资成本和渠道是不是在恶化
4. **谁能掐断(约束/政策)**:有没有监管、认证、供应链这类外部约束能让论点瞬间作废
5. **筹码与杠杆**:谁在持有、杠杆几倍、强平线在哪

这五层背后是一张**跨资产组合**:信用利差(CDS)、流动性/利率、股票市场杠杆与资金面、
算力现货价格、需求侧份额数据,全部接进同一张个股决策盘里一起看,而不是散落在不同的
关注列表和不同的分析框架里各自为政。医疗股换成"诊疗量/处方渗透/医保准入"当需求指标,
半导体换成"合同价/资本开支"当现金流指标,层的问法不变,这是本仓库真正想输出的方法论,
不是某一个行业的专用工具。

## 八件套

1. **主技能**(`SKILL.md`):装进 Claude Code / Cowork 的技能文件夹后,敲一个斜杠命令,
   AI 自动跑完全部数据、对照你预先写下的信号逐条判定、按层播报给你、存档当日快照
2. **五层记分卡模板**(`templates/dashboard_template.md`,行业无关):谁付钱/钱够不够/
   钱从哪来/谁能掐断/筹码与杠杆,任何行业换指标不换层
3. **八个数据脚本**(`scripts/`,开箱即跑,标的可配置):公司级 5 年期 CDS(清算所官方
   结算价)、韩国央行利率与自建流动性利差、韩股融资余额与强制平仓、AI 模型 Token 用量
   与中美份额、GPU 租金、关键词新闻监听、ADR 溢价、韩股机构/外资资金面
4. **免费数据源手册**(`docs/data-sources.md`):每条通道的确切端点、参数、编码坑、
   口径坑、失效风险,你以为要付费数据终端才有的数据,免费拿法都写在这里
5. **纪律军规**(`docs/discipline.md`):预注册信号 + 收盘口径、历史分位不足禁止编数、
   口径三问、阈值必须回测、减速不等于转跌、现货-合同价差的供给侧监测法
6. **回测方法示范**(`docs/backtest-method.md`):"给阈值找依据"的最小流程,附一个用
   韩国公开数据做的完整工作示例
7. **演示盘**(`demo/dashboard_demo.md`):一张填好示意数据的完整快照,一眼看懂成品
   长什么样(数据全部虚构,不构成投资建议)
8. **致谢与免责**:见文末

## 五步用法

1. **装**:把整个 `watchboard/` 目录复制进你自己的 Claude 技能文件夹(或者直接
   `git clone` 到你习惯放技能的位置)
2. **配**:复制 `config.example.json` 为 `config.json`,把里面的示例标的换成你自己
   关心的,CDS 监测哪几家公司、盯哪只 ADR、新闻监听什么关键词、GPU 盯哪几代卡、哪几只
   股票的资金面。不关心的脚本直接在 `scripts` 段里设成 `enabled: false`
3. **建盘**:复制 `templates/dashboard_template.md`,和 AI 助手一起把你自己的投资论点
   翻译成"可观测条件",论点一句话、什么价格算证伪(收盘口径)、什么条件允许再进场。
   这一步是整套流程的灵魂:AI 会逼你把"我感觉还能涨"说成"外资连续两天净买且不破某价"
   这种可以被验证的具体条件
4. **日用**:每天敲一次斜杠命令,一分钟读完分层播报,哪层绿灯哪层黄灯、信号板几比几、
   下一个检查点该盯什么
5. **复盘**:每次判定自动记进快照的复盘记录表,定期回头对答案,你的判断胜率到底怎么样,
   自己心里有数

## 适合谁

- **适合**:用 Claude Code / Cowork 的个人投资者。不需要会写代码,但需要愿意把自己的
  投资逻辑说清楚、写下来
- **不适合**:想要"买卖信号推送"的人。它不荐股,它把你自己的纪律外置成系统,如果你
  自己都说不清楚论点和证伪条件,这套工具帮不了你

## 已知边界与免责

- **研究工具,不是投资建议**。本仓库不对任何证券的买卖给出建议,所有输出仅供你自己的
  研究流程参考
- **数据通道按现状提供**。所有抓取类数据源都可能因为对方网站改版、限流、下线而失效,
  失效时脚本会如实报错留空,不会编造数据;欢迎在你 fork 之后自己修,也欢迎回报 issue
- **新建的仪表盘需要时间攒历史**。刚建好的快照,大部分指标的"历史分位"会显示"历史
  不足 N 天,分位暂缺",这是设计如此,不是 bug;通常需要攒 3-4 周的日频数据,分位判断
  才会开始有意义
- **不荐股**。整套系统的输出是"你自己预注册的条件现在是什么状态",不是"这只股票现在
  该买该卖"

## 目录结构

```
watchboard/
├── README.md                    本文件
├── LICENSE                      MIT
├── SKILL.md                     主技能编排(六步流程)
├── config.example.json          配置示例(复制为 config.json 使用)
├── scripts/                     八个数据脚本
├── templates/
│   └── dashboard_template.md    五层记分卡模板
├── docs/
│   ├── data-sources.md          免费数据源手册
│   ├── discipline.md            纪律军规
│   └── backtest-method.md       回测方法示范
└── demo/
    └── dashboard_demo.md        演示盘(示意数据)
```

## 致谢

监测维度的划分参考了公开卖方研究的常见框架,数据管道、回测与信号工程为自研。
