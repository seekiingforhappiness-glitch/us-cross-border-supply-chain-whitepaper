# 波1 规格：AI 可信度可测、可放权（display-only）

日期：2026-07-16 ｜ 依据：V15 路线丙波1 + V14 路 C ｜ 状态：Daniel 已裁路线（"C"），本 spec
细化执行边界；其中放权阈值数字受 **V15 保护条款**约束（草案默认值，候正式裁决，引擎只展示不改权）。

**一句话**：把"AI 哪个域可信到什么程度"从感觉变成一手实测数字，并让系统自己算出各域当前
该处于放权阶梯的哪一档——先只把结论亮在治理控制室，不接权限。

## 背景事实（2026-07-16 实测）

- 影子长跑首轮：金标 scripted 29/29=100%（确定性基准）；真 AI 过题 9/25=36%（上下文/审计类
  100%，简报/越权类 0%——键词判分对自由文本偏严，数字如实记录、解释候人）；档1 一致率
  **未测**（25 题后 CLI 通道连续失败，测量台诚实中止）；档1 可比底数已清点：180 例
  （R1=76/R16=32/R2=19/R4=18/R5=6/R6=29；critical=33/high=147；13 条航线）。
- 已勘误缺陷：--llm 档 MCP 子进程未透传临时副本路径（连真库；业务写=0 有 trace_id 铁证，
  但 llm_calls 审计落真库，"业务库全程只读"措辞不成立）。

## 交付物三件

### A. G-Shadow 修复包（agent/shadow_bench.py + agent/llm_agent.py）

1. **temp 路径透传**：MCP 多轮通道启动 claude CLI 时以 `ONTOLOGY_DB=<临时副本>` 注入子进程
   环境（mcp_server 已支持该环境变量=双世界机制）。修复后"业务库全程只读"才名副其实。
2. **真调用遥测不丢**：临时副本里的 llm_calls 增量在 run 结束时拷入 shadow.sqlite 的
   `shadow_llm_calls` 旁路表（治理留痕在旁路账本，业务库零写入）。
3. **--tier {bench1,bench2,both}** 选择器 + **案例级断点续跑**（同 run_id 已测 case_ref 跳过，
   通道死后 resume 不重烧已测题）。
4. **退避重试**：单案失败指数退避重试 2 次再计失败；连续 N（默认 5）案全失败才中止该档。
5. **escalation recall 切片**：档1 汇总增加"人当时选择升级/拒绝的案例中，AI 也建议升级/更保守
   的比例"（该转人工的识别率，Monday 吸收项 C）。
6. **样本量+置信区间**：所有一致率/过题率输出 Wilson 95% CI 与 n（吸收项 D）；n<30 的切片
   明标"样本不足，仅供参考"。

### B. prompt 版本机（agent/llm_agent.py + agent/egress_gate.py）

SYSTEM_PROMPT 裸常量 → 版本化注册（`PROMPT_VERSION = "p1"` 与文本同处声明，改文本必须换号）；
`llm_calls` 增 `prompt_version` 列（建表处 DDL + 旧库 ALTER 兜底），每次调用落版本号。
深研 2-1 落地；2-6（指纹漂移即拒）留波2（属审批绑指纹一族）。

### C. 放权门禁引擎（新 agent/gating.py，display-only）

- 输入：shadow.sqlite（shadow_run 全历史）+ 主库 llm_calls（遥测），只读。
- 计算：按域（规则 R*/题类）聚合一致率、过题率、escalation recall、样本量、Wilson CI。
- 判定：对照 `agent/gating_config.json`（阈值草案默认值=Monday 基准：Shadow→Suggest 85%+
  recall≥95%；Suggest→Approve 精确率≥95%；Approve→Auto ≥98%+n≥1000+纠正率<5%；文件头
  注明"草案候 Daniel 裁决，version 字段版本化"），输出各域当前档位 + 离下一档差什么
  （缺样本？缺一致率？缺 recall？）。
- 输出：`data/gating_report.json` + builder-console `export_data.py` 治理段接入 +
  ViewGovernance 新卡「放权阶梯」（各域档位徽章 + 差距白话说明 + 空数据如实标注）。
- **红线（V15 保护条款）**：gating 模块**不得**被 pipeline/ontology_runtime.py、
  agent/mcp_server.py、agent/tools.py import（加静态测试断言）；不改任何工具授权生成；
  所有域当前只可能落 Shadow/Suggest 档（样本量远不足 1000，结构上够不到 Auto）。

## 明确不做（波1 边界）

eval 基线差量回归与金标两分层（独立小单后补）；把档位接到 build_tool_defs（候阈值裁决）；
Command 信封/乐观锁/tenant_id（波2）；Recipe/ViewConfig（波3）。

## 验收

pytest 全绿（含新增：透传后真库 md5 在 --llm 干跑前后不变、gating 静态隔离断言、CI 数学
抽查）；lint --strict 零差异；修复后重跑 `--tier bench1` 长跑得出首份档1 一致率（或如实
标注通道仍不可用）；ViewGovernance 亲验渲染。
