// 一份表格两版之间改了哪几格：同名的表逐格比，显示出来的字不同就算改过。
//
// 比的是读者看得见的那一份（`lib/sheetRead` 读出来、截到上限之内的样子），所以只改了
// 公式、算出来的数没变的那一格不算改过——读者在表上看不出差别，标出来反而要他去找。
// 整张表新加或删掉不按格数：那一张表在这一版里要么全是新的、要么根本不在了，「改了
// 300 格」说的不是那件事。
import type { Sheet } from './sheetRead'

import { columnName } from './sheetRead'

/** 改过的一格：在哪张表、哪一格，上一版那里写的是什么。 */
export interface ChangedCell {
  sheet: string
  address: string
  before: string
}

export interface SheetDiff {
  cells: ChangedCell[]
  /** 这一版新加的表、删掉的表（表名）。 */
  addedSheets: string[]
  removedSheets: string[]
}

export function diffSheets(before: readonly Sheet[], after: readonly Sheet[]): SheetDiff {
  const old = new Map(before.map((sheet) => [sheet.name, sheet]))
  const cells: ChangedCell[] = []
  const addedSheets: string[] = []
  for (const sheet of after) {
    const prior = old.get(sheet.name)
    if (!prior) {
      addedSheets.push(sheet.name)
      continue
    }
    const height = Math.max(prior.rows.length, sheet.rows.length)
    const width = Math.max(prior.width, sheet.width)
    for (let r = 0; r < height; r += 1) {
      for (let c = 0; c < width; c += 1) {
        const was = prior.rows[r]?.[c] ?? ''
        const now = sheet.rows[r]?.[c] ?? ''
        if (was !== now) cells.push({ sheet: sheet.name, address: `${columnName(c)}${r + 1}`, before: was })
      }
    }
  }
  const kept = new Set(after.map((sheet) => sheet.name))
  const removedSheets = before.filter((sheet) => !kept.has(sheet.name)).map((sheet) => sheet.name)
  return { cells, addedSheets, removedSheets }
}
