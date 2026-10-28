// 从一个 zip（.docx、.xlsx、.pptx 都是）里取出一个文件。
//
// 只读中央目录里点到的那几条，所以不引一整个 zip 库：存储的直接切出来，压缩的交给
// 浏览器自带的 `DecompressionStream('deflate-raw')`。zip64、加密、分卷这些 Office
// 文件用不到的情形一律当「取不到」（返回 null），由调用方说「这一份对比不了」。

const END_OF_CENTRAL_DIR = 0x06054b50
const CENTRAL_ENTRY = 0x02014b50
const LOCAL_HEADER = 0x04034b50

/** 中央目录里的一条：名字和它在文件里的位置。字节要的时候才解压 —— 一份工作簿里有
 *  几十个部件，只想看看有没有 `xl/charts/` 的那几条时，不该把它们全解出来。 */
interface Entry {
  name: string
  method: number
  compressed: number
  start: number
}

/** 中央目录尾记录在文件最后 22 字节起、往前至多再有 65535 字节的注释。 */
function findEnd(view: DataView): number {
  const last = view.byteLength - 22
  const first = Math.max(0, last - 0xffff)
  for (let at = last; at >= first; at -= 1) {
    if (view.getUint32(at, true) === END_OF_CENTRAL_DIR) return at
  }
  return -1
}

async function inflateRaw(data: Uint8Array<ArrayBuffer>): Promise<Uint8Array> {
  const source = new ReadableStream<BufferSource>({
    start(controller) {
      controller.enqueue(data)
      controller.close()
    },
  })
  const reader = source.pipeThrough(new DecompressionStream('deflate-raw')).getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    chunks.push(value)
    size += value.length
  }
  const out = new Uint8Array(size)
  let at = 0
  for (const chunk of chunks) {
    out.set(chunk, at)
    at += chunk.length
  }
  return out
}

/** 走一遍中央目录。读不懂这一份 zip 时给 null（而不是半个列表）。 */
function entries(bytes: ArrayBuffer): Entry[] | null {
  const view = new DataView(bytes)
  if (view.byteLength < 22) return null
  const end = findEnd(view)
  if (end < 0) return null
  const count = view.getUint16(end + 10, true)
  let at = view.getUint32(end + 16, true)
  const decoder = new TextDecoder()
  const out: Entry[] = []
  for (let i = 0; i < count; i += 1) {
    if (at + 46 > view.byteLength || view.getUint32(at, true) !== CENTRAL_ENTRY) return null
    const method = view.getUint16(at + 10, true)
    const compressed = view.getUint32(at + 20, true)
    const nameLength = view.getUint16(at + 28, true)
    const extraLength = view.getUint16(at + 30, true)
    const commentLength = view.getUint16(at + 32, true)
    const local = view.getUint32(at + 42, true)
    const name = decoder.decode(new Uint8Array(bytes, at + 46, nameLength))
    at += 46 + nameLength + extraLength + commentLength
    if (local + 30 > view.byteLength || view.getUint32(local, true) !== LOCAL_HEADER) return null
    const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true)
    if (start + compressed > view.byteLength) return null
    out.push({ name, method, compressed, start })
  }
  return out
}

/** 这一份 zip 里有哪些条目（名字原样，带目录）。不是读得懂的 zip 时给空数组。 */
export function listZipEntries(bytes: ArrayBuffer): string[] {
  return entries(bytes)?.map((entry) => entry.name) ?? []
}

/** `name` 那一条的内容；没有这一条、或者这一份不是读得懂的 zip，给 null。 */
export async function readZipEntry(bytes: ArrayBuffer, name: string): Promise<Uint8Array | null> {
  const found = entries(bytes)?.find((entry) => entry.name === name)
  if (!found) return null
  const data = new Uint8Array(bytes, found.start, found.compressed)
  if (found.method === 0) return data.slice()
  if (found.method === 8) return inflateRaw(data)
  return null
}

/** `name` 那一条按 UTF-8 读出来；没有就是 null。 */
export async function readZipText(bytes: ArrayBuffer, name: string): Promise<string | null> {
  const data = await readZipEntry(bytes, name)
  return data ? new TextDecoder().decode(data) : null
}
