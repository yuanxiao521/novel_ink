import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';

// 多页（导演台 index / 主界面 dashboard / 人物 cards）各自作为独立 HTML 入口。
// dev 下 Vite 直接按路径服务；build 时通过 rollupOptions.input 保留三页。
export default defineConfig({
  root: '.',
  server: {
    port: 5173,
    // API 走后端（端口 8000，已配 CORS 放行 5173）；此处预留代理，后续可去掉前端直连
    proxy: {},
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('index.html', import.meta.url)),
        dashboard: fileURLToPath(new URL('dashboard.html', import.meta.url)),
        characters: fileURLToPath(new URL('characters.html', import.meta.url)),
      },
    },
  },
});