"""The example course the teaching pages' screenshots are taken in.

Run against the same local stack as fixture.py (alice is a platform admin,
bobby / carol / david are students), from backend/:

    .venv/bin/python ../docs/site/shots/course_fixture.py

Everything goes through the API the way a teacher and students would: a
course space (reviewed by alice as platform admin), three assignment tasks
(two approved, one left waiting for review), three weekly units, a quiz on
week 1, students enrolling by invite code, approvals, a submission and a
review. Prints the ids shots.mjs needs. Re-running builds a fresh course with
the same name suffixed by the run time, so pick the latest in the UI.
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx

BACKEND = os.environ.get("BACKEND", "http://127.0.0.1:8081")
COURSE = os.environ.get("COURSE_NAME", "程序设计基础（2026 秋）")


def login(name: str) -> dict[str, str]:
    r = httpx.post(f"{BACKEND}/users/auth/login", json={"username": name, "password": "demo12345"})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['data']['accessToken']}"}


def api(method: str, path: str, headers: dict, **kw):
    r = httpx.request(method, f"{BACKEND}{path}", headers=headers, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {r.status_code} {r.text[:400]}")
    if r.status_code == 204 or not r.content:
        return {}
    return r.json().get("data") or {}


def iso(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat()


def ms(days: float) -> int:
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp() * 1000)


def task(t: dict, space: int, name: str, intro: str, desc: str) -> int:
    body = {
        "name": name, "intro": intro, "description": desc,
        "submitterType": "USER", "resubmittable": True, "editable": True,
        "space": space, "participantLimit": 0, "defaultDeadline": 7, "deadline": ms(60),
        "submissionSchema": [{"prompt": "提交文件", "type": "FILE"}],
    }
    tid = api("POST", "/tasks", t, json=body)["task"]["id"]
    # POST /tasks drops submissionSchema today; the publish page means every task
    # to carry one file field, so set it the way PATCH does.
    api("PATCH", f"/tasks/{tid}", t, json={"submissionSchema": body["submissionSchema"]})
    return tid


def participants(t: dict, task_id: int) -> list[dict]:
    d = api("GET", f"/tasks/{task_id}/participants", t)
    return d.get("participants") or d.get("list") or (d if isinstance(d, list) else [])


def main() -> None:
    t = login("alice")
    name = COURSE if len(sys.argv) < 2 else sys.argv[1]
    created = api("POST", "/spaces", t, json={"name": name, "intro": "大一上学期的 C 语言入门课：每周一次作业、一次小测，和芝士一起把题做明白。"})
    space = created["space"]["id"]
    code = (created.get("inviteCode") or {}).get("code")
    api("POST", f"/admin/spaces/{space}/review", t, json={"approved": True, "reason": ""})

    t1 = task(t, space, "第 1 周作业：温度换算", "写一个程序，把摄氏温度换算成华氏温度。", "输入一个摄氏温度（可以是小数），输出对应的华氏温度，保留一位小数。\n\n要求：处理负数；输入不是数字时给出提示。")
    t2 = task(t, space, "第 2 周作业：成绩统计", "读入全班成绩，算出平均分、最高分和不及格人数。", "从标准输入读入 N 个成绩（0–100），输出平均分（保留两位小数）、最高分、不及格人数。")
    t3 = task(t, space, "第 3 周作业：字符串处理", "统计一段英文里每个单词出现的次数。", "读入一段英文，忽略大小写和标点，按出现次数从多到少输出每个单词。")
    for tid in (t1, t2):
        api("PATCH", f"/tasks/{tid}", t, json={"approved": "APPROVED"})

    u1 = api("POST", f"/spaces/{space}/units", t, json={"week": 1, "title": "变量、输入输出与表达式", "summary": "从 printf 和 scanf 开始，写出第一个完整的程序。", "assignmentTaskId": t1, "dueAt": iso(5), "published": True})
    api("POST", f"/spaces/{space}/units", t, json={"week": 2, "title": "分支与循环", "summary": "if、for、while，以及怎么用循环处理一批数据。", "assignmentTaskId": t2, "dueAt": iso(12), "published": False})
    api("POST", f"/spaces/{space}/units", t, json={"week": 3, "title": "数组与字符串", "summary": "一维数组、字符数组和常用的字符串函数。", "published": False})
    unit1 = u1.get("id") or u1.get("unit", {}).get("id")

    quiz = api("POST", f"/spaces/{space}/units/{unit1}/quiz", t, json={"title": "第 1 周小测", "dueAt": iso(4)})
    qid = quiz.get("id") or quiz.get("quiz", {}).get("id")
    qs = [
        {"kind": "SINGLE_CHOICE", "prompt": "下面哪个是 C 语言里读入一个整数的正确写法？", "options": ["scanf(\"%d\", x);", "scanf(\"%d\", &x);", "scanf(x);", "read(x);"], "answer": 1, "points": 2},
        {"kind": "TRUE_FALSE", "prompt": "表达式 7 / 2 在 C 语言里的值是 3.5。", "answer": False, "points": 2},
        {"kind": "FILL_BLANK", "prompt": "输出一个整数并换行，格式串写作 printf(\"____\", n)。", "answer": ["%d\\n"], "points": 2},
        {"kind": "SHORT_ANSWER", "prompt": "用一两句话说明：为什么 scanf 的参数前要加 &？", "answer": "scanf 需要变量的地址才能把读到的值写进去。", "points": 4},
    ]
    for q in qs:
        api("POST", f"/spaces/{space}/quizzes/{qid}/questions", t, json=q)
    qids = [q["id"] for q in api("GET", f"/spaces/{space}/units/{unit1}/quiz", t)["questions"]]

    students = {n: login(n) for n in ("bobby", "carol", "david")}
    for n, h in students.items():
        api("POST", f"/spaces/{space}/enroll", h, json={"code": code})

    parts = participants(t, t1)
    by_user = {}
    for p in parts:
        m = p.get("member") or {}
        by_user[m.get("username") or m.get("name") or str(p.get("id"))] = p
    for n in ("bobby", "carol"):
        p = by_user.get(n)
        if p:
            api("PATCH", f"/tasks/{t1}/participants/{p['id']}", t, json={"approved": "APPROVED", "deadline": ms(6)})

    # carol submits and gets reviewed; bobby submits and waits (待验收)
    out = {"space": space, "code": code, "tasks": [t1, t2, t3], "unit1": unit1, "quiz": qid, "participants": {k: v.get("id") for k, v in by_user.items()}}
    for n, text in (("carol", "温度换算.c"), ("bobby", "temperature.c")):
        p = by_user.get(n)
        if not p:
            continue
        h = students[n]
        src = "#include <stdio.h>\n\nint main(void) {\n    double c;\n    if (scanf(\"%lf\", &c) != 1) { printf(\"请输入数字\\n\"); return 1; }\n    printf(\"%.1f\\n\", c * 9 / 5 + 32);\n    return 0;\n}\n"
        att = api("POST", "/attachments", h, files={"file": (text, src.encode(), "text/x-c")}, data={"type": "file"})
        aid = att.get("id") or att.get("attachment", {}).get("id")
        sub = api("POST", f"/tasks/{t1}/participants/{p['id']}/submissions", h, json=[{"attachmentId": aid}])
        out.setdefault("submissions", {})[n] = sub
    carol = by_user.get("carol")
    if carol:
        subs = api("GET", f"/tasks/{t1}/participants/{carol['id']}/submissions", t)
        items = subs.get("submissions") or subs.get("list") or (subs if isinstance(subs, list) else [])
        if items:
            sid = items[0].get("id")
            api("POST", f"/tasks/{t1}/participants/{carol['id']}/submissions/{sid}/review", t, json={"accepted": True, "score": 95, "comment": "思路清楚，负数和非法输入都处理了。"})

    # bobby takes the quiz
    try:
        api("PUT", f"/spaces/{space}/quizzes/{qid}/my-attempt", students["bobby"], json={"answers": [
            {"questionId": qids[0], "response": 1}, {"questionId": qids[1], "response": False},
            {"questionId": qids[2], "response": "%d\\n"}, {"questionId": qids[3], "response": "因为 scanf 要把值写进变量，需要知道变量在内存里的位置。"}]})
    except RuntimeError as e:
        out["quiz_error"] = str(e)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
