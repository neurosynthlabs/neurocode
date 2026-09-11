import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// ~60% of the entry chunk was framework code. Splitting it into its own chunks keeps every chunk
// under 500 KB and lets the browser keep React cached across deploys — app code changes far more
// often than the framework does.
const vendor = (pkgs: string) => new RegExp(`[\\\\/]node_modules[\\\\/](${pkgs})[\\\\/]`)

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': new URL('./src', import.meta.url).pathname },
  },
  // Dev only: /api/* is the local FastAPI server (server/), started next to Vite by scripts/dev.sh.
  server: {
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.NC_API_PORT ?? 8787}`,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'react', test: vendor('react|react-dom|scheduler'), priority: 30 },
            { name: 'router', test: vendor('react-router|react-router-dom|@remix-run'), priority: 20 },
            { name: 'ui-kit', test: vendor('@base-ui|@floating-ui|@radix-ui|cmdk|sonner|react-remove-scroll|react-remove-scroll-bar|react-style-singleton|use-callback-ref|use-sidecar|tabbable|aria-hidden|get-nonce|tslib'), priority: 10 },
          ],
        },
      },
    },
  },
})
