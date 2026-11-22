// 测试里用的最小 zip：每一条按给的方式存（0 原样，8 deflate），带中央目录。不校验 CRC
// （`zipEntry.ts` 也不看它）。和 `docxText.spec.ts` 里那一份同一个写法，抽出来给别的测试用。
async function deflate(raw: Uint8Array<ArrayBuffer>): Promise<Uint8Array> {
  const source = new ReadableStream<BufferSource>({
    start(controller) {
      controller.enqueue(raw)
      controller.close()
    },
  })
  const reader = source.pipeThrough(new CompressionStream('deflate-raw')).getReader()
  const parts: number[] = []
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(...value)
  }
  return new Uint8Array(parts)
}

export interface ZipFixtureEntry {
  name: string
  /** 文本或原始字节。 */
  data: string | Uint8Array<ArrayBuffer>
  method?: 0 | 8
}

export async function zipFixture(entries: ZipFixtureEntry[]): Promise<ArrayBuffer> {
  const enc = new TextEncoder()
  const locals: Uint8Array[] = []
  const centrals: Uint8Array[] = []
  let offset = 0
  for (const entry of entries) {
    const method = entry.method ?? 0
    const name = enc.encode(entry.name)
    const raw = typeof entry.data === 'string' ? enc.encode(entry.data) : entry.data
    const data = method === 8 ? await deflate(raw) : raw
    const local = new Uint8Array(30 + name.length + data.length)
    const lv = new DataView(local.buffer)
    lv.setUint32(0, 0x04034b50, true)
    lv.setUint16(8, method, true)
    lv.setUint32(18, data.length, true)
    lv.setUint32(22, raw.length, true)
    lv.setUint16(26, name.length, true)
    local.set(name, 30)
    local.set(data, 30 + name.length)
    const central = new Uint8Array(46 + name.length)
    const cv = new DataView(central.buffer)
    cv.setUint32(0, 0x02014b50, true)
    cv.setUint16(10, method, true)
    cv.setUint32(20, data.length, true)
    cv.setUint32(24, raw.length, true)
    cv.setUint16(28, name.length, true)
    cv.setUint32(42, offset, true)
    central.set(name, 46)
    locals.push(local)
    centrals.push(central)
    offset += local.length
  }
  const centralSize = centrals.reduce((n, c) => n + c.length, 0)
  const end = new Uint8Array(22)
  const ev = new DataView(end.buffer)
  ev.setUint32(0, 0x06054b50, true)
  ev.setUint16(8, entries.length, true)
  ev.setUint16(10, entries.length, true)
  ev.setUint32(12, centralSize, true)
  ev.setUint32(16, offset, true)
  const out = new Uint8Array(offset + centralSize + 22)
  let at = 0
  for (const part of [...locals, ...centrals, end]) {
    out.set(part, at)
    at += part.length
  }
  return out.buffer as ArrayBuffer
}
