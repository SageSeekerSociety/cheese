// The document's colours for charts, read from the tokens on the page.
const listeners = new Set<() => void>()
export function onTheme(fn: () => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
export function themeChanged() {
  for (const fn of listeners) fn()
}
export function token(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim()
}
export function chartTheme(): object {
  const axis = {
    axisLine: { lineStyle: { color: token('line') } },
    axisTick: { lineStyle: { color: token('line') } },
    axisLabel: { color: token('muted') },
    splitLine: { lineStyle: { color: token('line') } },
    nameTextStyle: { color: token('muted') },
  }
  return {
    color: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => token(`chart-${i}`)),
    backgroundColor: 'transparent',
    textStyle: { color: token('text'), fontFamily: token('font-sans') },
    title: { textStyle: { color: token('ink') }, subtextStyle: { color: token('muted') } },
    legend: { textStyle: { color: token('text') } },
    tooltip: { backgroundColor: token('surface'), borderColor: token('line'), textStyle: { color: token('text') } },
    categoryAxis: axis,
    valueAxis: axis,
    logAxis: axis,
    timeAxis: axis,
  }
}
