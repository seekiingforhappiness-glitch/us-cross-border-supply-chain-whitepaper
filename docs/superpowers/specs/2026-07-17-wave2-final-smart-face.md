# 波2 收尾批规格：AI 处置正脸 + 生产级收编（V19）

## ① runtime 治理 API（apps/api/runtime.py 新建，router 工厂挂载同 governance/decisions）

- GET /runtime/runs（列表：status/goal/budget 摘要/updated_at，游标同 /objects 惯例）
- GET /runtime/runs/{id}（详情：状态+预算余量+steps 时间线全量，白话 kind 标签）
- POST /runtime/runs {goal_risk_id}（X-Actor 必填；启动=创建 run 并同步推进到首个稳态
  （waiting_approval/终态），LLM 不可用降级模式如实标 mode；提案-only 语义原样）
- POST /runtime/runs/{id}/resume（X-Actor；等审批三分支语义透传，白话结果）
- POST /runtime/runs/{id}/kill（**manager 专属**人类安全控制，X-Role+X-Actor 双查，
  审计落 action_log 语义级记录或 runs 表 killed_by 留痕；幂等）
- 全端点：统一错误信封/409 语义沿既有；X-World 头同门（runtime 表在对应世界库）。

## ② ApproveQuoteDecision 产物指纹绑定（app/command_bus.py 扩展）

沿 2b"产物指纹、动作名不可知"原则扩准入域：任何写命令 object_id 指向 admission case 且
案件处于待决态（读 schema 定精确谓词）→ 落 case 决策关键字段的规范指纹；ApproveQuoteDecision/
RejectOrRequestMoreInfo 执行前校验当前 case 指纹 vs 基线，不一致白话拒绝（同 mismatch 文案
风格），no_baseline 放行留痕。测试：篡改 case 字段后批→拒；未改→过；老案 no_baseline。

## ③ Streamlit 余量收编（app/streamlit_app.py + dq/coordination/admission 调用点）

U7 评估书 §1.1/1.3 清单里全部剩余写调用点（risk/po 的 assign、risk close、adm b1-b6、
coord 五动作、dq 两动作）统一经 execute_command（薄 helper 允许，语义零改动）；收编后
grep 证明全仓无绕总线的生产写调用（测试脚本除外）；欠账清零声明入报告。

## ④ 驾驶舱 AI 处置正脸（apps/cockpit）

- ImpactPanel 风险详情加「让 AI 处置」按钮（ops 可点；点后调 POST /runtime/runs，卡内
  出现 run 状态徽章）；
- 右栏 AI 工作流新增「AI 任务」区：进行中 runs 列表（状态徽章/预算条/最后一步白话），
  点开=步时间线（复用逐步点亮语言）；等审批 run 高亮提示"去待拍板"；
- 审批联动：Z7 批完提案后，对应 run 卡出现「继续执行」（调 resume），完成态显示写后复读
  结论；manager 可见 kill 按钮（二次确认），非 manager 灰态白话；
- 全部状态走 StateHint 诚实空态；模拟/验证双世界跟随 X-World。

## 红线与验收

engine/真值/EXPECTED_*/本体 JSON 不碰；冻结区语义不变（runtime 仍不可达审批）；
app/actions.py 函数本体不改；全量回归（pytest/security/lint/digest×2/test_runtime/
closed loops/tsc/build）+ 对抗复核；主会话浏览器终验（含 B-1 点穿）。
