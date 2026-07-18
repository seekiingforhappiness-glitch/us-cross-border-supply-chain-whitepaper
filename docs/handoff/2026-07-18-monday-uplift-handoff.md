# 交接简报：Monday 对标全面提升弧线（V13-V20）收官

存档：2026-07-18 ｜ 分支：`claude/project-handoff-api-layer-cd4f51`（worktree，已全量推送，HEAD=eafa806）
读者：下一个接管会话（冷启动可读）。**先读 AGENTS.md 启动协议与 STATUS.md 头部，再读本简报。**

## 一、一句话现状

Daniel 裁的八连决议（V13-V20）全部落地：驾驶舱有了完整的 AI 处置闭环（人发起→AI 干活带证据→
停等人批→人拍板→AI 写后复读收尾），底座有了单一写总线+乐观锁接缝+持久 runtime，治理有了
真实测的一致率与放权阶梯。工作区干净、发版门全绿、全部推送。

## 二、决议与交付对照（决策日志 docs/control-tower-plan-v0.2.md §4 为权威源）

| 决议 | 内容 | 主交付 commit |
|---|---|---|
| V13 | 审批搬回驾驶舱 + 两处回放 | 750991d（A-1 人类决策通道）/ 9681686（A-2 时间轴回放） |
| V14 | 终局定位：路 C 单租户+三接缝 | 89afceb（接缝清单：tenant_id/写路径归一/视图配置化） |
| V15 | 提升路线：丙·三波纵向切片 | 9afc6ea + 波1 7977e2f（G-Shadow 修复/prompt 版本/放权门禁 display-only） |
| V16 | 阈值 98% ratified/recall 定案/WAIVED+宪法六行 | 0e93e47（AGENTS §6.1 执行者纪律在此入宪） |
| V17 | 波U 用户正脸七件 | 89a28b9（双世界切换/数字溯源/信任档/三态/对象卡35类/协作流/U7评估书） |
| V18 | U7=C+B-1；波2 开工 | a61ec46（Command 总线+API 硬化+B-1）/ b69e7d6（乐观锁+tenant_id 落满35表）/ ed5400e（持久 runtime） |
| V19 | 智能化正脸+生产级收编 | 56d974a（runtime 治理 API 五端点+AI 处置一键流+准入指纹绑定+Streamlit 17 写点全收编）+ 98adaa0/6f91b1c（实测暴露的两勘误） |
| V20 | 证据链智能 | eafa806（/proposals/{id}/evidence 四块证据包+证据卡+真模型按趟开关） |

## 三、下个会话必须有的心智地图（架构新地标）

1. **单一写总线**：一切生产写（MCP dispatch/HTTP /actions/decisions/Streamlit/runtime）过
   `app/command_bus.py::execute_command`——幂等键、参数指纹、**审批绑产物指纹**（任务域+准入域，
   动作名不可知：基线=落库后按产物行现算的 proposal_fingerprint）。全仓 grep 零绕行（对抗复核证）。
2. **双世界**：X-World 头 verify/sim 切库不重启；写也跟随世界。模拟世界已迁移接缝列+运营三表
   （action_log/dq_issues/integration_outbox——6f91b1c 勘误，缺表曾致 sim 全部写崩）。
3. **接缝列**：35 对象表全员 `version`（触发器自增、显式设值让位）+ `tenant_id`（恒 'default'）。
   引擎表走链尾幂等迁移 `pipeline/apply_seam_columns`（engine/ 零改动）。
4. **持久 runtime**：`agent/runtime.py` 状态机（等审批/断点恢复/预算/loop guard/kill）；每步=
   总线一条幂等命令（run:{id}:{step}）；冻结区结构性不可达（只读 approval_status 观察）。
   治理 API 五端点在 `apps/api/runtime.py`（kill 仅 manager 双层）。
5. **证据链**：`apps/api/evidence.py` 纯读聚合（影响/先例+有效率/域信任档/备选代价），
   前端 `EvidenceCard` 嵌在批准键之前。不 import agent.gating（静态隔离红线）。
