/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    // Pin the API base URL so tests never depend on a local .env file.
    env: { VITE_API_BASE_URL: 'http://localhost:8000' },
    restoreMocks: true,
    unstubGlobals: true,
    // Typing-heavy UI tests take ~2 s alone; under full parallel load they can exceed
    // Vitest's 5 s default. A hanging test still fails, just later.
    testTimeout: 20_000,
  },
})
