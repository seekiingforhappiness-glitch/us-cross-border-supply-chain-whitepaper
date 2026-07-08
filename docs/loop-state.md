# Loop 迭代追踪器（自定步调，目标 15 次，交付可面客的完整控制塔）

> **给循环里的每次自己**：每次醒来先读这个文件 → 做"下一项未完成" → controller 独立复核（不信报告只信输出）
> → 更新本文件勾选 + git commit + push → ScheduleWakeup 续下一次。全部勾完 → 停 + PushNotification。
>
> **边界（§3 最高规则）**：本 loop 只做 §4「AI 主导人验收」的**完成/打磨/加固**，**绝不擅自开新业务域或新增对象/规则**
> （关务/资金风控等需 Daniel AskUserQuestion 亲批；碰到就 AskUserQuestion 停下，不自己拍板）。
> 每次迭代必须：既有 R1-R18 全 P/R=1.000 不扰动、agent 不越权、ground truth 不改、maker-checker/FORBIDDEN 不削弱。

## 迭代清单（15 项，均为 §4 无需逐次批准）

- [x] **1. Warehouse 富工作台 + permission-aware 对象级 agent**（补齐对象中心对称性；无新对象/规则）
- [x] **2. 全局 Executive 一页视图**（跨 5 场景 KPI：各域风险数/SLA/追回额/精度；manager 落地页增强）
- [x] **3. `docs/architecture.md` 刷新**到 5 场景全貌（对象图/场景连接/治理层）
- [ ] **4. `README.md` 重写**→ 全景导航（5 场景/R1-R18/对象工作台/复现命令/demo 台本指针）
- [ ] **5. demo-assertions 合并/新增**→ 5 场景统一验收清单（可勾选走查项）
- [ ] **6. 对抗测试加固**：5 域越权杀手统一测试 + 边界/负例用例补强
- [ ] **7. 代码质量 pass**：5 域 approve_mitigation 动作分支去重/共用 helper（不改行为，回归全绿）
- [ ] **8. 多区域 demo 数据**：让行级数据范围（region）可见地过滤（守真值 md5、engine R/P 不变）
- [ ] **9. agent eval 扩展**：覆盖采购/仓储问题（原则性加题，不放宽判定；同 scope_parity 口径）
- [ ] **10. `docs/field-gap-analysis.md` + retrospective 更新**到当前全貌
- [ ] **11. 一页纸（可视化 Artifact）** for 面客：五场景控制塔全景图
- [ ] **12. 接手/onboarding 文档**：新会话/新模型 5 分钟上手路径
- [ ] **13. 健壮性 pass**：streamlit 启动/空数据/边界输入的优雅处理 + 冒烟测试
- [ ] **14. 全链端到端验证 + release checklist**：一键复现全绿证据集
- [ ] **15. 最终整合**：release notes + STATUS 收官 + 全评估器全绿快照

## 进度日志

（每次迭代在此追加一行：`迭代 N — <做了什么> — <controller 复核结论> — <commit hash>`）

- 迭代 1 — Warehouse 升第 6 富对象 + 富工作台（库存位/预留/盘点/锚定风险）+ permission-aware 对象级 agent — controller 独立复核全绿：wh-agent 三角色 approve 全被拒、ROLE_PERMS/FORBIDDEN/warehouse_actions diff=0、R1-R18 全 P/R=1.000、标准视图 28→27、全套回归绿 — 见 loop 提交
- 迭代 2 — 全局 Executive 一页视图（manager 落地页 = 5 场景 KPI 一屏：延误/费用/采购/仓储/准入 + 全局健康横条）— controller 独立复核全绿：纯只读聚合、actions/rbac_nav/ontology diff=0、executive_view 测试全绿（数字与直查一致，152 总未结）、R1-R18 未动、三角色 UI 0 异常 — 见 loop 提交
- 迭代 3 — docs/architecture.md 全面刷新到当前 5 场景（分层架构/对象图含跨场景连接/对象中心层/现货救延误时序/治理体系/关键数字）— controller 核对对象数字与代码一致（33 对象/6 富对象/27 标准视图）— 纯文档 — 见 loop 提交
