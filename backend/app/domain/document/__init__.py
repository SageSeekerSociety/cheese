"""项目文档树 (project document library) — 知是 2.0 (§5).

文档即节点:一个 Document 是文档树里的一个节点,任何文档都可以有子文档(Notion/飞书
风格),没有单独的「文件夹」类型。文档正文是活文档 (living doc),落在 `block` substrate
上:每个 Document 拥有一个 DOC_ROOT 块(整篇 markdown)+ 若干 DOC_NODE 子块(每个顶层
markdown 块一个,靠 struct_parent_id + struct_order 保持稳定 id,供评论/引用锚定)。
"""
