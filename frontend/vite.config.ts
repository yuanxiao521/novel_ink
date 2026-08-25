import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 前端 dev 端口 5173。API/SSE 直连后端 http://127.0.0.1:8000：
// - host: true 同时监听 IPv4 与 IPv6，保证 localhost(::1) 与 127.0.0.1 都能访问前端。
// - 后端 CORS 已放行 5173 页面的两个来源（localhost / 127.0.0.1）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
  },
});