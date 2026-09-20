# PDF

## 产出：typst

```bash
typst compile 报告.typ 报告.pdf
typst compile --format png 报告.typ 预览.png    # 看一页长什么样
```

中文字体已经在 `TYPST_FONT_PATHS` 里。**缺字体时 typst 不报错**，中文会直接变成空心方框，
所以要靠 `--format png` 自己看一眼再交付。

不要绕道「先生成 Word 再转 PDF」：这台机器上没有 LibreOffice，装不上也调不到。用户在界面
上确实能把 Word 显示成 PDF，但那是平台另一个服务在做，不在你的环境里。

## 读：pypdf

```bash
uv run --with pypdf python3 -c "
from pypdf import PdfReader
for page in PdfReader('论文.pdf').pages:
    print(page.extract_text())
"
```

抽不出文字（整页是扫描图）时如实说「这份是扫描件，我读不到里面的文字」，不要编。

## 改 PDF 这件事本身不成立

PDF 是排版结果的快照，不是可编辑的文档——「把第三段那句话改一下」在 PDF 里没有对应的操作，
改出来的东西和原来的排版一定是两回事。所以：

- 用户手里**还有源文件**（.docx / .typ / .tex）时，改源文件再重新导出，并把这一点告诉他。
- 只有 PDF 时，能做的忠实做法是**重新排一份**（typst），并明确说明「这不是原文件改的，
  是新排的，版式会和原来有出入」。不要假装改的是原件。
- 只是要在 PDF 上做标注、加页、合订这类事，也先说清楚做出来的是什么。

**不要**为了「能编辑」而去装工具或转换格式：那些工具在这台机器上没有，绕出来的结果看起来
像 PDF，内容却已经不是原文了。
