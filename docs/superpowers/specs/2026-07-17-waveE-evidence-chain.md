# 波E 规格：证据链智能（V20）

一句话：AI 的每个建议自带证据链——影响多大、历史上同类怎么处置的、AI 在这类事上可信到
什么程度、别的选项代价如何。全部现查现算，纯读聚合，零新写路。

## ① 证据包端点（apps/api 新建 evidence.py，router 工厂挂载）

GET /proposals/{task_id}/evidence（X-Role 脱敏同 objects；X-World 双世界）现算四块：
1. impact：受影响订单行数/金额合计/波及客户数（复用 cockpit 影响 SQL 口径）。
2. precedents：resolution_memory 同 rule_id（次选同 lane）检索——各决定的例数分布 +
   事后有效率（effective 字段），近例 3 条摘要（decision/quality/白话结局）。样本不足如实标。
3. trust：data/gating_report.json 该 rule 域的 tier/rate/CI/n（文件缺失→available:false 诚实空态；
   只读 JSON，不 import gating——V15/16 静态隔离红线同 governance.py）。
4. alternatives：本风险下 expedite vs accept_delay 的代价对比（延误天数、受影响货值、
   历史上两种选择的有效率对照）——能算则算，算不出的字段如实 null+reason。

## ② runtime 接证据 + 真模型开关（agent/runtime.py 最小改 + apps/api/runtime.py）

- 确定性 planner 的 propose 步 note 里引用证据摘要一句（"同类 N 例，X% 选 accept_delay 且
  事后有效"）——证据逻辑放共享模块（apps/api/evidence.py 的纯函数或 app/ 下共享），runtime 只调用。
- POST /runtime/runs 请求体可选 "llm": true → 该趟 think 走真模型（覆盖 env 默认）；响应
  llm_mode 已有。UI 发起处加复选框「用真模型（慢约 2-3 分钟，走订阅通道；默认快速脚本模式）」。

## ③ 前端（apps/cockpit）

- ImpactPanel 提案详情（有 decision 的分支）嵌「证据链」卡：影响三数字 / 先例分布横条
  （各决定占比+有效率）/ 信任档徽章（复用 U3 语言："影子档·一致率 59% [CI]·n=76——请谨慎复核"）/
  备选对比两行。全部 StateHint 诚实空态；术语配白话。
- AiRuns 发起块加真模型复选框；AI 运营账卡 detail 补「进行中 N 趟 / 等拍板 M 趟」。

## 红线与验收

不改动作函数本体/engine/真值/本体 JSON/gating 引擎；证据端点纯读；回归全套（pytest/security/
lint/digest×2/test_runtime/tsc+build）+对抗复核（重点：证据端点脱敏、先例统计不编数、
样本不足标注、真模型开关不静默烧钱）。
