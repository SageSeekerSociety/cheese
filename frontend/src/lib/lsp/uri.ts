// How a file of a task's checkout is named to its language server, and back.
//
// The server knows a file by `file://<root>/<path>`, `root` being where the checkout
// sits on the machine (the socket's `ready` frame says). Each segment is
// percent-encoded; what the server sends back is decoded segment by segment, so
// its own choice of which characters to escape does not matter.

/** `file://` URI of an absolute path on the machine. */
export function fileUri(absolute: string): string {
  return `file://${absolute.split('/').map(encodeURIComponent).join('/')}`
}

/** The absolute path a `file://` URI names, or null for any other URI. */
export function pathOfUri(uri: string): string | null {
  const match = /^file:\/\/[^/]*(\/.*)$/i.exec(uri)
  if (!match) return null
  try {
    return match[1].split('/').map(decodeURIComponent).join('/')
  } catch {
    return null
  }
}

/** The root without a trailing slash (`/` stays `/`). */
function trimRoot(root: string): string {
  return root.length > 1 ? root.replace(/\/+$/, '') : root
}

/** The server's URI for a path relative to the checkout. */
export function checkoutUri(root: string, path: string): string {
  const base = trimRoot(root)
  return fileUri(`${base === '/' ? '' : base}/${path.replace(/^\/+/, '')}`)
}

/** The path relative to the checkout a server URI names, or null when it is outside it. */
export function checkoutPath(root: string, uri: string): string | null {
  const absolute = pathOfUri(uri)
  if (absolute === null) return null
  const base = trimRoot(root)
  const prefix = base === '/' ? '/' : `${base}/`
  if (!absolute.startsWith(prefix) || absolute.length === prefix.length) return null
  return absolute.slice(prefix.length)
}
