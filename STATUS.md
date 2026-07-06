# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-06

## 当前位置

- 阶段：**W1 建模定稿完成，待最终走查验收后进入 W2**
- 当前周目标：W1 收尾 → W2 数据生成（plan v0.2 §11）

## 已完成

- [x] 路线决策：B、控制塔首闭环、6 周、可演示交付
- [x] `docs/control-tower-plan-v0.2.md`（决策日志 D1-D9）
- [x] `AGENTS.md`（含会话结束协议）/ `STATUS.md` / `CLAUDE.md`
- [x] `docs/control-tower-ontology-manual.md`（11 对象、4 状态机、6 动作、权限矩阵）
- [x] C1-C6 人工裁决完成：C1/C2 人直接裁决，C3-C6 授权 AI，已记入 D9
- [x] `ontology/control-tower-ontology.json`（已校验合法 JSON，与 manual 一致）
- [x] `docs/demo-assertions.md`（A1-A13 / B1-B7 / C1-C4）
- [x] `.gitignore`（data/、sqlite、.env 不入库）

## 进行中

（无）

## 阻塞 / 待人确认

- [ ] W1 最终走查：按 demo-assertions A 段对照 manual §3/§5 过一遍（可与 W2 并行，发现问题随时提）

## 下一步（W2 数据生成）

1. `config/datagen.yaml`：规模、噪声比例、缓冲天数、随机种子
2. `datagen/`：生成器（含 DEMO-01 等 15 个确定性设计案例）
3. 输出 `data/raw/*.csv` + `mock_source.sqlite` + `injected_noise_log` + `expected_risk_events`
4. W2 验收：同种子可复现；两张 ground truth 表与注入严格 1:1

## 会话交接备注

任何模型接手：读 AGENTS.md §0 启动协议即可，无需本会话历史。W1 全部产出已提交 git。
