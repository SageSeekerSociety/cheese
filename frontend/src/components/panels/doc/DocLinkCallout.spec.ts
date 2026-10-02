import { createApp, h, nextTick, shallowRef } from 'vue'
import { createVuetify } from 'vuetify'
import { Editor } from '@tiptap/core'
import { afterEach, expect, it, vi } from 'vitest'

import { captureDocLink } from '../../../lib/docLinks'
import { docExtensions } from '../../../lib/docSchema'

import DocLinkCallout from './DocLinkCallout.vue'

vi.mock('@/i18n', () => ({ t: (key: string) => key }))
const cleanups: (() => void)[] = []
afterEach(() => cleanups.splice(0).forEach((cleanup) => cleanup()))
function setup() {
  const mount = document.createElement('div')
  mount.className = 'doc-editor-wrap'
  document.body.append(mount)
  const editorElement = document.createElement('div')
  mount.append(editorElement)
  const editor = new Editor({
    element: editorElement,
    extensions: docExtensions(),
    content: '[link](https://example.com)',
    contentType: 'markdown',
  })
  const target = shallowRef(captureDocLink(editor, 1)!)
  const overlay = document.createElement('div')
  mount.append(overlay)
  const close = vi.fn(() => {
    target.value = null as never
  })
  const app = createApp({
    render: () => (target.value ? h(DocLinkCallout, { target: target.value, onClose: close }) : null),
  })
  app.use(createVuetify())
  app.mount(overlay)
  cleanups.push(() => {
    app.unmount()
    editor.destroy()
    mount.remove()
  })
  const button = (key: string) =>
    Array.from(mount.querySelectorAll('button')).find((el) => el.textContent?.trim() === `work.room.docLink.${key}`)!
  return { editor, mount, close, button }
}
it('returns Escape from URL input to details, then to the editor', async () => {
  const { editor, mount, button, close } = setup()
  button('edit').click()
  await nextTick()
  const input = mount.querySelector('input')!
  expect(document.activeElement).toBe(input)
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  await nextTick()
  expect(document.activeElement).toBe(button('edit'))
  button('edit').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  await nextTick()
  expect(close).toHaveBeenCalledOnce()
  expect(document.activeElement).toBe(editor.view.dom)
})
it('does not steal external focus when closed programmatically', async () => {
  const { mount, button } = setup()
  const outside = document.createElement('input')
  document.body.append(outside)
  cleanups.push(() => outside.remove())
  outside.focus()
  button('close')?.click()
  // Close is icon-labelled, so use the first toolbar button.
  mount.querySelector<HTMLButtonElement>('[role="toolbar"] button')?.click()
  await nextTick()
  expect(document.activeElement).toBe(outside)
})
it('save restores editor focus and modifies the actual link mark', async () => {
  const { editor, mount, button } = setup()
  button('edit').click()
  await nextTick()
  const input = mount.querySelector('input')!
  input.value = 'https://example.org/new'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await nextTick()
  expect(captureDocLink(editor, 1)?.href).toBe('https://example.org/new')
  expect(document.activeElement).toBe(editor.view.dom)
})
