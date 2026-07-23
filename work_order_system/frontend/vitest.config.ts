import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 前端单元测试配置（对应 docs/05 §7-B）
// - jsdom：模拟浏览器 DOM，供 @testing-library/react 渲染组件
// - setupFiles：注入 jest-dom 匹配器，并补齐 Web Crypto 供 crypto.randomUUID()
// - include：仅收集 src 下的 *.test.ts(x)，避免误吞源码
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
