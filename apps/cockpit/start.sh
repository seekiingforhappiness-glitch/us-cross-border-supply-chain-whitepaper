#!/usr/bin/env bash
# 驾驶舱前端·一键启动（装依赖(仅首次) → 起开发服务器，端口 5174）
# 依赖 apps/api 已在另一个终端起服务（uvicorn apps.api.main:app --port 8100）；
# 未起时页面会优雅降级显示"API 未连接"，不阻塞前端本身启动——详见 README。
set -e
cd "$(dirname "$0")"
[ -d node_modules ] || { echo "① 首次安装依赖…"; npm install; }
echo "② 启动（浏览器打开 http://localhost:5174；FastAPI 需另开终端跑在 8100，见 README）" && npm run dev
