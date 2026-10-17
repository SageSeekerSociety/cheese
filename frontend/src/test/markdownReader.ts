// 消息正文（人的、芝士的）由 Markdown 阅读器画，MarkdownView 第一次画时才去 import
// 它。测试里留给第一条用例去等，这一下 import 在忙的机器上会比 flush() 慢，正文就是空的；
// 在 beforeAll 里先载进来，之后那一下 import 立刻就有。
export async function loadMarkdownReader(): Promise<void> {
  await import('@/components/panels/doc/blocks/reader')
}
