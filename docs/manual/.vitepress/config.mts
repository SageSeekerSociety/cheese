import fs from 'node:fs'
import path from 'node:path'
import { defineConfig } from 'vitepress'

// 内容就在上一层（vitepress 的标准布局：站的根 = 内容目录）。写文档的人不该为了加一页而读
// vitepress 的文档，所以侧边栏是扫出来的：新建一个 .md、写好 frontmatter，
// 它就出现在导航里。order 决定顺序，缺了就排到最后（按文件名）。
const CONTENT = path.resolve(__dirname, '..')

// 侧边栏按功能分组。组的顺序写死在这里(它是产品的叙事顺序,不该由文件名决定);
// 组内顺序看 order。加一页 = 新建一个 .md 并写好 frontmatter,配置不用动。
const GROUPS = ['开始', '基础', '干活', '产出', '资源']

type Page = { text: string; link: string; order: number; group: string; file: string }

function pages(): Page[] {
  return fs
    .readdirSync(CONTENT)
    // index.md 是首页,README.md 是写给维护者的规矩 —— 都不是说明书的一页。
    .filter((f) => f.endsWith('.md') && !['README.md', 'index.md'].includes(f))
    .map((f) => {
      const raw = fs.readFileSync(path.join(CONTENT, f), 'utf8')
      const front = /^---\n([\s\S]*?)\n---/.exec(raw)?.[1] ?? ''
      const field = (k: string) => new RegExp(`^${k}:\\s*(.+)$`, 'm').exec(front)?.[1]?.trim()
      return {
        text: field('title') ?? f.replace(/\.md$/, ''),
        link: `/${f.replace(/\.md$/, '')}`,
        order: Number(field('order') ?? 999),
        group: field('group') ?? '其他',
        file: f,
      }
    })
    .sort((a, b) => a.order - b.order || a.file.localeCompare(b.file))
}

function sidebar() {
  const all = pages()
  const seen = [...new Set(all.map((p) => p.group))]
  // 认识的组按 GROUPS 排;新起的组排在后面,而不是消失 —— 一页因为组名写错就从
  // 导航里蒸发,是这里最难发现的一种错。
  const order = [...GROUPS.filter((g) => seen.includes(g)), ...seen.filter((g) => !GROUPS.includes(g))]
  return order.map((g) => ({
    text: g,
    items: all.filter((p) => p.group === g).map(({ text, link }) => ({ text, link })),
  }))
}

export default defineConfig({
  lang: 'zh-CN',
  title: '知是 · 使用说明',
  description: '知是是一个你和 AI 队友一起做项目的地方。这份文档讲怎么用它。',
  // 站挂在 okcheese.com/docs 下，不是根 —— 所有内部链接由此生成。
  base: '/docs/',
  srcExclude: ['README.md', 'node_modules/**'],
  // 输出到前端的 public/：Vite 把这个目录原样搬进 dist，nginx 直接就托上了，
  // 不用改 Dockerfile，也不用第二个服务。
  outDir: '../../frontend/public/docs',
  // cleanUrls 生成 /docs/quickstart（没有 .html 后缀）。前端那台 nginx 的
  // try_files 要能补上 .html，见 docs/manual/README.md 的发布那一节。
  cleanUrls: true,
  lastUpdated: true,
  themeConfig: {
    nav: [{ text: '进入知是', link: 'https://okcheese.com/' }],
    sidebar: sidebar(),
    search: { provider: 'local' },
    outline: { level: [2, 3], label: '本页内容' },
    editLink: {
      pattern: 'https://github.com/SageSeekerSociety/cheese/edit/main/docs/manual/:path',
      text: '在 GitHub 上修改这一页',
    },
    docFooter: { prev: '上一页', next: '下一页' },
    lastUpdatedText: '最后更新',
    darkModeSwitchLabel: '主题',
    returnToTopLabel: '回到顶部',
    outlineTitle: '本页内容',
  },
})
