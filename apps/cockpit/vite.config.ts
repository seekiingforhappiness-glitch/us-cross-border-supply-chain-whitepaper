import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 驾驶舱前端：沿用 builder-console 同款栈，但不是零后端——运行时直接 fetch apps/api
// （FastAPI，默认 8100），构建产物仍是纯静态站点，base 用相对路径便于任意子路径静态托管
// （沿用 builder-console 惯例）。dev server 端口固定 5174，与 builder-console 的 5173 错开，
// 两个 apps/ 前端可以同时开着跑。
//
// /api 反向代理到 apps/api：实测浏览器直连 http://localhost:8100 会被 CORS 拦截
// （apps/api 当前未配置 CORS——curl -H "Origin: http://localhost:5174" 复现响应确实缺
// access-control-allow-origin 头；改 apps/api 加 CORS 中间件超出本单「apps/api 只许
// 上述参数化小改」的授权范围）。走 dev/preview server 自带的反向代理是同源请求，不触发
// CORS，且改动完全落在 apps/cockpit 自己的授权范围内——src/api.ts 固定请求相对路径
// /api/*，这里转发到真实的 apps/api 地址，两处路径前缀必须保持一致。
const API_PROXY = {
  "/api": {
    target: "http://localhost:8100",
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5174,
    proxy: API_PROXY,
  },
  preview: {
    proxy: API_PROXY,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
