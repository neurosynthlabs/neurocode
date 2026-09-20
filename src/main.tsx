import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import { installServiceWorker } from './lib/pwa'
import './index.css'

// A tab that outlived a deploy asks for chunk hashes that no longer exist. Reload once to pick up
// the new build; the timestamp stops a reload loop when a chunk is genuinely unreachable, and the
// route error boundary takes over from there.
window.addEventListener('vite:preloadError', (event) => {
  try {
    const last = Number(sessionStorage.getItem('nc.chunkReload') ?? 0)
    if (Date.now() - last < 10_000) return
    sessionStorage.setItem('nc.chunkReload', String(Date.now()))
  } catch {
    return
  }
  event.preventDefault()
  window.location.reload()
})

// Installable on a phone: the shell is kept so the app opens without a network, and nothing of the
// workspace ever is. It decides for itself whether it belongs on this page — it does not, in
// development or inside the desktop app — and says why in the console either way.
void installServiceWorker().then(({ act, why }) => {
  if (act !== 'nothing') console.info(`NeuroCode: ${why}`)
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
