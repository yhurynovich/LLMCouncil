import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        onProxyReq: (proxyReq, req) => {
          // Forward original client IP to backend for authentication decisions
          const clientIp = req.headers['x-forwarded-for'] || req.socket?.remoteAddress;
          if (clientIp) {
            proxyReq.setHeader('x-forwarded-for', clientIp);
          }
        },
      },
    },
  },
})
