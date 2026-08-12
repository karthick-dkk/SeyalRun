import { ref, onUnmounted } from 'vue'
import { useAuthStore } from '@/stores/auth'

export type StreamEvent =
  | { type: 'line'; line: string }
  | { type: 'done'; status: string; exit_code: number }
  | { type: 'error'; line: string }
  | { type: 'ping' }

export function useJobStream(jobId: string) {
  const lines = ref<string[]>([])
  const done = ref(false)
  const status = ref<string | null>(null)
  const exitCode = ref<number | null>(null)

  let ws: WebSocket | null = null

  function connect() {
    const auth = useAuthStore()
    const wsUrl = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/jobs/${jobId}/stream?token=${auth.token}`

    ws = new WebSocket(wsUrl)

    ws.onmessage = (e) => {
      try {
        const ev: StreamEvent = JSON.parse(e.data)
        if (ev.type === 'line') {
          lines.value.push(ev.line)
        } else if (ev.type === 'done') {
          done.value = true
          status.value = ev.status
          exitCode.value = ev.exit_code
          ws?.close()
        } else if (ev.type === 'error') {
          lines.value.push(`[ERROR] ${ev.line}`)
        }
      } catch {
        lines.value.push(e.data)
      }
    }

    ws.onerror = () => {
      lines.value.push('[WebSocket error — connection closed]')
      done.value = true
    }
  }

  function disconnect() {
    ws?.close()
  }

  onUnmounted(disconnect)

  return { lines, done, status, exitCode, connect, disconnect }
}
