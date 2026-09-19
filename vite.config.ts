import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// package.json is the one place the version lives; the UI reads it as __APP_VERSION__.
const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as { version: string }

// ~60% of the entry chunk was framework code. Splitting it into its own chunks keeps every chunk
// under 500 KB and lets the browser keep React cached across deploys — app code changes far more
// often than the framework does.
const vendor = (pkgs: string) => new RegExp(`[\\\\/]node_modules[\\\\/](${pkgs})[\\\\/]`)

const apiProxy = {
  target: `http://127.0.0.1:${process.env.NC_API_PORT ?? 8787}`,
  rewrite: (p: string) => p.replace(/^\/api/, ''),
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(version) },
  resolve: {
    alias: { '@': new URL('./src', import.meta.url).pathname },
  },
  // /api/* is the local FastAPI server (server/). The app always talks to it there, so the dev server and
  // `vite preview` — which the smoke and layout checks run against a production build — proxy it the same way.
  server: { proxy: { '/api': apiProxy } },
  preview: { proxy: { '/api': apiProxy } },
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
