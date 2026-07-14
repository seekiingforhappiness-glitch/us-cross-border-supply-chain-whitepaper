# apps/cockpit —— 驾驶舱（地基）

> V4 决议⑥授权的 React 双应用地基之一。**本目录当前只有工程骨架 + API 数据源接线**，
> 三个占位视图（全景/对象卡/AI 工作流）等 Daniel 对
> `docs/superpowers/specs/2026-07-14-cockpit-screen-narrative.md`（画面复述文档）回复
> "对，就是这个画面"之后，才做画面级布局/视觉设计。这份 README 只讲地基怎么跑，不讲画面。

沿用 `apps/builder-console` 同款技术栈：**Vite + React 18 + TypeScript（strict）**，
不引入任何新框架/状态库/UI 库。与 builder-console「零后端静态站点」不同，驾驶舱是
**活连接**的——运行时直接 `fetch` `apps/api`（FastAPI）的 `/ontology` 路由，不做构建期数据导出。

## 启动顺序（两步，先 API 后前端）

```bash
# ① 先起 API（仓库根目录下，另开一个终端）
uvicorn apps.api.main:app --port 8100

# ② 再起驾驶舱前端（本目录下，另一个终端）
cd apps/cockpit
./start.sh
# 等价于：npm install（仅首次）+ npm run dev → 浏览器打开 http://localhost:5174
```

顺序颠倒不会报错——前端启动不依赖 API 已就绪，只是首页顶部的连接状态条会显示
"API 未连接（uvicorn 启动命令见 README）"，等 API 起来后刷新页面即恢复正常（优雅降级，
见下「连接状态条」一节）。

`apps/builder-console` 的 dev server 默认占用 5173，驾驶舱固定用 **5174**（`vite.config.ts`
的 `server.port`），两个 `apps/` 前端可以同时开着跑，互不冲突。

生产构建与本地预览：

```bash
npm run build     # tsc 类型检查 + vite 打包到 dist/
npm run preview   # 本地静态预览 dist（http://localhost:4174）
```

## 双世界切换（ONTOLOGY_DB）

`apps/api` 连哪个 sqlite 库，由**启动 API 进程时**的 `ONTOLOGY_DB` 环境变量决定——
驾驶舱前端本身不用切换任何配置项，只要重启 API 换个环境变量指向，前端下次请求
`GET /ontology` 就会拿到新库的数据（连接状态条里的 `world` 字段会跟着变）：

```bash
# 验证世界（缺省）：data/ontology.sqlite，datagen 种子库，规则档案 R/P=1.000 对照源
uvicorn apps.api.main:app --port 8100
# → GET /ontology 的 world 字段 = "verification"

# 模拟世界：data/simworld.sqlite，14 个月连续活世界（见 sim/store.py 头注）
ONTOLOGY_DB=data/simworld.sqlite uvicorn apps.api.main:app --port 8100
# → GET /ontology 的 world 字段 = "simulation"
```

（`ONTOLOGY_DB_PATH` 是 `agent/mcp_server.py` 的既有同名环境变量约定，`apps/api` 仍兼容
识别；`ONTOLOGY_DB` 优先，新脚本/文档一律用 `ONTOLOGY_DB`。详见 `apps/api/README.md`。）

## 连接状态条

首页顶部常驻一行，零硬编码，全部字段现查 `GET /ontology`：

```
已连接 {world} 世界 · 本体 v{version} · {object_types} 对象/{links} 关系/{actions} 动作
```

`apps/api` 未起（或网络失败）时降级显示：

```
API 未连接（uvicorn 启动命令见 README）
```

### 技术细节：为什么走 `/api` 代理而不直连 `http://localhost:8100`

`src/api.ts` 请求路径固定用相对前缀 `/api`（如 `/api/ontology`），由 `vite.config.ts` 的
`server.proxy` / `preview.proxy` 转发到 `apps/api` 的真实地址。**不直接 fetch 绝对地址**
是因为实测浏览器跨源直连会被 CORS 拦截——`apps/api` 当前未配置 CORS 中间件（`curl -H
"Origin: http://localhost:5174" http://localhost:8100/ontology` 可复现：响应缺
`access-control-allow-origin` 头），而加 CORS 中间件超出本单「`apps/api` 只许参数化小改」
的授权范围。走 dev/preview server 自带的同源代理不触发 CORS，也不用碰 `apps/api` 一行代码——
这是刻意的范围收敛决策，未来如需支持生产环境静态部署（不经 Vite 代理），`apps/api` 侧仍需
补 CORS 中间件（挂账，见交付报告歧义清单）。

## 目录

```
apps/cockpit/
├── start.sh                  # 一键启动（装依赖 + npm run dev，端口 5174）
├── src/
│   ├── api.ts                 # 薄 API 客户端（fetch GET /ontology，无状态管理/缓存）
│   ├── App.tsx                 # 顶部连接状态条 + 三视图占位切换（useState，无 router 依赖）
│   ├── main.tsx
│   ├── styles.css              # 仅盒模型重置，无视觉设计
│   ├── components/
│   │   └── ConnectionStatusBar.tsx
│   └── views/                  # 三个占位视图：标题 + 一行"画面候 Daniel 对齐后实现"
│       ├── Panorama.tsx        # 全景
│       ├── ObjectCard.tsx      # 对象卡
│       └── AiWorkflow.tsx      # AI 工作流
└── index.html
```

## 边界（本单交付物范围）

只有工程骨架 + API 数据源接线，**不含任何画面级布局/视觉设计**——三个占位视图故意只有
纯文字，等 Daniel 对画面复述文档确认后再做画面级实现。package.json 依赖集合与
`apps/builder-console` 完全一致（react / react-dom / @vitejs/plugin-react / typescript /
vite 及其类型包），未新增任何框架/状态库/UI 库。
