// 卡上「这一处在哪儿」怎么写，页式文档那一档。
//
// 和 markdown 那边「标题「二、时间安排」」是同一件事：一句页码只说出这是哪一页，说不出
// 一页里的哪一处。原型写的是 `第 1 页 · 二、时间安排`。

import { t } from '@/i18n'

/** `第 2 页 · 二、时间安排`；认不出这一处在哪一节时只有 `第 2 页`。
 *
 *  单独拿出来是为了能直接测：拼错一个字，卡上就会把读者指到一个不存在的节。 */
export function pageLabel(page: number, section?: string): string {
  const name = section?.trim()
  return name ? t('work.room.preview.pageSection', { page, section: name }) : t('work.room.preview.page', { page })
}
