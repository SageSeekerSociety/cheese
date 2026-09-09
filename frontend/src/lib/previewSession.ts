import type { PreviewSession } from '../api'

export function postPreviewSession(session: PreviewSession, options: { target?: string; path?: string } = {}) {
  const destination = new URL(session.url)
  if (destination.origin === window.location.origin || !['http:', 'https:'].includes(destination.protocol)) {
    throw new Error('预览地址未与平台隔离')
  }
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = destination.href
  if (options.target) form.target = options.target
  for (const [name, value] of Object.entries({ grant: session.grant, path: options.path })) {
    if (value === undefined) continue
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = name
    input.value = value
    form.append(input)
  }
  document.body.append(form)
  try {
    // A form navigation sets the cookie in the target's storage partition.
    form.submit()
  } finally {
    form.remove()
  }
}
