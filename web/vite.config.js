import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发期前端跑在 5173，后端在 8000。
// 用 proxy 把 /api 和 /files 转过去，前端代码里直接写相对路径即可，不用处理 CORS。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/files': 'http://127.0.0.1:8000',
    },
  },
})
