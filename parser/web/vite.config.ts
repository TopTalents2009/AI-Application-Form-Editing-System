import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const parserTarget = 'http://127.0.0.1:8767'

export default defineConfig({
  plugins: [vue()],
  base: '/chatinsight/',
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    open: '/chatinsight/',
    proxy: {
      '/parser-api': {
        target: parserTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/parser-api/, '/api'),
      },
      '/api': {
        target: parserTarget,
        changeOrigin: true,
      },
    },
  },
})
