import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 零后端静态应用；相对 base 便于任意子路径静态托管（含 GitHub Pages 子目录）。
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
