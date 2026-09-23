import { defineConfig } from 'vitest/config'

export default defineConfig({
  server: {
    port: 5173, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', rewrite: path => path.replace(/^\/api/, '') } },
  },
  preview: {
    host: '127.0.0.1', port: 4173, strictPort: true,
    headers: { 'X-Bidding-Demo': '1' },
    proxy: { '^/api/chat/stream(?:\\?|$)': { target: 'http://127.0.0.1:8000', rewrite: path => path.replace(/^\/api/, '') } },
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
