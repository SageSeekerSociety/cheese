import { t } from '@/i18n'

const TAG_WORD: Record<string, string> = {
  p: 'p',
  a: 'a',
  img: 'img',
  picture: 'img',
  svg: 'img',
  button: 'button',
  table: 'table',
  li: 'li',
  input: 'input',
  textarea: 'input',
  select: 'input',
  h1: 'h',
  h2: 'h',
  h3: 'h',
  h4: 'h',
  h5: 'h',
  h6: 'h',
  section: 'section',
  article: 'section',
  aside: 'section',
  div: 'section',
  main: 'section',
  header: 'section',
  footer: 'section',
  nav: 'section',
}

/** 网页里圈中的那一处在卡片和消息里叫什么：帧找到了标题就写标题，找不到按标签说
 *  「网页里的一处段落」。选择器是给芝士回到那一处的凭据，不拿来给人读。 */
export function webPickLabel(label: string | undefined, tag: string): string {
  const name = label?.trim()
  if (name) return name
  const word = TAG_WORD[tag.toLowerCase()] ?? 'other'
  return t('work.room.preview.webSpot', { what: t(`work.room.preview.webTag.${word}`) })
}
