import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  envDir: mode === 'demo' ? false : undefined,
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
  test: { environment: 'jsdom', clearMocks: true, restoreMocks: true, testTimeout: 10_000 },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          charts: ['echarts'],
          ui: ['antd', '@ant-design/icons'],
        },
      },
    },
  },
}));
