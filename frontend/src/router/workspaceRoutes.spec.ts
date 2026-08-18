// The project frame is a routing shape, and a routing shape is data: a wrong
// one still typechecks and still renders something. What earns these tests is
// that the shape carries two promises the compiler cannot see —
//
//   1. every project page stays INSIDE the frame (its match chain starts at the
//      parent record, which is what keeps the sidebar mounted). Pull one back
//      out to the top level and nothing fails to compile; the sidebar just
//      disappears on that one page again.
//   2. no link that ever worked stops working — `?topic=`, and the three
//      项目文档 routes that used to be distinguished by name.
//
// The records under test are IMPORTED, not restated. `resolve()` is used rather
// than `push()` throughout: it exercises the real matcher without pulling every
// view in the workspace into the test.
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { legacyProjectRedirects } from './legacyProjectPaths'
import { workspaceRoutes } from './workspaceRoutes'

function router() {
  return createRouter({
    history: createMemoryHistory(),
    // Same order as router/index.ts: the legacy redirects come first, because
    // one of them matches the very path shape the workspace claims.
    routes: [...legacyProjectRedirects, workspaceRoutes],
  })
}

const PROJECT = '3f1a7c62-9d4e-4b8a-8f21-0c5d6e7a9b10'
const TOPIC = '9b81c0de-1f22-4a33-9c44-5d6e7f8a9b01'

describe('the project frame', () => {
  it.each([
    ['/', 'workspace-project'],
    [`/topics/${TOPIC}`, 'workspace-topic'],
    ['/dm/cheese', 'workspace-dm'],
    ['/docs/decisions', 'project-docs'],
    ['/overview', 'overview'],
    ['/calendar', 'calendar'],
    ['/settings', 'project-settings'],
    ['/members/lisi', 'member'],
  ])('renders %s inside the frame, not beside it', (suffix, name) => {
    const resolved = router().resolve(`/projects/${PROJECT}${suffix === '/' ? '' : suffix}`)
    expect(resolved.name).toBe(name)
    // The frame is the FIRST matched record — that is what "inside" means here,
    // and it is what keeps the sidebar mounted across all of these.
    expect(resolved.matched[0].path).toBe('/projects/:projectId')
    expect(resolved.matched.length).toBeGreaterThan(1)
    expect(resolved.params.projectId).toBe(PROJECT)
  })

  it('renders a sidebar and a content view at the frame level', () => {
    const frame = router().resolve(`/projects/${PROJECT}/overview`).matched[0]
    expect(Object.keys(frame.components ?? {}).sort()).toEqual(['default', 'sidebar'])
  })

  it('puts what you are looking at in the URL', () => {
    const r = router()
    expect(r.resolve(`/projects/${PROJECT}/topics/${TOPIC}`).params.topicId).toBe(TOPIC)
    expect(r.resolve(`/projects/${PROJECT}/dm/lisi`).params.peer).toBe('lisi')
    expect(r.resolve(`/projects/${PROJECT}/docs/weeklies`).params.kind).toBe('weeklies')
  })

  it('hands each child its params as props, so a reload rebuilds the same view', () => {
    const topic = router().resolve(`/projects/${PROJECT}/topics/${TOPIC}`)
    expect(topic.matched[topic.matched.length - 1].props.default).toBe(true)
  })
})

describe('links that used to work', () => {
  // Every workspace link ever pasted into a chat carries ?topic=. The topic is
  // a path segment now, so WorkspaceEntry answers the query — but the URL has
  // to keep RESOLVING first, and it has to keep the query intact to be read.
  it('still resolves a ?topic= link, query intact', () => {
    const resolved = router().resolve(`/projects/${PROJECT}?topic=${TOPIC}`)
    expect(resolved.name).toBe('workspace-project')
    expect(resolved.query.topic).toBe(TOPIC)
  })

  it.each(['charter', 'decisions', 'weeklies'])('sends the old /%s page to docs/:kind', async (kind) => {
    const r = router()
    // A redirect only runs during navigation, and these carry a function that
    // must copy the project id across — calling resolve() would skip both.
    await r.push(`/projects/${PROJECT}/${kind}`)
    expect(r.currentRoute.value.name).toBe('project-docs')
    expect(r.currentRoute.value.path).toBe(`/projects/${PROJECT}/docs/${kind}`)
  })
})

// 手机上工作台是一条页面栈：工作区 → 话题列表 → 话题页。栈末端那一层要收起
// 底栏（它不是一级目的地）并给出回哪儿去——两样都是路由上的数据，缺了不会编译
// 失败，只会在手机上表现为"底栏压着对话框"或"进去就出不来"。
describe('页面栈的末端', () => {
  const leafOf = (path: string) => {
    const matched = router().resolve(path).matched
    return matched[matched.length - 1]
  }

  // 话题列表之外的每一层都是走进去的，所以都得能走回来。漏一条不会编译失败，
  // 只会在手机上表现为"进去就出不来"，而且是新加一条路由时最容易漏的一件事。
  it('列表之外的每一层都收起底栏，并说明回哪一层', () => {
    const paths = [
      `/projects/${PROJECT}/topics/t1`,
      `/projects/${PROJECT}/dm/cheese`,
      `/projects/${PROJECT}/docs/charter`,
      `/projects/${PROJECT}/overview`,
      `/projects/${PROJECT}/calendar`,
      `/projects/${PROJECT}/settings`,
      `/projects/${PROJECT}/members/alice`,
    ]
    for (const path of paths) {
      const leaf = leafOf(path)
      expect(leaf.meta.hideTabs, path).toBe(true)
      expect(leaf.meta.backTo, path).toBe('workspace-project')
    }
  })

  it('话题列表那一层自己是一级目的地，底栏留着', () => {
    expect(leafOf(`/projects/${PROJECT}`).meta.hideTabs).toBeUndefined()
  })

  // 自带页头的那两层不要系统顶栏：两条横条写同一个标题，在手机上占掉一屏的 13%。
  it('自带页头的层不再叠一条系统顶栏', () => {
    expect(leafOf(`/projects/${PROJECT}`).meta.ownHeader).toBe(true)
    expect(leafOf(`/projects/${PROJECT}/topics/t1`).meta.ownHeader).toBe(true)
    expect(leafOf(`/projects/${PROJECT}/dm/cheese`).meta.ownHeader).toBeUndefined()
  })
})
