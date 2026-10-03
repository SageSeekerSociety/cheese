"""Searching a room's checkout for lines that carry given keywords: the
executor's `repo_search`, which answers whether a fact is written in the repo.

Shipped beside `runtime.py` (`RELEASE_FILES`), standard library only, and run
by Pythons as old as 3.9.
"""

from __future__ import annotations

import subprocess


def search(params, root):
    """Find lines in this room's checkout carrying ALL the given keywords.

    What it is for: 记忆只记 repo 里查不到的东西（结论 61），and the only
    place that can answer "is it in the repo" is the machine holding the
    checkout — the platform has no copy of it. Tracked files and untracked
    ones both count: a fact written in a file that is not committed yet is
    still written down somewhere a person will read.

    ``--and`` between the keywords, not one ``-e`` each: OR is how this
    fails in exactly the repo it matters in. 「前端构建用 pnpm，不要用 npm」
    carries the keyword ``npm``, which in a real frontend repo is in the
    lockfile, every `package.json`, CI and half of `docs/` — the caller's
    window fills up in path order and the one line that actually states the
    fact never arrives. Which keywords to AND is the platform's call; this
    answers the question it was handed.

    ``searched`` is the first field, not a convenience: 「repo 里确实没写」
    and 「这次没查成」 are opposite answers. A caller that cannot tell them
    apart keeps accepting writes on a machine where this never runs, with
    nothing anywhere saying so.
    """
    terms = [
        term
        for term in params.get("terms", [])
        if isinstance(term, str) and term.strip()
    ][:12]  # a bound on argv, not the choice of keywords
    if not terms:
        return {"searched": False, "reason": "no-terms", "hits": []}
    argv = [
        "git",
        "-C",
        str(root),
        "grep",
        "--no-color",
        "-n",  # line numbers: the refusal names a place, not just a file
        "-I",  # never a binary
        "-F",  # the keywords are literals, not patterns
        "-i",
        "--untracked",
    ]
    for index, term in enumerate(terms):
        argv += (["--and"] if index else []) + ["-e", term]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"searched": False, "reason": f"did not run: {exc}", "hits": []}
    # 1 = 一行都没匹配上，那是一个答案。其余都是没查成：这个仓库不是仓库、
    # 这台机器上没有 git、这个 git 不认某个选项。说出来——「从此一条都拦不住
    # 而日志里一个字都没有」是这套东西最坏的坏法，因为它看起来跟一切正常一样。
    if result.returncode not in (0, 1):
        stderr = result.stderr.decode("utf-8", "replace").strip()[:200]
        return {
            "searched": False,
            "reason": f"exit {result.returncode}: {stderr}",
            "hits": [],
        }
    hits = []
    for line in result.stdout.decode("utf-8", "replace").splitlines()[:200]:
        path, _, rest = line.partition(":")
        number, _, text = rest.partition(":")
        if not number.isdigit():
            continue
        hits.append({"path": path, "line": int(number), "text": text[:400]})
    return {"searched": True, "hits": hits}
