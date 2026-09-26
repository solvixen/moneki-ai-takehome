import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 开发时代理 /api 到本机问答服务（先启动 uvicorn 再 npm run dev）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
