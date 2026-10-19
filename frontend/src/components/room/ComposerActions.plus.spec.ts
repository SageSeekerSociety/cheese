// 输入框下面那一行，桌面和手机同一个结构：往这条消息里放东西的入口都在「+」里，从里面
// 选一项和以前直接点那一颗是同一件事；右边是麦克风和发送。录音时发送键就是「说完了」。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import ComposerActions from './ComposerActions.vue'

import { setLocale, t } from '@/i18n'

beforeAll(() => {
  setLocale('zh-CN')
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 底部面板是 VOverlay，happy-dom 没有 visualViewport，不补上浮层挂不起来。
  vi.stubGlobal('visualViewport', {
    width: 375,
    height: 812,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

function mount(width: number, extras: Record<string, unknown> = {}) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  return render(ComposerActions, {
    props: {
      uploading: false,
      canSend: true,
      showImagePicker: width < 960,
      enterSends: true,
      canChecklist: true,
      canRemind: true,
      ...extras,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

async function plusItems(): Promise<string[]> {
  await fireEvent.click(screen.getByLabelText(t('work.room.composer.more')))
  return (await screen.findAllByRole('menuitem')).map((el) => el.textContent?.trim() ?? '')
}

describe('输入框下面那一行', () => {
  it('桌面上：上传文件、清单、提醒、键盘快捷键都在「+」里，外面不再摆这几颗', async () => {
    mount(1280)
    expect(screen.queryByLabelText(t('work.room.checklist.compose'))).toBeNull()
    expect(screen.queryByLabelText(t('work.room.reminder.open'))).toBeNull()
    expect(await plusItems()).toEqual([
      t('work.room.composer.attachFiles'),
      t('work.room.checklist.compose'),
      t('work.room.reminder.open'),
      expect.stringContaining(t('global.shortcuts.open')),
    ])
  })

  it('手机上同一个「+」，多一项照片或视频', async () => {
    mount(375)
    const items = await plusItems()
    expect(items.slice(0, 4)).toEqual([
      t('work.room.composer.attachFiles'),
      t('work.room.composer.sendPhotos'),
      t('work.room.checklist.compose'),
      t('work.room.reminder.open'),
    ])
  })

  it('从「+」里点「发清单」就是发清单', async () => {
    const { emitted } = mount(1280)
    await plusItems()
    await fireEvent.click(screen.getByRole('menuitem', { name: t('work.room.checklist.compose') }))
    expect(emitted().checklist).toHaveLength(1)
    expect(emitted().remind).toBeUndefined()
  })

  it('房间给不了发清单的路时，「+」里就没有这一项', async () => {
    mount(1280, { canChecklist: false })
    expect(await plusItems()).not.toContain(t('work.room.checklist.compose'))
  })

  it('刚说进来一段时多一颗「还原原话」，点它换成原话', async () => {
    const { emitted } = mount(1280, { voiceSwap: 'raw' })
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.composer.voice.useRaw') }))
    expect(emitted()['voice-swap']).toHaveLength(1)
  })

  it('录着的时候，发送键那个位置是「说完了」：点它结束录音，不发消息', async () => {
    const { emitted } = mount(1280, { voice: 'recording', canSend: false })
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.composer.voice.done') }))
    expect(emitted()['voice-done']).toHaveLength(1)
    expect(emitted().send).toBeUndefined()
  })

  it('整理中点发送键那个位置什么也不发', async () => {
    const { emitted } = mount(1280, { voice: 'thinking', canSend: true })
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.composer.voice.thinking') }))
    expect(emitted().send).toBeUndefined()
    expect(emitted()['voice-done']).toBeUndefined()
  })
})
