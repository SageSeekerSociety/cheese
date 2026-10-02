// How somebody else's caret looks in a living document: a line in their colour
// and their name above it.
//
// The colour is the one avatarColor() gives their handle — the same value in
// both themes, so the white name on it is deliberately a fixed white too (see
// UserAvatar.vue for the same reasoning). It is set here, on the element, and
// not in a stylesheet: the colour is per person.

export function renderCaret(user: Record<string, unknown>): HTMLElement {
  const color = String(user.color || '')
  const caret = document.createElement('span')
  caret.classList.add('collaboration-carets__caret')
  caret.style.borderColor = color
  const label = document.createElement('span')
  label.classList.add('collaboration-carets__label')
  label.style.backgroundColor = color
  label.style.color = '#fff'
  label.textContent = String(user.name || user.handle || '')
  caret.append(label)
  return caret
}