6. **确定性尺子已精化**：文件 md5 只管"测试期间未触库"；**重建确定性看
   `pipeline/db_digest` 业务逻辑指纹**（遥测表豁免清单在该文件头，新增遥测表必须登记理由）。
   基线 9582dcb0（两次全链重建实证）。重建链尾必须跑 apply_seam_columns（release-checklist §0 已登记）。

## 四、关键真数字（全部一手实测）

- 档1 一致率（AI 建议 vs 人历史决定，180 例）：总体 53.9%；critical 27.3%；high 59.9%——
  proposal-only 的最硬实证。分域数据喂放权阶梯（21 域全影子档）与证据卡信任块（R1: 38.2% n=76）。
- 金标真 AI 过题 9/25=36%（scripted 基准 29/29）；键词判分偏严，解释权候校准。
- 回归基线：pytest apps/api **125 passed**；agent.test_runtime 60 项；security 288 注入全拒；
  lint --strict 零差异；db_digest ×2 一致。

## 五、勘误与教训（复发即倒查本节）

1. "文件 md5 重建一致"不变量自 G-Ledger 起已不成立（真实时间戳）——尺子精化为业务指纹（见三.6）。
2. 位置式 INSERT 是接缝列的天敌：build_ontology 与 admission_actions 各中一次（后者曾让准入
   指纹绑定在真库名存实亡、被**合成基线的绿测遮住**——规则 6 教科书案例；真通道堵门测试已固化）。
3. 模拟世界≠只读展品：可写化后必须有运营表（三.2）；测试世界观同步更新（c14560a）。
4. workflow 验证段停滞已 5 次（疑通道限流），惯例=主会话接手收尾；执行代理 API 断连可
   SendMessage 原地续命（上下文保留）。
5. 用户实测是最高级复核：本弧线四个真缺陷（发起入口找不到/sim 写崩/徽章歧义/角色钮太远）全部
   来自 Daniel 用了两下，任何自检都没抓到。

## 六、挂账清单（下个会话的活）

- **候 Daniel 裁决**：WAIVED 通道首用场景；接真实数据时机与介质（裁决题5）；U7 评估书内
  问题4 之外部分已裁（C+B-1 已落）。
- **工程排队**：放权档位接线到工具授权（V16 已批阈值，接线归"波2 语义"——当前无域够格，
  不急）；ViewConfig（V14 接缝③，波3）；Recipe 自动化（波3/裁决题2）；角色切换钮移近动作区
  （五.5 第 4 条）；runtime planner 值级 schema 校验；eval 基线差量回归+金标两分层。
- **在飞**：独立小会话修 test_cockpit ai-flow kind 断言（任务 f15cd416）——落地前勿动该处。
- **可选长跑**：金标 bench2 用修复后通道复测（首轮 36% 样本 25）；真模型 runtime 试跑几趟攒
  llm 档遥测。

## 七、环境速查

```bash
# 服务（nohup 脱管，会话重启不掉）：API :8100（ONTOLOGY_DB=data/ontology.sqlite）
#   驾驶舱 dev :5174（vite，/api 代理→8100）；透视镜 preview :4174
# 全链重建（会清运营遥测，真值不变）：release-checklist §0（链尾含 apply_seam_columns）
# 确定性门：python3 -m pipeline.db_digest ×2 应一致（9582dcb0）
# 影子测量：python3 -m agent.shadow_bench --llm --tier {bench1,bench2} [--resume RUN_ID]
# 放权报告：python3 -m agent.gating → data/gating_report.json（display-only）
# runtime CLI：python3 -m agent.runtime --start/--resume/--list/--kill
```

红线不变：engine/判定与真值只读；EXPECTED_* 只增不改；冻结区四动作永不入 AI 面；
决策日志 append-only 业务语义须 Daniel 亲批；子代理不 commit；主会话独立复核门不可替代。
