import { reactive } from 'vue'

export interface ConfirmOpts {
  title?: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

interface ConfirmState extends Required<ConfirmOpts> {
  visible: boolean
  resolve: ((v: boolean) => void) | null
}

// Module-level singleton — all components share one dialog overlay
const state = reactive<ConfirmState>({
  visible: false,
  title: 'Confirm',
  message: '',
  confirmLabel: 'OK',
  cancelLabel: 'Cancel',
  danger: false,
  resolve: null,
})

export function useConfirm() {
  function confirm(message: string, opts?: ConfirmOpts): Promise<boolean> {
    return new Promise((resolve) => {
      state.visible = true
      state.message = message
      state.title = opts?.title ?? 'Confirm'
      state.confirmLabel = opts?.confirmLabel ?? 'OK'
      state.cancelLabel = opts?.cancelLabel ?? 'Cancel'
      state.danger = opts?.danger ?? false
      state.resolve = resolve
    })
  }

  function accept() {
    state.visible = false
    state.resolve?.(true)
    state.resolve = null
  }

  function reject() {
    state.visible = false
    state.resolve?.(false)
    state.resolve = null
  }

  return { confirm, accept, reject, state }
}
