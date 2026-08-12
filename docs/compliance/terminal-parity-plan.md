# Terminal: JumpServer parity plan

Decision (2026-08-12): build the missing JumpServer PAM capabilities into the
core terminal and restructure it to carry them.

This is sequenced by **control value first**, not by visibility. The ordering
below is the whole point of the document — the increments are independent, so
the order is the only real decision.

---

## What exists today

`terminal-service/app/ws/terminal.py` + `frontend/src/views/TerminalView.vue`
(1,293 lines) + `components/terminal/`:

panes and split, per-pane font and theme, reconnect, clipboard paste, screen
capture, search, session recording with a SHA-256 digest, idle timeout,
`session.create` / `session.end` audit, kiosk mode.

Backend: recording, idle, resize, recording upload. Nothing else.

## What is missing

| Capability | Present | Notes |
|---|---|---|
| SFTP browse / upload / download | **no** | grants already exist — see below |
| Session sharing (read-only join) | no | |
| Admin takeover / live termination of a joined session | partial | terminate exists, join does not |
| Watermarking | no | |
| zmodem (rz/sz) | no | |
| Command hints / completion | no | |
| RDP / VNC gateway | no | out of scope of "terminal", separate product surface |
| Database proxying | no | as above |

## Increment 0 — the phantom permission (do this first)

`AuthorizationsAdmin.vue:312` offers `['ssh', 'sftp', 'upload', 'download']`
as grantable actions. A grep across `identity-service` and `terminal-service`
finds **no implementation for sftp, upload or download**. The grant is stored,
audited, and enforces nothing.

On a system whose purpose is proving who could do what, that is worse than a
missing feature: an access review would show "user X may download from host Y"
and conclude file transfer is controlled, when no file transfer exists at all.
The quarantined `modules/jumpserver-legacy/` tree does not implement SFTP
either (only recording and resize), so there is nothing to port.

Either the actions become real (Increment 1) or they come out of the UI. They
must not stay as they are.

## Increment 1 — SFTP, gated by the grants that already exist

- `terminal-service`: SFTP subsystem over the existing asyncssh connection,
  reusing the same credential unwrap and the same `SessionTarget`; no second
  auth path.
- Authorization: every operation checks the existing `sftp` / `upload` /
  `download` actions through the same `za_authorization` gate SSH uses. A
  download with only `sftp` granted must be refused.
- Audit: one chained row per operation — `sftp.list`, `sftp.upload`,
  `sftp.download` — carrying path, byte count and `result`, so R-10 holds.
  File transfer is exactly the event an assessor looks for.
- Frontend: a file panel beside the terminal pane, not a separate page; it
  belongs to the session.

**Exit:** a grant of `sftp` without `download` demonstrably blocks a download,
and the attempt is in the chain.

## Increment 2 — supervision

Read-only session join by link, admin takeover, forced termination from within
the joined view. `session.join` / `session.takeover` audited with the joining
actor. This is the half of PAM that answers "who was watching".

## Increment 3 — deterrent and UX layer

Watermarking (user + session id + timestamp overlaid on the pane), zmodem,
keyboard shortcut map, per-session theme persistence, command hints.

## Restructure

`TerminalView.vue` is 1,293 lines and already mixes pane management, session
lifecycle, capture and settings. Increments 1–3 will not fit in it without it
becoming unmaintainable. Split before adding, not after:

- `useTerminalSession()` — connection, reconnect, idle, resize
- `useTerminalPanes()` — split/pane state only
- `TerminalFilePanel.vue` — SFTP (Increment 1)
- `TerminalShareBar.vue` — supervision (Increment 2)
- `TerminalView.vue` — layout and composition only

Do this as the first commit of Increment 1, with no behaviour change, so the
split is verifiable against the existing screenshots before any feature lands
on top of it.

## Ground rules carried from this release

- Every increment goes through the full loop: source deploy → test → images →
  publish → deploy staging **from the images** → test. Two release-only bugs
  this cycle were invisible to source builds.
- `npm run build` (~3s) before every deploy.
- Every new audit call site records a `result` (R-10), and file transfer is
  `critical=True` — a transfer that cannot be logged must not proceed.
