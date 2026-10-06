// A Bilibili link on a line of its own is a video; anywhere else it is a link.
// What is stored is the link, so the text reads the same wherever the player is
// not drawn, and the player's address can only ever point at Bilibili's player.
import { describe, expect, it } from 'vitest'

import { videoEmbed } from './video'
import { nodeMarkdown, parseMarkdown } from '.'

const LINK = 'https://www.bilibili.com/video/BV1xx411c7mD'

function videos(md: string): string[] {
  const out: string[] = []
  parseMarkdown(md).descendants((node) => {
    if (node.type.name === 'video') out.push(node.attrs.src as string)
    return true
  })
  return out
}

describe('a video in a document', () => {
  it('is a Bilibili link on a line of its own, and is written back as that line', () => {
    const md = `先看这段讲解：\n\n${LINK}\n\n再做第一题。`
    expect(videos(md)).toEqual([LINK])
    expect(nodeMarkdown(parseMarkdown(md))).toBe(md)
  })

  it('is not made from a link inside a sentence', () => {
    expect(videos(`讲解在这里 ${LINK} ，看完再做。`)).toEqual([])
  })

  it('is not made from a link to another site', () => {
    expect(videos('https://www.youtube.com/watch?v=dQw4w9WgXcQ')).toEqual([])
  })
})

describe('the player address', () => {
  it('comes from the video id, keeping the part number', () => {
    expect(videoEmbed(`${LINK}?p=3&spm_id_from=333`)).toBe(
      'https://player.bilibili.com/player.html?bvid=BV1xx411c7mD&p=3&autoplay=0'
    )
  })

  it('is refused for anything that is not a Bilibili video link', () => {
    expect(videoEmbed('javascript:alert(1)//bilibili.com/video/BV1xx411c7mD')).toBeNull()
    expect(videoEmbed('https://evil.example/www.bilibili.com/video/BV1xx411c7mD')).toBeNull()
    expect(videoEmbed(`${LINK} onload=alert(1)`)).toBeNull()
  })
})
