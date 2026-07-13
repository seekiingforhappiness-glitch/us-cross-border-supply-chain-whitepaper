#!/usr/bin/env bash
# 建造者透视镜·一键启动（导出最新数据 → 装依赖(仅首次) → 起服务）
set -e
cd "$(dirname "$0")"
echo "① 导出最新数据…" && python3 export_data.py
[ -d node_modules ] || { echo "② 首次安装依赖…"; npm install; }
echo "③ 启动（浏览器打开 http://localhost:5173）" && npm run dev
