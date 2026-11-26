"""Fill the prototype page with what a real model says.

Runs one case (default ``a1``) twice, with the prompts on main and with a
variant (default ``memory``). Each run is two steps: 芝士's first message, then
its answer after the person says they did not understand. Writes
``prototype.html`` with those messages in it; nothing 芝士 says on the page is
written by hand.

    uv run --project backend python evals/understanding/prototype.py --model glm-5.2
    uv run --project backend python evals/understanding/prototype.py \\
        --model deepseek-flash@DEEPSEEK --variant rewrite --out /tmp/proto.html

The model is given as in ``run.py`` (``名字`` or ``名字@前缀``). Every run also
lands in ``evals/results/prototype-<time>.json``, so the page can be traced to
its requests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.understanding import run as ev  # noqa: E402
from evals.understanding.cases import by_id  # noqa: E402

TEMPLATE = Path(__file__).with_name("prototype.html")
LABELS = {
    "rules": "改后的规矩",
    "memory": "改后的规矩，并带上这个人的记录",
    "rewrite": "现在的规矩，发出前由另一个芝士改写",
}
FOLLOW = "你问的什么东西，没看懂，再问一次"


async def flow(client, ep, case, variant: str, follow: str) -> dict:
    base = "baseline" if variant == "rewrite" else variant
    first = await ev.agent_turn(client, ep, case, base)
    sent = first["sent"]
    rewrites = []
    if variant == "rewrite" and sent:
        rw = await ev.rewrite(client, ep, case, sent)
        sent, rewrites = rw["sent"], [rw]
    person = case.roster[0].split("（")[0]
    reply = await ev.agent_turn(
        client, ep, case, base, ev.then_says(first, sent, person, follow)
    )
    reply_sent = reply["sent"]
    if variant == "rewrite" and reply_sent:
        rw = await ev.rewrite(client, ep, case, reply_sent)
        reply_sent = rw["sent"]
        rewrites.append(rw)
    return {
        "first": sent,
        "reply": reply_sent,
        "requests": [first["request"], reply["request"]],
        "rewrites": rewrites,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", default="glm-5.2")
    parser.add_argument("--variant", default="memory", choices=sorted(LABELS))
    parser.add_argument("--case", default="a1")
    parser.add_argument("--follow", default=FOLLOW, help="人说没看懂的那句话")
    parser.add_argument(
        "--out", default=None, help="默认 evals/results/prototype-<时间>.html"
    )
    args = parser.parse_args()
    case = by_id([args.case])[0]
    ep = ev.Endpoint.parse(args.model)
    if not ep.ready:
        print(
            "没有模型接口：设好 CHEESE_EVAL_BASE_URL / CHEESE_EVAL_API_KEY 再跑。",
            file=sys.stderr,
        )
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    async with httpx.AsyncClient(timeout=300) as client:
        before = await flow(client, ep, case, "baseline", args.follow)
        after = await flow(client, ep, case, args.variant, args.follow)
    data = {
        "model": ep.model,
        "variant": args.variant,
        "label": LABELS[args.variant],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "flows": {
            "before": {"first": before["first"], "reply": before["reply"]},
            "after": {"first": after["first"], "reply": after["reply"]},
        },
    }
    person = case.roster[0].split("（")[0]
    shown_case = {
        "project": case.project.split("（")[0],
        "title": case.title,
        "person": person,
        "brief": case.brief.split("：", 1)[-1],
        "said": [list(s) for s in case.said],
        "trigger": list(case.trigger),
        "follow": args.follow,
    }
    ev.RESULTS.mkdir(parents=True, exist_ok=True)
    (ev.RESULTS / f"prototype-{stamp}.json").write_text(
        json.dumps(
            {"case": case.id, **data, "full": {"before": before, "after": after}},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    page = TEMPLATE.read_text(encoding="utf-8")
    for tag, value in (("proto-data", data), ("proto-case", shown_case)):
        old = f'<script id="{tag}" type="application/json">null</script>'
        if old not in page:
            raise SystemExit(f"template lost its {tag} slot")
        blob = json.dumps(value, ensure_ascii=False).replace("</", "<\\/")
        page = page.replace(
            old, f'<script id="{tag}" type="application/json">{blob}</script>'
        )
    out = Path(args.out) if args.out else ev.RESULTS / f"prototype-{stamp}.html"
    out.write_text(page, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
