// 从一个 zip（.docx、.xlsx、.pptx 都是）里取出一个文件。
//
// 只读一个条目，所以不引一整个 zip 库：按中央目录找到那一条，存储的直接切出来，压缩的
// 交给浏览器自带的 `DecompressionStream('deflate-raw')`。zip64、加密、分卷这些 Office
// 文件用不到的情形一律当「取不到」（返回 null），由调用方说「这一份对比不了」。

const END_OF_CENTRAL_DIR = 0x06054b50
const CENTRAL_ENTRY = 0x02014b50
const LOCAL_HEADER = 0x04034b50

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

/** `name` 那一条的内容；没有这一条、或者这一份不是读得懂的 zip，给 null。 */
export async function readZipEntry(bytes: ArrayBuffer, name: string): Promise<Uint8Array | null> {
  const view = new DataView(bytes)
  if (view.byteLength < 22) return null
  const end = findEnd(view)
  if (end < 0) return null
  const count = view.getUint16(end + 10, true)
  let at = view.getUint32(end + 16, true)
  const decoder = new TextDecoder()
  for (let i = 0; i < count; i += 1) {
    if (at + 46 > view.byteLength || view.getUint32(at, true) !== CENTRAL_ENTRY) return null
    const method = view.getUint16(at + 10, true)
    const compressed = view.getUint32(at + 20, true)
    const nameLength = view.getUint16(at + 28, true)
    const extraLength = view.getUint16(at + 30, true)
    const commentLength = view.getUint16(at + 32, true)
    const local = view.getUint32(at + 42, true)
    const entryName = decoder.decode(new Uint8Array(bytes, at + 46, nameLength))
    at += 46 + nameLength + extraLength + commentLength
    if (entryName !== name) continue
    if (local + 30 > view.byteLength || view.getUint32(local, true) !== LOCAL_HEADER) return null
    const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true)
    if (start + compressed > view.byteLength) return null
    const data = new Uint8Array(bytes, start, compressed)
    if (method === 0) return data.slice()
    if (method === 8) return inflateRaw(data)
    return null
  }
  return null
}
