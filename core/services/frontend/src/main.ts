import './theme' // applies the persisted dark/light choice before first paint — import first

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './assets/style.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)

// Wait for the router's initial navigation (including its async beforeEach guard)
// to fully resolve before mounting. Without this, a brand-new tab opened via the
// _session URL-fragment handoff (see router/index.ts and api/client.ts::terminalUrl)
// races: App.vue's own onMounted also calls auth.init(), and if THAT fires first —
// before the guard has set the handed-off token — its unauthenticated /auth/session
// request fails and clearToken()s the token the guard sets moments later, bouncing
// the new tab to the login screen instead of picking up the existing session.
router.isReady().then(() => {
  app.mount('#app')
})

// Chrome/Edge steal the mouse wheel on a focused <input type="number"> to
// step its value instead of scrolling the page — feels like "scroll is
// broken" on any settings page with number fields stacked above more
// content (e.g. Platform Settings' rate-limit inputs). Blurring on wheel
// hands the same gesture back to the page's normal scroll.
document.addEventListener('wheel', (e) => {
  const el = document.activeElement as HTMLElement | null
  if (el && el.tagName === 'INPUT' && (el as HTMLInputElement).type === 'number' && el === e.target) {
    el.blur()
  }
}, { passive: true })

// A tab that's been open since before a deploy still holds content-hashed chunk
// URLs (e.g. LoginView-<hash>.js) from the OLD build — files that no longer exist
// once the new build replaces dist/. Every SeyalRun page except the server-rendered
// SSH Hosts page needs a lazy-loaded route chunk, so a stale tab silently fails to
// render anything past the login screen. Vite fires this event precisely when a
// dynamic import() 404s for that reason; reloading picks up the current build's
// index.html (which references the current chunk hashes) instead of leaving the
// user stuck with no explanation.
window.addEventListener('vite:preloadError', () => {
  window.location.reload()
})
