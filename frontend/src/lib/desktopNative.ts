// In the desktop app this page is the window of an app, and its frame should
// behave like one: the rail, the sidebars and the bars are not text to select,
// a right-click on them opens no browser menu (Look Up, Open Link, Download
// Image), their links and pictures are not dragged out of the window, and a
// file dragged in from outside lands only where the page takes files.
// What a person reads or writes stays as it is in a browser: what they type,
// messages, documents, code and file previews keep their selection and the
// system's menu with Copy on it. In a browser none of this runs.
//
// The page decides this, not the app (desktop/): the list below names this
// frontend's own surfaces and changes with them, and the page is current as
// soon as it ships, while an installed app waits for its next update.

import { inDesktopApp } from './desktopApp'

/** Where text stays selectable and a right-click opens the system's menu. */
const SELECTABLE = [
  // What a person types.
  'input',
  'textarea',
  '[contenteditable]:not([contenteditable="false"])',
  // Documents and rich text, editable or not.
  '.ProseMirror',
  '.monaco-editor',
  // What a person reads.
  'pre',
  'code',
  '.md-content',
  '.card-markdown',
  '.markdown-body',
  '.rich-content',
  '.legal-body',
  '.im-text',
  '.card-msg__text',
  '.msg-select',
  '.chat-response',
  '.reasoning-text',
  // Step checklists: a teammate's in the chat, a room's and a card's progress.
  '.checklist',
  '.todo-checklist',
  // File previews: the text layers of a PDF and of slides, and a sheet's cells.
  '.pv-text',
  '.slide-text-layer',
  '.ps__cell',
]

// Under `:root[data-desktop-app]`, which only this module sets.
const RULES = `
:root[data-desktop-app] body { -webkit-user-select: none; user-select: none; }
${SELECTABLE.map((s) => `:root[data-desktop-app] ${s}`).join(',\n')} { -webkit-user-select: text; user-select: text; }
`

// What a drag may pick up: content, and whatever the page itself made
// draggable, such as a project tile in the rail dragged to reorder.
const DRAGGABLE = [...SELECTABLE, '[draggable="true"]'].join(', ')
const KEEPS_MENU = SELECTABLE.join(', ')

// A file dragged in from Finder and let go anywhere the page does not take
// files: with the app's own drag-and-drop handler off (desktop/src-tauri,
// main.rs), WebKit's default for that drop is to open the file in place of the
// page, and an app window has no Back to return from it. A drop zone that takes
// files cancels the event itself (the composer, the library, the welcome page;
// the doc editor cancels its dragover), so whatever reaches the document still
// uncancelled had no taker and is refused here.
function carriesFiles(event: DragEvent): boolean {
  return !!event.dataTransfer?.types.includes('Files')
}

function refuseStrayFileDrops(): void {
  document.addEventListener('dragover', (event) => {
    if (event.defaultPrevented || !carriesFiles(event)) return
    event.preventDefault()
    // The cursor says the file will not go here, rather than a copy badge.
    event.dataTransfer!.dropEffect = 'none'
  })
  document.addEventListener('drop', (event) => {
    if (!event.defaultPrevented && carriesFiles(event)) event.preventDefault()
  })
}

function within(target: EventTarget | null, selector: string): boolean {
  const element = target instanceof Element ? target : target instanceof Node ? target.parentElement : null
  return !!element?.closest(selector)
}

/** Makes the frame behave as an app's in the desktop app; does nothing in a browser. */
export function behaveAsDesktopApp(): void {
  if (!inDesktopApp()) return
  document.documentElement.dataset.desktopApp = ''
  const style = document.createElement('style')
  style.textContent = RULES
  document.head.append(style)
  document.addEventListener('dragstart', (event) => {
    if (!within(event.target, DRAGGABLE)) event.preventDefault()
  })
  refuseStrayFileDrops()
  // A development build (`pnpm tauri dev` against the dev server) keeps the
  // menu everywhere, since Inspect Element is on it.
  if (import.meta.env.DEV) return
  document.addEventListener('contextmenu', (event) => {
    if (!within(event.target, KEEPS_MENU)) event.preventDefault()
  })
}
