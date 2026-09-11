import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
