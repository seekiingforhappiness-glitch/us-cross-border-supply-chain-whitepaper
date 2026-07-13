"""模拟世界引擎。
S1：世界骨架 + 时间引擎 + 14 个月历史回填。
S2：异常谱系（anomalies）+ 连锁引擎 + AI 同事运转回路（detectors + ai_loop）。

与 datagen/ 物理隔离：本包只写 data/simworld.sqlite，绝不触碰 data/ontology.sqlite、
data/truth/ 或既有 datagen/engine/agent/app 代码（提案 §五红线）。一切模拟产物显式标 source='sim'。
"""
