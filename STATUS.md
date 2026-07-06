# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-06

## 当前位置

- 阶段：**W5 正式关闭（C3 人工走查通过，24/24 断言全部打勾）→ W6 AI 协同层（最后一周）**

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

## 已完成（续 2）

- [x] W5：app/actions.py（A3-A6 五要素+越权审计）+ Streamlit 控制塔四视图 + 角色切换
- [x] W5：test_closed_loop 全绿（A8-A13/B4-B6/C1-C2/C4）；UI AppTest 双角色无异常
- [x] demo-assertions 除 C3 外全部自动化通过

## 阻塞 / 待人确认

- [x] C3 五分钟走查完成（2026-07-06）：Daniel 在 UI 亲手走完 RSK-0044 全闭环，
      顺带现场验证 B4/B6/B7；发现并修复 UI 缺陷一处（成功回执被 rerun 冲掉导致重复点击）
- [ ] 阅读 docs/data-guide.md 抽查数据（遗留，不阻塞）

## 下一步（W6 AI 协同层与收尾）

1. `agent/tools.py`：注册 A3-A6 + 3 个查询函数（对象/影响链/审计历史）为 LLM tools（D7 兑现）
2. `agent/`：风险解释与处置建议（proposal-only，复用 v0.1 手册 §7 护栏与输出 schema）
3. 10 题评估集 + 越权/编造测试（AI 回答必须可溯源到对象 ID）
4. 终版 README、架构说明、项目复盘

## 复现命令

```
python3 -m datagen.generate && python3 -m datagen.verify            # 数据
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate  # 对象层
python3 -m engine.detect && python3 -m engine.evaluate              # 风险引擎
python3 -m app.test_closed_loop                                     # 动作闭环（临时副本）
streamlit run app/streamlit_app.py                                  # 控制塔 UI
```
