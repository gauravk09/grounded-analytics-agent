import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The browser talks to ONE origin. Without this proxy every request is cross-origin, so the API
  // needs CORS rules that differ between dev and production — two configurations to keep in step,
  // and only one of them ever gets tested.
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
