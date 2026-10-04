// POST a multipart body over XHR, reporting how much of it has gone up.
//
// XHR, not `fetch`: a multipart body needs the browser to set its own boundary,
// and only XHR reports request-body progress at all — `fetch` has no
// upload-progress event, so a `fetch` upload can only ever show a spinner that
// cannot say whether a 9 MB file is 5% or 95% of the way up.
//
// It resolves with the status and the parsed JSON (or `null` when the body is
// not JSON) rather than throwing, so the caller keeps its own wording for a
// refusal. A transport-level failure — no response at all — resolves as status
// 0, which is the shape a caller already handles.
//
// It takes its headers rather than reaching for the token itself: this is pure
// transport, usable wherever a URL and a form are (the composer's upload is the
// one caller today).
export function postFormWithProgress<T>(
  url: string,
  form: FormData,
  headers: Record<string, string>,
  onProgress?: (fraction: number) => void
): Promise<{ status: number; body: T | null }> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value)
    xhr.upload?.addEventListener('progress', (e) => {
      // `lengthComputable` is false when the server cannot give a total; a
      // fraction with no denominator would be measuring nothing.
      if (onProgress && e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total)
    })
    xhr.addEventListener('load', () => {
      let body: T | null = null
      try {
        body = JSON.parse(xhr.responseText) as T
      } catch {
        body = null
      }
      resolve({ status: xhr.status, body })
    })
    xhr.addEventListener('error', () => resolve({ status: 0, body: null }))
    xhr.send(form)
  })
}
