import type { FeedbackKind } from '@/cx_types'

import { computed } from 'vue'

import { t } from '@/i18n'
import { useFeedbackStore } from '@/stores/feedback'

// 提交表单里**跟着类型走的那一份说法**：类型那一排有哪几档，以及每一档下面标题的占位
// 语、说明那一栏的标题与占位语、那句后果说明。
//
// 三条表都是**字面量的表**，不拼键：i18n 的闸门（`src/i18n/catalog.spec.ts`）是照着
// 源码里的字面量认「这个键有人用」的，拼出来的键既不会被算作一次调用，真正那三个叶子
// 又会被判成「没有任何文件引用」—— 一边报「键不存在」、一边报「没人用」，两句话都不
// 指向真正的原因。
//
// 表值写成**函数**而不是字符串：`t()` 要在求值的那一刻读当前语言。写成常量会在 setup
// 时求值一次，之后切语言这一栏就不跟着变了（而语言是可以在提交页上切的）。
//
// 类型的**名字**（Bug / 建议 / 其他）不在这里：它走 `feedbackLabels.kindLabel()`
// （i18n 的 `feedback.kind.*`），卡片、详情页、管理台读的也是它。这一份界面再从词表里
// 写一遍同样的三个词，就是同一个事实的第二份拷贝，而那种漂开的表现是「列表里叫建议、
// 表单里叫功能请求」。
const BODY_COPY: Record<FeedbackKind, () => { label: string; placeholder: string }> = {
  bug: () => ({
    label: t('feedback.submit.field.body.bug.label'),
    placeholder: t('feedback.submit.field.body.bug.placeholder'),
  }),
  suggestion: () => ({
    label: t('feedback.submit.field.body.suggestion.label'),
    placeholder: t('feedback.submit.field.body.suggestion.placeholder'),
  }),
  other: () => ({
    label: t('feedback.submit.field.body.other.label'),
    placeholder: t('feedback.submit.field.body.other.placeholder'),
  }),
}

/** 标题那一栏的占位语也跟着类型走：建议和其他不是「发生了什么」。字面量表，理由同上。 */
const TITLE_PLACEHOLDER: Record<FeedbackKind, () => string> = {
  bug: () => t('feedback.submit.field.title.placeholder.bug'),
  suggestion: () => t('feedback.submit.field.title.placeholder.suggestion'),
  other: () => t('feedback.submit.field.title.placeholder.other'),
}

/** 每一类下面那句后果说明，同样是字面量表，理由同上。 */
const KIND_HINT: Record<FeedbackKind, () => string> = {
  bug: () => t('feedback.submit.kind.hint.bug'),
  suggestion: () => t('feedback.submit.kind.hint.suggestion'),
  other: () => t('feedback.submit.kind.hint.other'),
}

export function useFeedbackSubmitCopy() {
  const store = useFeedbackStore()

  /** 类型那一排有哪几档。词表来自服务端；meta 还没到时用这三个 —— 它们是
   *  `FeedbackKind` 的全部取值。 */
  const kinds = computed<FeedbackKind[]>(() => store.meta?.kinds ?? ['bug', 'suggestion', 'other'])

  const titlePlaceholder = computed(() => TITLE_PLACEHOLDER[store.draft.kind]())
  const bodyLabel = computed(() => BODY_COPY[store.draft.kind]().label)
  const bodyPlaceholder = computed(() => BODY_COPY[store.draft.kind]().placeholder)
  const kindHint = computed(() => KIND_HINT[store.draft.kind]())

  return { kinds, titlePlaceholder, bodyLabel, bodyPlaceholder, kindHint }
}
