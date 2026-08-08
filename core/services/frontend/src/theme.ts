import { ref } from 'vue'

// Applied as document.documentElement's data-theme attribute — style.css's
// :root[data-theme="light"] block overrides the dark-theme CSS variables every
// component already reads colors through, so this is the only place theme
// switching needs to live. Imported at the very top of main.ts (before the app
// mounts) so the persisted choice applies before first paint — otherwise a
// returning light-theme user would see one dark frame flash first.
const STORAGE_KEY = 'seyalrun-theme'
type Theme = 'dark' | 'light'

function storedTheme(): Theme {
  return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
}

export const theme = ref<Theme>(storedTheme())

function applyTheme(t: Theme) {
  document.documentElement.setAttribute('data-theme', t)
}

export function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  localStorage.setItem(STORAGE_KEY, theme.value)
  applyTheme(theme.value)
}

applyTheme(theme.value)
