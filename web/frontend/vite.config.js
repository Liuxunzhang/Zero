import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // Localhost only, matching the backend's default bind. Memory images and
    // plugin output are sensitive; expose the dev server deliberately with
    // `npm run dev -- --host` when LAN access is actually wanted.
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
