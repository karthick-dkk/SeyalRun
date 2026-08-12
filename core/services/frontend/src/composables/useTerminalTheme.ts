/**
 * Terminal colour themes and the user's persisted choice.
 *
 * First extraction of the TerminalView split (terminal-parity-plan.md,
 * Increment 1a). TerminalView.vue is 1,293 lines mixing pane management,
 * session lifecycle, capture, hosts and settings; SFTP, session sharing and
 * watermarking will not fit into it as it stands. The split has to happen
 * before those land, not after, and it lands as a sequence of
 * no-behaviour-change commits so each one is verifiable on its own.
 *
 * This piece first because it is genuinely self-contained: static palette data
 * plus a localStorage-backed selection, with no dependency on session or pane
 * state. The only coupling in the original was that setTheme() also closed the
 * open menu, which is a caller concern — the caller now does it.
 */
import { computed, ref } from 'vue'

export type TerminalTheme = Record<string, string>

export const THEMES: Record<string, TerminalTheme> = {
  'SeyalRun Dark': {
    background: '#1a1b1e', foreground: '#dce1e7', cursor: '#dce1e7', cursorAccent: '#1a1b1e', selectionBackground: '#3a4a5a',
    black: '#1c1e26', red: '#ff5c57', green: '#5af78e', yellow: '#f3f99d', blue: '#57c7ff', magenta: '#ff6ac1', cyan: '#9aedfe', white: '#f1f1f0',
    brightBlack: '#686868', brightRed: '#ff6e6e', brightGreen: '#69ff94', brightYellow: '#ffffa5', brightBlue: '#d6acff', brightMagenta: '#ff92df', brightCyan: '#a4ffff', brightWhite: '#ffffff',
  },
  'Light': {
    background: '#fafafa', foreground: '#24292f', cursor: '#24292f', cursorAccent: '#fafafa', selectionBackground: '#b9d6f0',
    black: '#24292f', red: '#cf222e', green: '#116329', yellow: '#7d4e00', blue: '#0969da', magenta: '#8250df', cyan: '#1b7c83', white: '#6e7781',
    brightBlack: '#57606a', brightRed: '#a40e26', brightGreen: '#1a7f37', brightYellow: '#633c01', brightBlue: '#218bff', brightMagenta: '#a475f9', brightCyan: '#3192aa', brightWhite: '#24292f',
  },
  'Solarized Dark': {
    background: '#002b36', foreground: '#93a1a1', cursor: '#93a1a1', cursorAccent: '#002b36', selectionBackground: '#073642',
    black: '#073642', red: '#dc322f', green: '#859900', yellow: '#b58900', blue: '#268bd2', magenta: '#d33682', cyan: '#2aa198', white: '#eee8d5',
    brightBlack: '#586e75', brightRed: '#cb4b16', brightGreen: '#586e75', brightYellow: '#657b83', brightBlue: '#839496', brightMagenta: '#6c71c4', brightCyan: '#93a1a1', brightWhite: '#fdf6e3',
  },
  'Dracula': {
    background: '#282a36', foreground: '#f8f8f2', cursor: '#f8f8f2', cursorAccent: '#282a36', selectionBackground: '#44475a',
    black: '#21222c', red: '#ff5555', green: '#50fa7b', yellow: '#f1fa8c', blue: '#bd93f9', magenta: '#ff79c6', cyan: '#8be9fd', white: '#f8f8f2',
    brightBlack: '#6272a4', brightRed: '#ff6e6e', brightGreen: '#69ff94', brightYellow: '#ffffa5', brightBlue: '#d6acff', brightMagenta: '#ff92df', brightCyan: '#a4ffff', brightWhite: '#ffffff',
  },
}

/** Same key the original used, so an existing user's choice survives the split. */
const STORAGE_KEY = 'sr_term_theme'
const DEFAULT_THEME = 'SeyalRun Dark'

export function useTerminalTheme() {
  const themeNames = Object.keys(THEMES)
  const themeName = ref(localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME)
  // Falls back rather than returning undefined: a theme removed from the map
  // (or a hand-edited localStorage value) must not blank the terminal.
  const currentTheme = computed(() => THEMES[themeName.value] || THEMES[DEFAULT_THEME])

  function setTheme(name: string) {
    if (!THEMES[name]) return
    themeName.value = name
    localStorage.setItem(STORAGE_KEY, name)
  }

  return { themeNames, themeName, currentTheme, setTheme }
}
