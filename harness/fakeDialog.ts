// 替身：移出确认直接回「确定」，好在截图里走到重读那一步；其余导出照原模块。
export * from '../src/plugins/dialog'
export const useDialog = () => ({ confirm: () => ({ wait: async () => true }) })
