import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/sessions': { target: process.env.BAND_API_URL || 'http://127.0.0.1:8765', ws: true },
      '/score-soundfont': { target: process.env.BAND_API_URL || 'http://127.0.0.1:8765' },
      '/drum-kit': { target: process.env.BAND_API_URL || 'http://127.0.0.1:8765' },
    },
  },
})
