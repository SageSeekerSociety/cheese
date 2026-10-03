/** 弹窗宽度三档（docs/design-system.md §3.7）。
 *
 *  从全仓 63 个 v-dialog 的宽度归出来：420/440 最多（确认框、一两个字段），
 *  520–600 是一般表单，640–720 是并排两列或带预览的表单。 */
export type DialogSize = 'sm' | 'md' | 'lg'

export const DIALOG_WIDTH: Record<DialogSize, number> = { sm: 420, md: 560, lg: 720 }
