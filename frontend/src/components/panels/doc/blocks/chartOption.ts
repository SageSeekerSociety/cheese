// What a chart block draws: its table read as categories and series, turned
// into an ECharts option in the document's colours.
//
// The table is the chart: the first column is the categories (the x values of
// a scatter), every other column a series named by its head. A value label
// shows the cell exactly as written, so `71%` and `$1,274` read the same on
// the chart as in the table. An empty or unreadable cell is a gap, never zero.
import type { ChartKind } from '../../../../lib/docSchema/blocks'

import { chartNumber } from '../../../../lib/docSchema/blocks'

import { t } from '@/i18n'

export interface ChartTheme {
  colors: string[]
  text: string
  muted: string
  line: string
  surface: string
  font: string
  /** The system asks for reduced motion: draw without animating. */
  still: boolean
}

export interface ChartData {
  categories: string[]
  series: { name: string; values: (number | null)[]; texts: string[] }[]
}

/** The table's rows (header first, each a list of cell texts) as chart data. */
export function chartData(rows: string[][]): ChartData {
  const [head = [], ...body] = rows
  const filled = body.filter((row) => row.some((cell) => cell.trim()))
  return {
    categories: filled.map((row) => row[0]?.trim() ?? ''),
    series: head.slice(1).map((name, i) => {
      const texts = filled.map((row) => row[i + 1]?.trim() ?? '')
      return { name: name.trim(), values: texts.map((text) => chartNumber(text) ?? null), texts }
    }),
  }
}

/** How tall the chart is: bars on their side need a row each. */
export function chartHeight(kind: ChartKind, horizontal: boolean, data: ChartData): number {
  if (horizontal) return Math.max(160, data.categories.length * 32 + (data.series.length > 1 ? 72 : 40))
  return kind === 'pie' ? 260 : 240
}

type Option = Record<string, unknown>

export function chartOption(
  kind: ChartKind,
  horizontal: boolean,
  data: ChartData,
  theme: ChartTheme,
  narrow: boolean
): Option {
  const legend = data.series.length > 1 || kind === 'pie'
  const base: Option = {
    color: theme.colors,
    textStyle: { fontFamily: theme.font, color: theme.text },
    animation: !theme.still,
    animationDuration: 220,
    legend: legend
      ? {
          top: 0,
          left: 0,
          type: 'scroll',
          icon: 'roundRect',
          itemWidth: 11,
          itemHeight: 11,
          textStyle: { color: theme.text, fontSize: 12 },
          pageTextStyle: { color: theme.muted },
        }
      : undefined,
    tooltip: {
      trigger: kind === 'pie' || kind === 'scatter' ? 'item' : 'axis',
      backgroundColor: theme.surface,
      borderColor: theme.line,
      textStyle: { color: theme.text, fontSize: 13 },
      confine: true,
    },
  }
  const label = (seriesIndex: number) => (p: { dataIndex: number }) =>
    data.series[seriesIndex]?.texts[p.dataIndex] ?? ''

  if (kind === 'pie') {
    const first = data.series[0]
    return {
      ...base,
      tooltip: {
        ...(base.tooltip as Option),
        formatter: (p: { name: string; dataIndex: number; percent: number }) =>
          t('work.room.doc.blocks.chartSlice', {
            name: p.name,
            value: first?.texts[p.dataIndex] ?? '',
            percent: p.percent,
          }),
      },
      series: [
        {
          type: 'pie',
          radius: narrow ? ['34%', '60%'] : ['40%', '68%'],
          center: ['50%', '58%'],
          data: data.categories.map((name, i) => ({ name, value: first?.values[i] ?? 0 })),
          // On a phone the names are in the legend and the shares sit on the
          // slices: labels outside would run off a narrow screen.
          label: narrow
            ? { position: 'inside', color: theme.surface, fontSize: 12, fontWeight: 600, formatter: '{d}%' }
            : { color: theme.text, fontSize: 12, formatter: '{b}\n{d}%' },
          itemStyle: { borderColor: theme.surface, borderWidth: 2 },
        },
      ],
    }
  }

  const valueAxis = {
    type: 'value',
    splitLine: { lineStyle: { color: theme.line, type: 'dashed' } },
    axisLabel: { color: theme.muted, fontSize: 12 },
  }
  const grid = { left: 4, right: 12, top: legend ? 32 : 12, bottom: 4, containLabel: true }

  if (kind === 'scatter') {
    const xs = data.categories.map((text) => chartNumber(text) ?? null)
    return {
      ...base,
      grid,
      xAxis: { ...valueAxis, scale: true },
      yAxis: { ...valueAxis, scale: true },
      series: data.series.map((s) => ({
        type: 'scatter',
        name: s.name,
        symbolSize: 9,
        data: s.values.map((y, i) => (xs[i] === null || y === null ? null : [xs[i], y])).filter(Boolean),
      })),
    }
  }

  const categoryAxis = {
    type: 'category',
    data: data.categories,
    inverse: horizontal,
    axisTick: { show: false },
    axisLine: { lineStyle: { color: theme.line } },
    axisLabel: { color: theme.muted, fontSize: 12, hideOverlap: true },
    boundaryGap: kind === 'bar' || kind === 'stacked',
  }
  const few = data.categories.length <= (narrow ? 6 : 12)
  return {
    ...base,
    grid,
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? categoryAxis : valueAxis,
    series: data.series.map((s, i) => {
      const bars = kind === 'bar' || kind === 'stacked'
      return {
        type: bars ? 'bar' : 'line',
        name: s.name,
        data: s.values,
        stack: kind === 'stacked' ? 'all' : undefined,
        areaStyle: kind === 'area' ? { opacity: 0.16 } : undefined,
        connectNulls: false,
        symbol: 'circle',
        symbolSize: 6,
        barMaxWidth: 28,
        itemStyle: bars && kind !== 'stacked' ? { borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] } : undefined,
        label: {
          show: kind !== 'stacked' && few && (bars || data.series.length === 1),
          position: horizontal ? 'right' : 'top',
          color: theme.muted,
          fontSize: 12,
          formatter: label(i),
        },
      }
    }),
  }
}
