// The task description is stored as editor JSON, and the migration that moved
// each task's old video link into its description wrote the video node by hand.
// An editor refuses a node its schema does not know, so the two must agree.
import { getSchema } from '@tiptap/core'
import { Node as PMNode } from '@tiptap/pm/model'
import { describe, expect, it } from 'vitest'

import { richTextExtensions } from './richText'

const LINK = 'https://www.bilibili.com/video/BV1xx411c7mD'

describe('a task description holding a video', () => {
  it('keeps the video the migration wrote', () => {
    const schema = getSchema(richTextExtensions())
    const doc = PMNode.fromJSON(schema, {
      type: 'doc',
      content: [{ type: 'video', attrs: { src: LINK } }, { type: 'paragraph' }],
    })
    expect(doc.firstChild?.type.name).toBe('video')
    expect(doc.firstChild?.attrs.src).toBe(LINK)
  })
})
