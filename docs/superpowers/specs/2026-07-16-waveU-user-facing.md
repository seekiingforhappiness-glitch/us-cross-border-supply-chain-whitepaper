# 波U 规格：前端用户正脸整体升级（U1-U7 全量，V17）

日期：2026-07-16 ｜ Daniel 裁决："启动workflows模式，按任务分层用不同的模型执行所有任务。"
（= U1-U6 全量实现 + U7 出评估方案；分层=Opus复杂件/Sonnet常规件；Fable 编排+验收）

- **U1 双世界一键切换**：API 全读端点支持 `X-World: verify|sim` 请求头（缺省=启动环境变量，
  向后兼容 byte-identical）；驾驶舱顶栏世界切换钮，切换后全舱数据源跟随（时间回放在 sim 世界
  才有完整威力）。两库路径解析进 get_db_path 依赖，不重启进程。
- **U2 数字溯源**：驾驶舱指标点开"你从哪来"：口径白话+来源表/对象+样例 id+跳转透视镜提示。
  后端在聚合端点附 provenance 信封（每指标：caliber/sources/sample_ids），前端 popover。
  范围=七区卡+队列列头（ObjectCard 归 U5 域，不碰）。
- **U3 AI 可信度正脸**：API 新只读端点暴露 data/gating_report.json 摘要；驾驶舱 AI 运营账卡
  显示"当前档位+白话为什么"（治理翻译成老板语言，display-only 语义原样传达）。
- **U4 三态系统化**：加载/空/错误/无权四态统一组件（暗色 token、白话文案），全舱视图接入。
- **U5 对象卡白话铺开**：objectLabels 映射层从部分对象铺满 35 类（中文名/字段白话/分组），
  数据尽量由本体 JSON 描述生成不手抄；ObjectCard 渲染分组+白话。只动 objectLabels.ts/
  ObjectCard.tsx/新数据文件。
- **U6 协作流真身**：API 只读端点暴露 coordination_threads（+消息行若有）；驾驶舱右栏
  "协作流"标签从占位变真数据（sim 28 条），按风险/对象聚合展示，留飞书/企微接入的 UI 形状。
- **U7 操作台归一评估**（文档，不动代码）：Streamlit 操作台迁驾驶舱的方案书：现状盘点/
  三候选（全迁/只迁高频动作/不迁）/工作量/风险/推荐，候 Daniel 裁。

红线（全体执行者）：AGENTS §6.1 执行者纪律；engine/真值/EXPECTED_*/本体 JSON/agent/ 不碰
（U3 只读 gating_report.json 不 import gating）；冻结区/权限语义不动；X-Role 脱敏在新端点
同样生效；不 commit；不做浏览器验证（主会话统一验收）；回归=pytest apps/api + lint --strict
+ tsc + build + 双库 md5 不变。
