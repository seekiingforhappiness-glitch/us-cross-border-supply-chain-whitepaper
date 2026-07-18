# 波2 第一批规格：Command 写总线核心 + API 契约硬化 + B-1（V18）

日期：2026-07-16 ｜ 依据：V14 接缝②（写路径归一）+V15 波2+V18（Daniel"你来决定"授权裁决）

## 一、Command 写总线核心（app/command_bus.py 新建，不动 35 对象表结构）

1. **commands 台账表**（新表，建在业务库，append-only）：command_id(uuid)/idempotency_key(唯一,
   可空)/action/actor/role/params_fingerprint(sha256 of 规范化 JSON)/trace_id(可空)/result_status/
   object_id/created_at。
2. **execute_command()** 单一执行入口：查幂等键（命中→原样返回首次结果，不重执行）→ 落 commands
   行 → 调既有 app.actions 函数（**函数本体一行不改**）→ 回填结果。事务边界沿用 action_context。
3. **审批绑提案指纹**：propose 类命令落指纹；approve_mitigation 执行前按 task_id 反查该任务
   propose 命令的指纹，与任务当前 proposal_params 指纹比对——不一致=提案在审批间隙被改，拒绝
   （白话错误："你看到的方案和现在库里的不是同一版"）。指纹存 commands 表，不改 tasks 结构。
4. **三门贯通**：agent/tools.py Toolbox.dispatch、apps/api /actions、/decisions 全走
   execute_command。Streamlit：21 表单入口若有共享调用点则接总线；分散直调则本批只接
   高价值 tab（任务处理台），其余登记欠账如实报告——诚实边界，不许静默漏接。
5. 红线：maker-checker/权限判定仍在 app 层原函数（总线不代判）；冻结区语义不变；
   engine/真值/EXPECTED_*/本体 JSON 不碰。

## 二、API 契约硬化（apps/api）

统一错误信封 {error:{code,message(白话),detail?}}；_StateConflict→HTTP 409；POST 支持
Idempotency-Key 请求头→透传总线；GET /objects 游标分页（keyset，limit+cursor，向后兼容：
无 cursor 行为不变）。

## 三、B-1 三冻结按钮（apps/cockpit）

CloseRiskEvent（风险详情面板：处置完成后"关闭风险"）、ApproveQuoteDecision/
RejectOrRequestMoreInfo（准入案详情：批准报价/驳回或要补件）。全部复用 DecisionButtons 模式
（manager gating/灰态白话/X-Actor/失败原样展示），零新写路（/decisions 白名单四动作已含）。

## 四、明确不做（第二批）

乐观锁 version 列/tenant_id 列（DDL 再生成+md5 新基线，主会话贴身执行）；持久 Agent runtime；
Streamlit 全量收编。

## 验收

回归全绿（pytest/lint/agent_security/shadow_gating/tsc/build）+ 幂等并发用例（同 key 双发
只执行一次）+ 指纹篡改用例（改 params 后 approve 被拒）+ 双库 md5 不因测试变 + 对抗复核过。
