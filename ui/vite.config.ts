import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build straight into the FastAPI static dir so one service serves everything.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../api/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
