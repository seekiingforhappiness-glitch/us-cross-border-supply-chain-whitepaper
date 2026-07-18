// 严重度排序权威源（B 批工程清理去重）：ImpactPanel.tsx（具名 const）、corridorModel.ts（具名
// const，经 laneSevRank 导出）、LaneQueue.tsx（内联字面量 { critical: 3, high: 2, medium: 1,
// low: 0 }）三处曾各自重复定义同一张表——归一到这一处，三处改 import 复用。
// 未知 severity 一律回落 1（= medium 权重）——三处原实现里凡带 `?? 1` 兜底的用法保持不变行为。
export const SEV_RANK: Record<string, number> = { critical: 3, high: 2, medium: 1, low: 0 };

export function severityRank(s: string): number {
  return SEV_RANK[s] ?? 1;
}
