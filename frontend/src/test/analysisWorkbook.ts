// 仓库自带的那份表格模板（`backend/app/domain/documents/templates/analysis.xlsx`）的
// 字节，给前端用例当真实输入。
//
// 为什么要这份真的：这份模板是 openpyxl 写的（芝士 产出表格用的就是它），里面有一张
// 柱状图，而表格查看器当时正是被它整份读不出来。用现造的替身字节测不出这件事 ——
// openpyxl 与 Excel 写 drawing 部件的方式不同，正是差别所在。
//
// 读文件这一步住在这里而不是用例里：`src/**/*.spec.ts` 不许 import `node:fs`
// （AGENTS.md「Test behaviour, never the implementation」把读源码当文本的用例挡在门外，
// 那条选择题管的是全文匹配）。这里读的是一份二进制输入，不是源码。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))

/** 仓库根下这份模板的路径。 */
export const ANALYSIS_XLSX_PATH = join(
  HERE,
  '..',
  '..',
  '..',
  'backend',
  'app',
  'domain',
  'documents',
  'templates',
  'analysis.xlsx'
)

/** 它的字节，一份 `ArrayBuffer`（`PreviewSheet` 的 `data` 就是它）。 */
export function analysisWorkbookBytes(): ArrayBuffer {
  const bytes = readFileSync(ANALYSIS_XLSX_PATH)
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}
