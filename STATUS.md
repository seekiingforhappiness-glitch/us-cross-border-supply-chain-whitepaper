# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-06

## 当前位置

- 阶段：**W2 数据生成完成，verify 全绿，待人抽查 + 1 项批准 → W3**

## 已完成

- [x] W0：plan v0.2（D1-D9）+ 治理三件套 + .gitignore
- [x] W1：ontology manual + JSON + demo-assertions（C1-C6 已裁决）
- [x] W2：`config/datagen.yaml` + `datagen/` 六模块
- [x] W2：`data/raw/`（9 张含噪源表）+ `data/truth/`（ground truth ×2 + 快照）+ sqlite
- [x] W2：verify 全部通过（可复现、15 设计案例、多客户击穿 ≥15）
- [x] 规则澄清同步 manual §7 / JSON（delivered/released 跳过、severity 取最大）

## 阻塞 / 待人确认

- [ ] **批准 plan §9 milestone 估算修订**：1,500-2,500 → 600-1,200（理由见 weekly-notes/W2）
- [ ] 数据真实感抽查（10 分钟）：打开 data/raw/tms_shipments.csv 和 tms_milestones.csv
      扫一眼日期、港口、延误是否像真的；重点看 SHP-2026-0099（DEMO-01 主案例）
- [ ] （随时可做）W1 走查：demo-assertions A 段对照 manual §3/§5

## 下一步（W3 管道与对象层）

1. `pipeline/`：清洗 + 状态标准化 + supplier 名称 entity resolution（限时，超时用硬编码映射兜底）
2. 建 `data/ontology.sqlite`：对象表 + link 表（从含噪源表重建，禁读 data/truth/）
3. 数据质量报告（空值率、判重量、乱序量、状态冲突修正量、ER 命中率）
4. W3 验收：影响传播链一条 SQL 走通；对象数与源数据差异可解释

## 会话交接备注

任何模型接手：读 AGENTS.md §0。复现数据：`python3 -m datagen.generate && python3 -m datagen.verify`。
data/ 在 .gitignore 中，git 里只有代码和配置，数据用命令重新生成。
