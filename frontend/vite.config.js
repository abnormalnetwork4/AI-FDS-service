import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // 두 페이지: 위험도 대시보드(index.html)와 사내 AI 채팅(chat.html, /chat.html)
  build: { rollupOptions: { input: { main: 'index.html', chat: 'chat.html' } } },
  // 브라우저는 같은 주소의 /api를 호출하고 Vite가 개발용 FDS 서버로 전달합니다.
  server: { host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8000', '/chat-api': 'http://127.0.0.1:8100' } },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8000', '/chat-api': 'http://127.0.0.1:8100' } },
})
