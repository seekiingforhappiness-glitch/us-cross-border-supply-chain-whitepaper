# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-06

## 当前位置

- 阶段：**W3 完成（管道+对象层，评估全绿）→ 下一步 W4 风险引擎**

## 已完成

- [x] W0：plan v0.2（决策日志 D1-D11）+ 治理三件套
- [x] W1：ontology manual + JSON + demo-assertions（C1-C6 已裁决）
- [x] W2：datagen 六模块 + 15 设计案例 + ground truth 双表，verify 42 项全绿
- [x] W2+：真实字段 Tier 1（D11）+ 真实感升级（ISO 柜号/船名池/真实商号）
- [x] W3：pipeline 重建对象层 → data/ontology.sqlite；重建精度 100%；DQ 报告
- [x] docs/data-guide.md 小白数据导读；docs/field-gap-analysis.md 字段差距分析

## 阻塞 / 待人确认

- [ ] 阅读 docs/data-guide.md（20 分钟），按 §5 三个动作抽查数据——现在不需要行业经验也能查
- [ ] （随时可做）W1 走查：demo-assertions A 段对照 manual §3/§5

## 下一步（W4 风险引擎与影响传播）

1. `engine/rules.py`：R1-R3 实现（读 ontology.sqlite，显式 as_of_date，D8）
2. `engine/detect.py`：生成 RiskEvent 写入 risk_events 表（A2 语义：同键合并升级）
3. `engine/evaluate.py`：对 expected_risk_events 算查准/查全，KPI：Recall≥95%（高危漏判=0）、
   Precision≥85%（评估判定逻辑受 AGENTS §5 保护，不得为过而改）
4. W4 验收：KPI 达标 + 不达标先修规则禁改指标

## 复现命令

```
python3 -m datagen.generate && python3 -m datagen.verify   # 数据
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate  # 对象层
```
