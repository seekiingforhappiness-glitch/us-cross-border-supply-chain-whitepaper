# v0.6 规划 — 收尾双专题（风险评分模型 + 单证号级 ER）

版本：v0.6 ｜ 2026-07-07 ｜ 状态：人已指示"完成后面的所有"，据此批准执行
本文件为 v0.6 决策载体（H 系列，append-only）。

## 专题一：风险评分模型（Y1）

**目标**：把 ground truth 用于监督学习，学"ML 落地"而非追指标。

**H1 — 两段式设计，预设诚实结论。**
(a) 开船前静态模型：仅用 etd 前可知特征（航线/船司/柜型/月份/供应商交期/贸易术语），
预测该票是否会发展出 R1 延误风险。**预期 AUC≈0.5 并如实报告**——本世界的延误
是随机注入的，静态特征本无信号；模型学到"无信号"是正确结果，不是失败。
(b) 在途早警模型：仅用首个 eta_change 的幅度与时点等**早期**事件特征（严格早于
风险判定所需信息），预测最终是否击穿。此处应有真实信号。
**评估器只对 (b) 设门槛（AUC≥0.7），对 (a) 只验证诚实报告**——不给随机世界定不可能的指标。

**H2 — 零新依赖。** 纯 Python 手写逻辑回归（梯度下降），不引入 sklearn/numpy。
训练/测试按 shipment_id 哈希切分（确定性 70/30），特征与标签只来自
ontology.sqlite + truth（评估层可读 truth）。

**交付**：engine/scoring.py（特征/训练/评分）+ engine/evaluate_scoring.py
（AUC、precision@10、校准表、两段结论）+ 评分写回 shipments.risk_score_static/early 两列
（pipeline 建列，engine 填充，UI 不强制展示）。

**H1 勘误（Y1 评审时追加）**：静态模型实测 AUC=0.75，非预设的 ≈0.5。诊断为
**as_of 快照删失混杂**：早开船的票多已妥投（R1 规则跳过 delivered → 无标签），
晚开船的仍在途更易被标记——按月正例率 5 月 0% → 7 月 35%。表观信号来自标签删失
而非静态特征的因果预测力，plan 的"无信号"前提在因果意义上仍成立，但预设未考虑
标签生成机制的时点效应。这是比"预期正确"更有价值的 ML 教训：先审标签，再谈特征。

## 专题二：单证号级 ER（Y2，G1 完全体·有界版）

**目标**：兑现字段调研时记录的结构性差距 G1——真实承运商事件不带内部 shipment_id。

**H3 — 爆炸半径受控。** 只改 tms_milestones：源表**删除 shipment_id 列**，
改带 booking_no 与 container_no（真实 EDI 形态：两者至少其一，10% 只有柜号，
5% 只有订舱号）；tms_shipments 保留 shipment_id（TMS 内部键，合理）。
管道新增解析步：按 booking_no → 唯一票；仅有 container_no → 经 containers 表反查；
注入 2% 坏单证号（订舱号 typo）→ 解析失败进 unresolved 停车表（不丢弃、不猜），
DQ 报告解析率。**设计案例船的事件保证可解析**（断言不受影响）。

**交付**：datagen 里 milestone emit 改造 + 坏单证号噪声（新噪声类型
doc_ref_typo 记入 noise_log——本 plan 即为其审批记录）+ pipeline 解析步 +
verify/pipeline.evaluate 适配 + unresolved 停车表 + 全量回归。

## 验收与工作方式

- 每专题一次 Opus 委托、Fable 独立重跑评审；全部既有评估器零回归为硬门
- Y2 后九评估器 + evaluate_scoring 共十个全绿（scoring 的 (a) 段按 H1 只验诚实报告）
- 收尾：复盘/README/STATUS 更新，作品文档同步关键数字
