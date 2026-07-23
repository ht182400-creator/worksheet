import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发代理：/api 转发到 FastAPI 后端（端口 8000，对应 §25 BASE /api/v1）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
