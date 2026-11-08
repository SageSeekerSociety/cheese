import '@/styles/content.scss'
import '@/styles/fonts.css'
import '../src/style.css'
import { createApp, h, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import vuetify from '@/plugins/vuetify'
import i18n, { localeReady, setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import Members from '@/views/teams/detail/Members.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'TeamsDetailMembers', component: { render: () => null } },
    { path: '/t', name: 'TeamsDetailDefault', component: { render: () => null } },
  ],
})
const team = ref({
  id: 7, handle: 'crew', name: 'Cheese 核心组', intro: '', avatarId: 0, owner: { id: 1 },
  admins: { total: 1, examples: [] }, members: { total: 3, examples: [] }, visibility: 'public',
  role: 'MEMBER', personal: false,
})
// 顶上一条说明：这是哪一版、哪个场景、接口被读了几次，截图里一眼看得出。
const reads = ref(0)
setInterval(() => (reads.value = (window as any).__READS || 0), 50)
const LABELS: Record<string, string> = {
  pending: '首次读取在途（接口一直不回）',
  empty: '读回真实空名单',
  failed: '首次读取失败',
  reread: '已有名单 → 移出 qinmo 后重读在途',
  'reread-fail': '已有名单 → 移出 qinmo 后重读失败',
  manual: '手动放行（每次读成员都停住，点右边按钮决定回什么）',
}
const waiting = ref(0)
setInterval(() => (waiting.value = ((window as any).__WAITING || []).length), 50)
const release = (answer: string) => {
  const q = (window as any).__WAITING as ((a: string) => void)[]
  q.splice(0).forEach((res) => res(answer))
}
const btn = (label: string, answer: string) =>
  h('button', { style: 'margin-left:8px;padding:2px 10px;border:1px solid #c9a24a;border-radius:6px;background:#fff;cursor:pointer', disabled: !waiting.value, onClick: () => release(answer) }, label)
const links = () =>
  h('div', { style: 'margin-top:4px' }, [
    '切换场景：',
    ...Object.keys(LABELS).map((k) => h('a', { href: `?s=${k}`, style: 'margin-right:10px' }, k)),
  ])
const bar = () =>
  h('div', { style: 'font:13px/1.6 system-ui;background:#fff7e6;border:1px solid #f0c36d;border-radius:8px;padding:8px 12px;margin:12px 16px;color:#5c3d00' }, [
    `隔离亲验 · 版本 ${(window as any).__BUILD} · 场景：${LABELS[(window as any).__SCENARIO]} · 成员接口已读 ${reads.value} 次 · 接口为替身，页面是真的团队成员页`,
    ...((window as any).__SCENARIO === 'manual'
      ? [h('div', { style: 'margin-top:4px' }, [`等待中的读取：${waiting.value} 个`, btn('回名单', 'list'), btn('回空名单', 'empty'), btn('回失败', 'fail')])]
      : []),
    (window as any).__SHOTS ? null : links(),
  ])
const app = createApp({ render: () => h('div', [bar(), h('div', { style: 'max-width:852px;padding:0 16px' }, [h(Members)])]) })
app.use(i18n).use(vuetify).use(router)
app.provide(teamDataInjectionKey as symbol, team)
localeReady.then(() => setLocale('zh-CN')).then(() => router.push('/')).then(() => app.mount('#app'))

// 「改动后重读」：等名单出来，点那一行的「⋯」→「移除成员」，走到重读在途/失败那一步。
const s = (window as any).__SCENARIO
if (s === 'reread' || s === 'reread-fail') {
  const until = async (f: () => any) => { for (let i = 0; i < 100; i++) { const v = f(); if (v) return v; await new Promise((r) => setTimeout(r, 50)) } }
  ;(async () => {
    const btn = await until(() => document.querySelector('[aria-label="qinmo 的操作"]'))
    ;(btn as HTMLElement).click()
    const item = await until(() => [...document.querySelectorAll('.v-list-item, [role="menuitem"]')].find((e) => e.textContent?.includes('移除成员')))
    ;(item as HTMLElement).click()
    await until(() => (window as any).__READS >= 2)
    await new Promise((r) => setTimeout(r, 300))
    document.body.dataset.done = '1'
  })()
}
