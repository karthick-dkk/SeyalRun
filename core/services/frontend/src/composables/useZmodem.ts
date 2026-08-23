/**
 * ZMODEM transport for the terminal — rz/sz over the existing session.
 *
 * The server decides whether a transfer may happen at all: it reads the opening
 * frame's direction (ZRQINIT = the host wants to send, ZRINIT = the host is ready
 * to receive) and checks the SAME per-host `download` / `upload` grants the Files
 * panel uses. If it refuses, it cancels the transfer and writes a notice into the
 * stream, and nothing here ever sees a session start. This module is therefore
 * the transport only — it deliberately holds no policy, because a client-side
 * permission check is a UI hint, not a control.
 *
 * The library is driven from the raw byte stream, so it must sit between the
 * WebSocket and xterm: once a ZMODEM session begins, those bytes are protocol,
 * not text, and writing them to the terminal would both corrupt the transfer and
 * spray binary at the user.
 */
import * as ZmodemLib from 'zmodem.js/src/zmodem_browser'

export interface ZmodemHooks {
  /** Write ordinary terminal output through to xterm. */
  toTerminal: (data: string) => void
  /** Send raw bytes back to the host (the session's input channel). */
  toHost: (bytes: Uint8Array) => void
  /** Progress/status for the UI; message is already human-readable. */
  onStatus: (message: string) => void
  /** Ask the user for a file to upload. Resolves to null if they cancel. */
  pickFiles: () => Promise<File[] | null>
}

export function useZmodem(hooks: ZmodemHooks) {
  const sentry = new ZmodemLib.Sentry({
    to_terminal: (octets: ArrayLike<number>) => {
      hooks.toTerminal(String.fromCharCode(...Array.from(octets)))
    },
    sender: (octets: ArrayLike<number>) => hooks.toHost(Uint8Array.from(Array.from(octets))),
    on_retract: () => hooks.onStatus(''),
    on_detect: (detection: any) => {
      const session = detection.confirm()
      if (session.type === 'send') {
        void handleUpload(session)
      } else {
        void handleDownload(session)
      }
    },
  })

  /** Host is ready to receive (`rz`) — the browser sends a locally chosen file.
   *  Same shape as the Files panel: the bytes come from the user's own file
   *  picker, never from a path or URL the page composed. */
  async function handleUpload(session: any) {
    const files = await hooks.pickFiles()
    if (!files || !files.length) {
      hooks.onStatus('Upload cancelled')
      session.close()
      return
    }
    try {
      await ZmodemLib.Browser.send_files(session, files, {
        on_offer_response: (_f: File, xfer: any) => {
          if (xfer) hooks.onStatus(`Uploading ${xfer.get_details().name}…`)
        },
        on_progress: (_f: File, xfer: any) => {
          const d = xfer.get_details()
          hooks.onStatus(`Uploading ${d.name} — ${fmt(xfer.get_offset())} / ${fmt(d.size)}`)
        },
      })
      await session.close()
      hooks.onStatus('Upload complete')
    } catch (e: any) {
      hooks.onStatus(`Upload failed: ${e?.message || e}`)
    }
  }

  /** Host is sending (`sz`) — the browser saves the file.
   *
   *  The payload chunks have to be accumulated as they arrive: accept() resolves
   *  when the transfer completes, and by then the individual 'input' events are
   *  gone. Handing save_to_disk an empty array (my first version) produces a
   *  0-byte file and a transfer that looks like it worked. */
  async function handleDownload(session: any) {
    session.on('offer', (xfer: any) => {
      const d = xfer.get_details()
      const chunks: Uint8Array[] = []
      hooks.onStatus(`Downloading ${d.name}…`)
      xfer.on('input', (payload: ArrayLike<number>) => {
        chunks.push(new Uint8Array(Array.from(payload)))
        hooks.onStatus(`Downloading ${d.name} — ${fmt(xfer.get_offset())} / ${fmt(d.size)}`)
      })
      xfer.accept()
        .then(() => {
          ZmodemLib.Browser.save_to_disk(chunks, d.name)
          hooks.onStatus(`Downloaded ${d.name} (${fmt(d.size)})`)
        })
        .catch((e: any) => hooks.onStatus(`Download failed: ${e?.message || e}`))
    })
    session.start()
  }

  /** Feed every inbound chunk here instead of straight to xterm. */
  function consume(bytes: Uint8Array) {
    sentry.consume(bytes)
  }

  return { consume }
}

function fmt(n: number): string {
  if (n < 1024) return `${n} B`
  const u = ['KB', 'MB', 'GB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${u[i]}`
}
