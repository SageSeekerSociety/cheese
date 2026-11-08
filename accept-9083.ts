// 临时验收入口，不进仓库：只挂产品里真的现场栏（PanelSite + usePanelSite + useSiteTranscript）。
// 后端换成本页 vite 里的替身（见 accept-9083.vite.config.ts），socket 推送由按钮调用真的 receive。
import '@/styles/fonts.css'
import './style.css'
import type { Block } from './cx_types'

import { computed, createApp, defineComponent, h, ref } from 'vue'

import PanelSite from './components/panels/PanelSite.vue'
import { usePanelSite } from './composables/usePanelSite'
import i18n, { localeReady } from './i18n'
import vuetify from './plugins/vuetify'

const TOPIC = 'accept-demo'
const row = (id: string, second: number, text: string): Block =>
  ({
    id,
    conversation_id: TOPIC,
    kind: 'event',
    author: 'system',
    author_type: 'system',
    turn_id: null,
    content: text,
    created_at: `2026-11-07T10:00:${String(second).padStart(2, '0')}Z`,
    refs: [],
    run_record: true,
    meta: { event_type: 'message_edited' },
  }) as unknown as Block

const App = defineComponent({
  setup() {
    const tick = ref(0)
    const site = usePanelSite({
      topicId: () => TOPIC,
      active: () => true,
      refreshTick: () => tick.value,
      runningTurns: () => ({}),
      agentControl: () => null,
    })
    const log = ref<string[]>([])
    const note = (s: string) => log.value.unshift(`${new Date().toTimeString().slice(0, 8)} ${s}`)
    const order = computed(() => site.transcript.value.map((b) => b.id).join(' → ') || '（空）')
    const ok = computed(() => {
      const ts = site.transcript.value.map((b) => Date.parse(b.created_at))
      return ts.every((t, i) => i === 0 || ts[i - 1] <= t)
    })
    async function push() {
      site.receive(row('t2', 2, 't2 · 10:00:02 第二条（较新，socket 先送到）'))
      note('模拟 socket 推送 t2')
    }
    async function release() {
      await fetch('/__acc/release', { method: 'POST' })
      tick.value += 1
      note('后端放出 [t1, t2]，现场刷新一次（往后读 after=0）')
    }
    async function reset() {
      await fetch('/__acc/reset', { method: 'POST' })
      location.reload()
    }
    const btn = (label: string, on: () => void, id: string) =>
      h('button', { id, onClick: on, style: 'margin:0 6px 6px 0;padding:4px 10px;border:1px solid #c9a24b;border-radius:6px;background:#fff8e6;cursor:pointer' }, label)
    return () =>
      h('div', { style: 'display:flex;gap:16px;padding:16px;height:100vh;box-sizing:border-box;font:13px/1.6 system-ui,sans-serif' }, [
        h('div', { style: 'width:340px;flex:none' }, [
          h('h3', { style: 'margin:0 0 6px' }, '现场增量行时间错序 · 验收'),
          h('div', { style: 'color:#666;margin-bottom:8px' }, '左边按钮驱动右边真的现场栏。后端与 socket 是替身，现场栏组件和取数逻辑是产品代码。'),
          btn('① 模拟 socket 推送 t2', push, 'acc-push'),
          btn('② 后端放出 [t1, t2] 并刷新', release, 'acc-release'),
          btn('重置', reset, 'acc-reset'),
          h('div', { style: 'margin-top:10px' }, ['现场顺序：', h('b', { id: 'acc-order' }, order.value)]),
          h('div', { id: 'acc-verdict', style: `margin-top:4px;font-weight:600;color:${ok.value ? '#2e7d32' : '#c62828'}` }, ok.value ? '按时间排（正确）' : '时间错序（t2 在 t1 上面）'),
          h('div', null, `错误提示：${site.errorMsg.value ?? '无'}`),
          h('pre', { style: 'background:#f6f6f6;padding:6px;border-radius:4px;white-space:pre-wrap;max-height:40vh;overflow:auto' }, log.value.join('\n') || '（还没操作）'),
        ]),
        h('div', { style: 'flex:1;min-width:0;border:1px solid #ddd;border-radius:8px;overflow:hidden;display:flex;flex-direction:column' }, [
          h(PanelSite, { topicId: TOPIC, active: true, site, style: 'flex:1;min-height:0' }),
        ]),
      ])
  },
})

void localeReady.finally(() => createApp(App).use(i18n).use(vuetify).mount('#app'))
