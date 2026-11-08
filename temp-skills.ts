// 隔离亲验入口（不进仓库）：挂真的 AgentTeamSettings（真 useAgentSkills、真技能面板），
// 只把浏览器里的 fetch 换成内存里的假后端。页面顶上那条是假后端的开关，不是产品界面。
import 'vuetify/styles'
import '@/styles/fonts.css'
import './style.css'

import { createApp, h } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'

import AgentTeamSettings from '@/components/settings/AgentTeamSettings.vue'
import i18n, { localeReady, setLocale } from '@/i18n'
import vuetify from '@/plugins/vuetify'
import { queryClient } from '@/query/client'
import { createPinia } from 'pinia'
import { VSonner } from 'vuetify-sonner'

const PROJECT = 'p-harness'
const qs = new URLSearchParams(location.search)
const state = {
  version: 1,
  skills: ['wolfram', 'pdf', 'documents'],
  failNext: qs.get('fail') === '1',
  held: qs.get('held') === '1',
  offline: qs.get('offline') === '1',
  saves: [] as string[],
}
const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, '')
localStorage.setItem('accessToken', `${b64({ alg: 'none' })}.${b64({ exp: 4102444800, sub: 'me' })}.x`)
localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }))

function agentRow() {
  return {
    id: 'a1', project_id: PROJECT, handle: 'cheese-harness', type_name: null, display_name: '芝士',
    seat_handle: 'cheese-a1', is_default: true, is_active: true, version: state.version,
    configuration: { body: '', skills: [...state.skills] },
  }
}
const ok = (data: unknown) => new Response(JSON.stringify({ code: 200, data, message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
const raw = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const fail = (status: number, message: string) => new Response(JSON.stringify({ code: status, message, error: { name: 'Harness', message } }), { status, headers: { 'Content-Type': 'application/json' } })

const realFetch = window.fetch.bind(window)
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
  const p = url.pathname
  const method = (init?.method ?? 'GET').toUpperCase()
  await new Promise((r) => setTimeout(r, 250))
  if (p.startsWith('/connector/')) {
    if (p === '/connector/my/devices') return raw({ devices: [{ device_id: 'dev1', name: '我的电脑', online: true, project_ids: [PROJECT], team_ids: [], screens: [] }] })
    if (p.endsWith('/skills') && p.includes('/my/devices/') && state.offline) return raw({ detail: '模拟：机器离线' }, 503)
    if (p.endsWith('/skills') && p.includes('/my/devices/')) return raw({ skills: [
      { name: 'pdf', description: '读 PDF、抽表格', source_root: '/home/me/.claude/skills/pdf', digest: 'd1', state: 'adopted' },
      { name: 'ponytail', description: '懒，但把活干对的写法（机器上有、没带）', source_root: '/home/me/.claude/skills/ponytail', digest: 'd2', state: 'adopted' },
    ] })
    if (p === '/connector/skills/market') return raw({ skills: [], plugins: [], plugin_total: 0, mcp: [], notes: [] })
    return raw({})
  }
  if (!p.startsWith('/api/')) return realFetch(input, init)
  const a = p.slice(4)
  if (a === `/projects/${PROJECT}/agents` && method === 'GET') return ok({ data: [agentRow()], total: 1 })
  if (a === '/agent-types') return ok({ data: [], total: 0 })
  if (a === `/projects/${PROJECT}/default-model`) return ok({ choices: [{ id: 'glm-5.2', label: 'GLM-5.2', default: true, allowed: true, requires_plan: null, efforts: [] }] })
  if (a === `/projects/${PROJECT}/skill-catalog`) return ok({ data: [{ name: 'wolfram', description: '算得出来的问题交给它' }, { name: 'documents', description: '读写 Word / PPT / Excel / PDF' }, { name: 'cheese', description: '在知是里交付工作（平台给每位队友都发）' }], total: 3 })
  if (a === `/projects/${PROJECT}/skills`) return ok({ data: [], total: 0 })
  if (a.endsWith('/health')) return ok({ status: 'ok', detail: '' })
  if (a.endsWith('/editing')) {
    const held = JSON.parse(String(init?.body ?? '{}')).held
    if (held && state.held) return fail(409, 'alice 正在编辑这个队友')
    return ok({ editing: held ? { handle: 'me', seconds: 60 } : null })
  }
  if (a === `/projects/${PROJECT}/agents/a1` && (method === 'PATCH' || method === 'PUT')) {
    const body = JSON.parse(String(init?.body ?? '{}'))
    if (state.failNext) { state.failNext = false; paint(); return fail(500, '模拟：服务端没存进去') }
    if (body.expected_version !== state.version) return fail(409, 'stale')
    state.skills = body.configuration.skills
    state.version += 1
    state.saves.push(`v${state.version}: [${state.skills.join(', ')}]`)
    paint()
    return ok(agentRow())
  }
  if (a.startsWith('/projects/') && a.endsWith('/own-agents')) return ok({ can_manage: true, agents: [], my_connections: [], devices: [] })
  console.warn('[harness] unhandled', method, p)
  return ok({})
}

function paint() {
  const bar = document.getElementById('harness-bar')!
  bar.innerHTML = `<div style="font:13px/1.6 system-ui,sans-serif;padding:8px 12px;background:#eef2f7;border-bottom:1px solid #ccd;">
  <b>隔离亲验入口</b>（假后端在浏览器内存里，刷新即复原）·
  <label><input id="h-fail" type="checkbox" ${state.failNext ? 'checked' : ''}/> 下一次保存失败（模拟 500）</label> ·
  <a href="?held=1">模拟别人正在编辑（只读）</a> · <a href="?offline=1">模拟机器离线</a> · <a href="?">复原</a><br/>
  后端当前名单：<code id="h-now">[${state.skills.join(', ')}]</code> · 版本 v${state.version} · 已保存：${state.saves.join('；') || '无'}</div>`
  ;(document.getElementById('h-fail') as HTMLInputElement).onchange = (e) => { state.failNext = (e.target as HTMLInputElement).checked }
}
paint()

setLocale('zh-CN')
await localeReady?.catch?.(() => {})
createApp({ render: () => h('div', { style: 'max-width:900px;margin:16px auto;padding:0 12px' }, [h(AgentTeamSettings, { projectId: PROJECT }), h(VSonner, { position: 'top-center' })]) })
  .use(createPinia()).use(i18n).use(vuetify).use(VueQueryPlugin, { queryClient })
  .mount('#app')
