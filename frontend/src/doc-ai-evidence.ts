import './style.css'
import './styles/fonts.css'
import './styles/content.scss'

import { createApp, h } from 'vue'
import { VApp } from 'vuetify/components'

import PanelDoc from './components/panels/PanelDoc.vue'
import vuetify from './plugins/vuetify'
import i18n, { setLocale } from './i18n'

setLocale('zh-CN')
const topic = {
  id: '11111111-1111-4111-8111-111111111111',
  project_id: '22222222-2222-4222-8222-222222222222',
  title: 'Docs 完整主链核验',
  kind: 'topic',
  status: 'active',
  parent_id: null,
  created_by: 'reader',
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}
const app = createApp({
  render: () =>
    h(VApp, null, {
      default: () =>
        h('main', { style: 'height:100vh;display:flex;justify-content:flex-end;background:var(--bg)' }, [
          h('aside', { style: 'padding:24px;flex:1' }, [
            h('h1', 'Docs 实际组件核验'),
            h('p', '生产 PanelDoc → PanelDocView → DocSurface、AI 卡片和评论线程。'),
            h('p', '接口由本地浏览器夹具提供；此 PDF 不证明线上 AI 或数据库行为。'),
          ]),
          h('section', { style: 'width:min(880px,100vw);height:100%;border-left:1px solid var(--line)' }, [
            h(PanelDoc, { topic, activityTick: 0, topicList: [topic] }),
          ]),
        ]),
    }),
})
app.use(i18n)
app.use(vuetify)
app.mount('#app')
