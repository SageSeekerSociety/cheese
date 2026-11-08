// 临时验收入口，不进仓库：挂产品里真的助手面板（AssistantPanel）和真的全站对话（stores/agent 的
// agentThread + askAgent，内部就是 createAssistant）。后端换成本页 vite 里的替身（见
// accept-9a6b.vite.config.ts）：对话 c1 里有一句服务端存下的「旧的一句」，芝士还在答（流不结束），
// 插话接口 /say 回 500。页面时钟冻在「旧的一句」那一毫秒，模拟同一毫秒发出另一句。
import '@/styles/fonts.css'
import './style.css'

import { computed, createApp, defineComponent, h, ref } from 'vue'

import AssistantPanel from './components/assistant/AssistantPanel.vue'
import i18n, { localeReady } from './i18n'
import vuetify from './plugins/vuetify'
import { agentThread, agentView, askAgent } from './stores/agent'

const NOW = '2026-11-08T00:00:00.000Z'
// 冻结页面时钟：new Date() / Date.now() 都停在 NOW（只模拟「同一毫秒」，不改产品代码）。
const RealDate = Date
const frozen = new RealDate(NOW).getTime()
class FrozenDate extends RealDate {
  constructor(...args: unknown[]) {
    if (args.length) super(...(args as [string]))
    else super(frozen)
  }
  static now() {
    return frozen
  }
}
;(globalThis as { Date: DateConstructor }).Date = FrozenDate as unknown as DateConstructor

const VERSION = new URLSearchParams(location.search).get('v') ?? ''

const App = defineComponent({
  setup() {
    const log = ref<string[]>([])
    const note = (s: string) => log.value.unshift(s)
    const users = computed(() => agentThread.messages.value.filter((m) => m.role === 'user').map((m) => m.text))
    const ok = computed(() => users.value.includes('旧的一句'))
    void agentThread.load().then(() => note('打开对话 c1：读回「旧的一句」（服务端存的，时间 ' + NOW + '），芝士正在答'))
    async function send(text: string) {
      note(`发送「${text}」：芝士在答，走插话（/say），先临时显示；替身 2.5 秒后回 500`)
      await askAgent(text)
      note(`插话失败后，面板上人说的话：${users.value.map((u) => `「${u}」`).join('、') || '（无）'}`)
    }
    async function reset() {
      await fetch('/__acc/reset', { method: 'POST' })
      location.reload()
    }
    return () =>
      h('div', { style: 'display:flex;gap:16px;padding:16px;height:100vh;box-sizing:border-box;font:13px/1.6 system-ui,sans-serif' }, [
        h('div', { style: 'width:340px;flex:none' }, [
          h('h3', { style: 'margin:0 0 6px' }, '乐观消息回滚误删 · 验收'),
          VERSION ? h('div', { id: 'acc-version', style: 'margin:0 0 6px;padding:4px 8px;border-radius:6px;background:#eef3ff;font-weight:600' }, VERSION) : null,
          h('div', { style: 'color:#666;margin-bottom:8px' }, '右边是真的助手面板。「旧的一句」是服务端存下的历史。在输入框里说一句发出去：芝士在答，这句走插话，先临时显示在面板上；替身 2.5 秒后让插话接口回 500，面板收回没被收下的那句。页面时钟冻在「旧的一句」那一毫秒。'),
          h('button', { id: 'acc-send', onClick: () => void send('补一句'), style: 'margin:0 6px 6px 0;padding:4px 10px;border:1px solid #c9a24b;border-radius:6px;background:#fff8e6;cursor:pointer' }, '代我发「补一句」'),
          h('button', { id: 'acc-reset', onClick: reset, style: 'margin:0 6px 6px 0;padding:4px 10px;border:1px solid #c9a24b;border-radius:6px;background:#fff8e6;cursor:pointer' }, '重置'),
          h('div', { style: 'margin-top:10px' }, ['人说的话：', h('b', { id: 'acc-users' }, users.value.map((u) => `「${u}」`).join('、') || '（无）')]),
          h('div', { id: 'acc-verdict', style: `margin-top:4px;font-weight:600;color:${ok.value ? '#2e7d32' : '#c62828'}` }, ok.value ? '「旧的一句」还在（正确）' : '「旧的一句」被一起删掉了'),
          h('div', null, `提示：${agentThread.notice.value ?? '无'}`),
          h('pre', { style: 'background:#f6f6f6;padding:6px;border-radius:4px;white-space:pre-wrap;max-height:40vh;overflow:auto' }, log.value.join('\n') || '（还没操作）'),
        ]),
        h('div', { style: 'flex:1;min-width:0;max-width:480px;border:1px solid #ddd;border-radius:8px;overflow:hidden;display:flex;flex-direction:column' }, [
          h(AssistantPanel, {
            style: 'flex:1;min-height:0',
            messages: agentView.value.messages,
            steps: agentView.value.steps,
            changes: agentView.value.changes,
            results: agentView.value.results,
            proposals: agentView.value.proposals,
            streaming: agentThread.streaming.value,
            tool: agentThread.tool.value,
            queued: agentThread.queued.value,
            notice: agentThread.notice.value,
            busy: agentThread.busy.value,
            greeting: '你好',
            placeholder: '说点什么',
            onSend: (text: string) => void send(text),
          }),
        ]),
      ])
  },
})

void localeReady.finally(() => createApp(App).use(i18n).use(vuetify).mount('#app'))
