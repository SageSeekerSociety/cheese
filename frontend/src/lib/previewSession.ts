import type { PreviewSession } from '../api'

/** 内容域上「房间里的这份文件」的地址前缀（后端 `ROOM_FILES_PATH`）。
 *
 *  `/` 那个地址属于房间当前的 artifact；一份房间文件不挂在它下面，所以有自己的
 *  地址空间，和 `/_cheese/session` 同住 `/_cheese/`。 */
export const ROOM_FILE_PATH = '/_cheese/room/'

/** 把一份房间文件在内容域上的地址拼出来。
 *
 *  逐段转义：文件名可以是中文、带空格，而这段字符串要先在表单里过一遍、再变成
 *  303 的 Location 头，最后才被浏览器请求——留下的转义正好被解码回原来的名字。 */
export function roomFileDestination(path: string): string {
  return ROOM_FILE_PATH + path.split('/').map(encodeURIComponent).join('/')
}

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
