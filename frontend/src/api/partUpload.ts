// 把一份文件按段直接放进存储：资料库（项目的、团队的）和题目的材料走的是同一条路
// （后端 `app/domain/library/records.py` 的 `upload_slot` / `upload_part` / `received`）。
//
// 字节不经过后端：先领一次上传的凭据，再按它说的段长一段一段读出来，每段算出 MD5、领一个
// 只收这一段的地址，原样 PUT 过去——对象存储的预签名地址，或者文件存在服务器本地盘时后端
// 自己的一个地址（以 `/` 开头）。存储收每一段时自己对 MD5（`Content-MD5`）；整份的
// SHA-256 在读的同一遍里算出来，几 GB 的文件也只读一遍，不整份进内存。
import { createSHA256, md5 } from 'hash-wasm'

import { t } from '../i18n'

import { BASE, request } from './http'

/** 领到的一次上传：凭据，和文件怎么切成段（最后一段可以短）。 */
interface UploadSlot {
  upload: string
  part_bytes: number
  parts: number
}

/** 登记一次传完的上传要交回的：凭据，整份的 SHA-256 和每一段的 MD5（十六进制）。 */
export interface Uploaded {
  upload: string
  sha256: string
  parts: string[]
}

/** 同时在路上的段数：每一段都整段放在内存里（几十 MB），几段并行就够把带宽用满。 */
const PARALLEL_PARTS = 3
/** 一段传失败了再试几次：几 GB 的文件切成几百段，一段碰上网络抖动不该让整份重来。
 *  只重试网络错误和服务器那边的 5xx；4xx 是这一段本身不被收（过期、哈希对不上）。 */
const PART_ATTEMPTS = 3

async function putPart(
  base: string,
  upload: string,
  part: number,
  digest: string,
  bytes: Uint8Array<ArrayBuffer>
): Promise<void> {
  const address = await request<{ url: string; headers: Record<string, string> }>(`${base}/uploads/part`, {
    method: 'POST',
    body: JSON.stringify({ upload, part, md5: digest }),
  })
  const url = address.url.startsWith('/') ? `${BASE}${address.url}` : address.url
  for (let attempt = 1; ; attempt++) {
    let status = 0
    try {
      const put = await fetch(url, { method: 'PUT', body: bytes, headers: address.headers })
      if (put.ok) return
      status = put.status
    } catch (error) {
      if (attempt >= PART_ATTEMPTS) throw error
      continue
    }
    if (status < 500 || attempt >= PART_ATTEMPTS) throw new Error(t('files.library.uploadFailed', { status }))
  }
}

/** 传了多少：落进存储的那几段的字节数，和整份多大。 */
export type UploadProgress = (sent: number, total: number) => void

/** 一份文件传到了百分之几（向下取整，传完才是 100）。 */
export function uploadPercent(sent: number, total: number): number {
  return total > 0 ? Math.floor((sent / total) * 100) : 100
}

/** 把 `file` 传进 `base`（`/projects/<id>/library`、`/teams/<id>/library`、
 *  `/tasks/<id>/files`）领到的一次上传，交回登记要用的凭据和哈希。每落进存储
 *  一段就报一次 `progress`：几 GB 的文件要传很久，页面靠它写传到了多少。 */
export async function sendInParts(base: string, file: File, progress?: UploadProgress): Promise<Uploaded> {
  const slot = await request<UploadSlot>(`${base}/uploads`, {
    method: 'POST',
    body: JSON.stringify({ name: file.name, size: file.size }),
  })
  const whole = await createSHA256()
  const parts: string[] = []
  const inFlight = new Set<Promise<void>>()
  let failure: unknown = null
  let sent = 0
  progress?.(0, file.size)
  for (let part = 1; part <= slot.parts && failure === null; part++) {
    const start = (part - 1) * slot.part_bytes
    const bytes = new Uint8Array(await file.slice(start, start + slot.part_bytes).arrayBuffer())
    whole.update(bytes)
    const digest = await md5(bytes)
    parts.push(digest)
    const sending: Promise<void> = putPart(base, slot.upload, part, digest, bytes)
      .then(() => {
        sent += bytes.byteLength
        progress?.(sent, file.size)
      })
      .catch((error: unknown) => {
        failure ??= error
      })
      .finally(() => inFlight.delete(sending))
    inFlight.add(sending)
    if (inFlight.size >= PARALLEL_PARTS) await Promise.race(inFlight)
  }
  await Promise.all(inFlight)
  if (failure !== null) throw failure
  return { upload: slot.upload, sha256: whole.digest('hex'), parts }
}
