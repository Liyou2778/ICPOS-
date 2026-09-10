import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// 开发模式：/api 与 /ws 代理到本地后端（harness 一键模式会用后端静态托管 dist，无需代理）
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_API_TARGET || 'http://127.0.0.1:8000';
  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': { target, changeOrigin: true },
        '/ws': { target, changeOrigin: true, ws: true },
      },
    },
    build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1200 },
  };
});
