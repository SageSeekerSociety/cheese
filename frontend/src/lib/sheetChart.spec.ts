// @vitest-environment jsdom
/**
 * 工作簿里带的图表：从 zip 里把定义读出来，再照它引用的格子取数。
 *
 * 仓库自带的 analysis.xlsx 里有一张柱状图（挂在第二张表「汇总」上，引用「数据」的
 * B1:C6）。这几条盯的是读者能不能看见那张图、以及图上的类别与数值是不是文件里真有的。
 */
import { expect, it } from 'vitest'

import { expandRef, readChartParts, resolveCharts } from './sheetChart'
import { readSheets } from './sheetRead'

import { analysisWorkbookBytes } from '@/test/analysisWorkbook'

it('从工作簿里读得出那张图：在哪张表、什么类型、几条序列', async () => {
  const parts = await readChartParts(analysisWorkbookBytes())

  expect(parts.raw).toHaveLength(1)
  const chart = parts.raw[0]!
  expect(chart.sheet).toBe('汇总')
  expect(chart.kind).toBe('bar')
  expect(chart.horizontal).toBe(false)
  expect(chart.title).toBe('各类别指标')
  expect(chart.series.map((s) => s.values)).toEqual(["'数据'!$B$2:$B$6", "'数据'!$C$2:$C$6"])
})

it('图引用的那几格被记下来，读表时留它们的原始数', async () => {
  const parts = await readChartParts(analysisWorkbookBytes())
  const wanted = parts.needed.get('数据')!
  expect(wanted.has('B2')).toBe(true)
  expect(wanted.has('C6')).toBe(true)

  const read = await readSheets(analysisWorkbookBytes(), 'workbook')
  const data = read.sheets.find((s) => s.name === '数据')!
  expect(data.numbers?.get('B2')).toBe(0)
})

it('取数：类别来自引用那一片，序列名也能从格子里取', async () => {
  const read = await readSheets(analysisWorkbookBytes(), 'workbook')
  const parts = await readChartParts(analysisWorkbookBytes())
  const bySheet = resolveCharts(parts.raw, read.sheets)

  const [chart] = bySheet.get('汇总')!
  expect(chart.categories).toEqual(['【类别1】', '【类别2】', '【类别3】', '【类别4】', '【类别5】'])
  expect(chart.series.map((s) => s.name)).toEqual(['指标一', '指标二'])
  expect(chart.series[0]!.texts).toHaveLength(5)
})

it('没有图的工作簿、或者根本不是 zip 的字节，读出来是空的，不抛', async () => {
  const notZip = new TextEncoder().encode('姓名,分数\n张三,90\n').buffer as ArrayBuffer
  const parts = await readChartParts(notZip)
  expect(parts.raw).toEqual([])
  expect(parts.needed.size).toBe(0)
})

it('格子引用展开成地址：单格、一片、带工作表名和 `$` 的都认', () => {
  expect(expandRef("'数据'!$A$2:$A$4", 100)).toEqual(['A2', 'A3', 'A4'])
  expect(expandRef('B1', 100)).toEqual(['B1'])
  expect(expandRef("'数据'!$B$2:$C$3", 100)).toEqual(['B2', 'C2', 'B3', 'C3'])
  // 上限挡住一片几十万格的引用：图只画开头那几格，读表不必为它多留。
  expect(expandRef('A1:A100', 6)).toHaveLength(6)
  // 整列、整行、跨工作簿：我们不认，返回空。
  expect(expandRef('A:A', 100)).toEqual([])
  expect(expandRef("[1]'数据'!A1", 100)).toEqual([])
})
