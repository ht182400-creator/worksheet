import '@testing-library/jest-dom'
import { crypto } from 'node:crypto'

// jsdom 环境下补齐 Web Crypto（Node 24 全局已有，但若环境缺失则注入），
// 供 App.tsx 中的 crypto.randomUUID() 使用（幂等键 / 报工）。
if (!globalThis.crypto) {
  globalThis.crypto = crypto as unknown as Crypto
}
