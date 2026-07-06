# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-06

## 当前位置

- 阶段：**W4 完成（风险引擎 KPI 满分）→ 下一步 W5 动作闭环与控制塔 UI**

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

## 已完成（续）

- [x] W4：engine R1-R3（as_of 时间旅行安全）+ A2 写库语义 + KPI 评估
      Recall 1.000 / Precision 1.000 / 影响定位 51:51 / 幂等 / 审计 1:1

## 下一步（W5 动作闭环与控制塔 UI）

1. `app/actions.py`：A3-A6 动作函数（沿用 manual §5 签名，D7——W6 直接注册为 AI tools）
2. `app/`：Streamlit 控制塔——风险队列、对象详情（关系跳转）、任务处理台、角色切换器
3. 砍序（plan §11-W5）：先砍独立审批视图→角色切换器→详情页合并；
   不可砍底线：风险队列+任务处理台+动作回写+action_log
4. W5 验收：非开发者按 demo-assertions 5 分钟走完闭环（A8-A13、B4-B7、C 段在此打勾）

## 复现命令

```
python3 -m datagen.generate && python3 -m datagen.verify            # 数据
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate  # 对象层
python3 -m engine.detect && python3 -m engine.evaluate              # 风险引擎
```
