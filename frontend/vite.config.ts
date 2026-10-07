import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Vitest only affects `npm test`; the production build is unchanged.
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  server: {
    proxy: {
      // Dev-only: stream POST /query to the local FastAPI server. Keeping the
      // client on a relative /query URL avoids CORS and hard-coded origins.
      '/query': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
