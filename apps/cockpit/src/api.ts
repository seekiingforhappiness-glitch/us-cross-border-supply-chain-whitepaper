// 薄 API 客户端：只做「fetch + 类型」，不做状态管理/缓存/重试——那些属于画面级实现，
// 候 Daniel 对 docs/superpowers/specs/2026-07-14-cockpit-screen-narrative.md 画面复述确认后再定。
//
// 请求路径固定用相对前缀 /api，由 vite.config.ts 的 server.proxy / preview.proxy 转发到
// apps/api 的真实地址 http://localhost:8100（apps/api 默认端口，见 apps/api/README.md）。
// 不直接 fetch 绝对地址的原因：实测浏览器跨源直连会被 CORS 拦截——apps/api 当前未配置
// CORS 中间件，且加 CORS 超出本单「apps/api 只许参数化小改」的授权范围；走同源的 dev/
// preview 代理不触发 CORS，也不用碰 apps/api 一行代码。双世界切换（验证世界 ⇄ 模拟世界）
// 由 apps/api 进程自己的 ONTOLOGY_DB 环境变量决定，前端这层地址不用跟着变——重启 API
// 换个环境变量，前端下次请求就会拿到新世界的数据。
export const API_BASE_URL = "/api";

// 与 apps/api/main.py::get_ontology_summary 的响应形状对应（只声明本层实际用到的字段；
// objects/links/actions 详细结构留给画面级实现按需扩展，此处不提前定义未用到的类型）。
export interface OntologySummary {
  version: string;
  name?: string;
  displayName?: string;
  world: string;
  roles: string[];
  objects: unknown[];
  links: unknown[];
  actions: unknown[];
  summary: {
    object_types: number;
    links: number;
    actions: number;
    exposed_actions: number;
    frozen_actions: number;
  };
}

/** GET /ontology —— 本体自描述（version/world/对象·关系·动作清单摘要）。
 * 网络失败或非 2xx 一律抛错，调用方（ConnectionStatusBar）负责降级展示，这层不吞异常。 */
export async function fetchOntologySummary(): Promise<OntologySummary> {
  const resp = await fetch(`${API_BASE_URL}/ontology`);
  if (!resp.ok) {
    throw new Error(`GET /ontology 失败：HTTP ${resp.status}`);
  }
  return (await resp.json()) as OntologySummary;
}
