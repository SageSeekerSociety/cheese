"""实况文档写入时的检查。

**写入照样成功。** 一份文档写歪了不该被平台拦下来——拦下来等于让人（或芝士）
先猜格式再写字，而它当场就能改；拒掉一次正当的更新，代价比一条警告大得多。
所以这里只回答一个问题：**这份正文哪些地方不像「现在的状态」**，以及每一处
当场该怎么改。检查跑在服务端（`PUT /documents/{id}`），警告跟着响应回到写它
的人手里，`cheese_doc_set` 再把它拼进工具返回。

检查是纯函数，同样的正文永远给同样的警告：提示词每轮重写、工具返回要能对着测，
一个会随环境变脸的检查两样都做不到。
"""

import re

#: 一份实况文档的写作预算（字符）。超了照样写入，只提醒：它是一页状态页，每轮
#: 都注入，要一分钟读完。
LIVING_DOC_CHAR_LIMIT = 1500

#: 一个代码块超过这些行、或者这么多字符，就当作「把命令输出/日志原样贴进来了」。
_PASTED_CODE_LINES = 40
_PASTED_CODE_CHARS = 1500

#: 连续这么多行以日期开头，就是流水账而不是状态。
_DATE_RUN_LINES = 3
#: 连续这么多行以 `$ ` / `[handle]:` 开头，就是粘贴的终端或聊天。
_PASTE_RUN_LINES = 3

_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*(.+?)[ \t]*$")
_DATE_LINE_RE = re.compile(
    r"^[ \t]*(?:[-*>][ \t]*)?(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}月\d{1,2}日)"
)
#: 追加式段落。文档记「现在成立的状态」，一条说法被推翻就改掉它本身，不是
#: 在旁边再追加一条更正——读者得自己推断哪一版有效。
_APPEND_WORDS = "更正|更正一下|补充|补充说明|附注|追加"
_APPEND_RE = re.compile(
    r"(?m)^[ \t]*(?:[-*>][ \t]*)?(?:\*\*)?(?:" + _APPEND_WORDS + r")[：:]"
)
_CHAT_LINE_RE = re.compile(r"^[ \t]*\[[^\]\n]{1,40}\][：:][ \t]*")
#: 不收 `>`：那是 Markdown 的引用块，连着几行引用是正常的文档写法。
_SHELL_LINE_RE = re.compile(r"^[ \t]*(?:\$|❯)[ \t]+\S")
_FENCE_RE = re.compile(r"(?m)^[ \t]*(```|~~~)")


def _runs(lines: list[str], matches) -> int:
    """最长的一段连续命中行数。"""
    longest = run = 0
    for line in lines:
        if matches(line):
            run += 1
            longest = max(longest, run)
        elif line.strip():
            run = 0
    return longest


def pasted_code_blocks(content: str) -> list[tuple[int, int]]:
    """每个代码块的 ``(行数, 字符数)``，按出现顺序。没有就是空表。

    围栏没配对时（奇数个）最后一段按到文末算：一份贴了一半的日志照样是贴。
    """
    fences = list(_FENCE_RE.finditer(content))
    blocks = []
    for start, end in zip(fences[::2], fences[1::2], strict=False):
        body = content[start.end() : end.start()]
        blocks.append((len(body.splitlines()), len(body)))
    if len(fences) % 2 == 1:
        body = content[fences[-1].end() :]
        blocks.append((len(body.splitlines()), len(body)))
    return blocks


def living_doc_warnings(content: str) -> list[str]:
    """这份正文哪里不像状态；干净时是空表。

    每条都带着「当场怎么改」，因为警告的用处就是让人此刻动手——一条只说
    「有问题」的警告，读者除了忽略它没有别的选择。
    """
    warnings: list[str] = []
    if len(content) > LIVING_DOC_CHAR_LIMIT:
        warnings.append(
            f"正文 {len(content)} 字，超过 {LIVING_DOC_CHAR_LIMIT} 字的写作预算。"
            "删掉做完的事、过程和证据细节，只留目标、现状、分工、已确定的事和待决；"
            "细节留在聊天、任务卡和 PR 里。"
        )

    headings = [m.group(1) for m in _HEADING_RE.finditer(content)]
    logs = [h for h in headings if "进展日志" in h or "时间线" in h]
    if logs:
        warnings.append(
            "小节《" + "》《".join(logs) + "》是流水账：文档记状态，不记过程。"
            "把这几天发生了什么留在聊天或结论卡里，正文只留「现在成立」的那几条。"
        )

    lines = content.splitlines()
    date_run = _runs(lines, lambda line: bool(_DATE_LINE_RE.match(line)))
    if date_run >= _DATE_RUN_LINES:
        warnings.append(
            f"有连续 {date_run} 行以日期开头——那是日志，不是状态。"
            "把每个日期对应的最新状态合并成一句，旧日期连同它那一行一起删掉。"
        )

    appended = len(_APPEND_RE.findall(content))
    if appended:
        warnings.append(
            f"有 {appended} 处「更正：」「补充：」这样的追加段。"
            "直接改掉上面那条已经不成立的说法，不要在旁边再挂一条更正——"
            "读者不该自己去推断哪一版有效。"
        )

    chat_run = _runs(lines, lambda line: bool(_CHAT_LINE_RE.match(line)))
    if chat_run >= _PASTE_RUN_LINES:
        warnings.append(
            f"有连续 {chat_run} 行像是粘贴的聊天记录。文档不复制原文，"
            "只留结论和指向证据的链接。"
        )

    shell_run = _runs(lines, lambda line: bool(_SHELL_LINE_RE.match(line)))
    if shell_run >= _PASTE_RUN_LINES:
        warnings.append(
            f"有连续 {shell_run} 行像是粘贴的命令与输出。文档不复制终端，"
            "只留结论和 `<&路径>` 这类指回证据的引用。"
        )

    for line_count, char_count in pasted_code_blocks(content):
        if line_count >= _PASTED_CODE_LINES or char_count >= _PASTED_CODE_CHARS:
            warnings.append(
                f"有一个 {line_count} 行、约 {char_count} 字的代码块。"
                "大段粘贴的代码或输出搬到仓库或交付产物里，文档里只留结论和引用。"
            )

    return warnings
