import type { QuotedContext } from './quotedContext'

import { t } from '../i18n'

/** 各种引用共用一个框：文字引用说引的是哪一段，位置引用说指在哪儿。
 *
 *  按 `kind` 分派，最后一支兜住这个版本还不认识的 kind——库里可能已经躺着更新的
 *  客户端发来的引用，读到它时画不出细节，但至少要说得出「这里有一份引用」，不能
 *  把 kind 当页码算出一串 undefined。 */
export function quoteSummary(quote: QuotedContext): string {
  switch (quote.kind) {
    case 'slide-page':
      return t(quote.scope === 'selection' ? 'slides.quotedSelection' : 'slides.quotedPage', {
        page: quote.page,
      })
    case 'page-pin':
      return t('slides.pinnedPage', { page: quote.page })
    case 'sheet-cell':
      return t('slides.quotedCell', {
        address: quote.sheet ? `${quote.sheet}!${quote.address}` : quote.address,
      })
    case 'text-range':
      return t('slides.quotedPassage')
    case 'web-element':
      return t('slides.quotedWebElement')
    case 'web-text':
      return t('slides.quotedWebText')
    case 'web-region':
      return t('slides.quotedWebRegion')
    default:
      return t('slides.quotedSource')
  }
}
