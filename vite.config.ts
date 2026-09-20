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

/* Reaching the dev server from a phone on the same Wi-Fi. Off unless NC_LAN says otherwise, and the
   same switch the API reads (NEUROCODE_LISTEN_ON_LAN), because a web app on the LAN with its API
   still bound to 127.0.0.1 is a screen that can never load anything. `scripts/dev.sh` sets both.

   `host: true` means every interface, which is what a phone needs and also what anyone else on that
   network gets: this is a deliberate switch, not a default, and docs/ARCHITECTURE.md says what it
   exposes. `allowedHosts` lets Bonjour names through as well as the raw address, since that is what
   a Mac advertises itself as. */
const lan = ['1', 'true', 'yes'].includes((process.env.NC_LAN ?? '').toLowerCase())

const apiProxy = {
  target: `http://127.0.0.1:${process.env.NC_API_PORT ?? 8787}`,
  rewrite: (p: string) => p.replace(/^\/api/, ''),
  // The Workbench's terminal and debugger talk over WebSockets under /api too; without this the upgrade is
  // dropped here and the socket never opens.
  ws: true,
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(version) },
  resolve: {
    alias: { '@': new URL('./src', import.meta.url).pathname },
  },
  // /api/* is the local FastAPI server (server/). The app always talks to it there, so the dev server and
  // `vite preview` — which the smoke and layout checks run against a production build — proxy it the same way.
  server: { host: lan || undefined, allowedHosts: lan ? ['.local'] : undefined, proxy: { '/api': apiProxy } },
  preview: { host: lan || undefined, allowedHosts: lan ? ['.local'] : undefined, proxy: { '/api': apiProxy } },
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
