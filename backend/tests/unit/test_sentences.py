"""The platform's sentences: one catalog, read by both ends, and the key survives.

The backend stores a room line as Chinese text plus the key and parameters of
the sentence (`app/core/sentences.py`); a screen renders the key in its
reader's language from the frontend catalog `roomNotice` (an error's sentence
from `apiError`), and the backend renders the same key from the same files for
what reaches one person away from a screen (push, the desktop app). What can go
wrong without anything failing at the moment it happens:

- a sentence lacks a language, or its translation names other placeholders,
  and renders with a hole;
- a call site names a key the catalog does not have, or fills it with the wrong
  parameters, which raises only when that notice is finally said in production.

Every ``say(...)`` in the backend is checked against the catalog, so either
mistake fails at the commit that makes it.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from app.core.sentences import (
    ERROR_MESSAGES,
    HISTORICAL_NOTICE_KEYS,
    I18N_META_KEY,
    LOCALES,
    MESSAGES,
    NOTICE_MESSAGES,
    from_descriptor,
    in_language,
    listing,
    notice_message,
    render,
    say,
    with_keys,
)

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "backend/app"
CATALOG = ROOT / "frontend/src/i18n/messages"


#: Each backend catalog and the frontend namespace that mirrors it.
CATALOGS = [("roomNotice", NOTICE_MESSAGES), ("apiError", ERROR_MESSAGES)]


def _catalog(locale: str, namespace: str) -> dict[str, str]:
    """A locale's catalog, read the way the backend reads it: one file, or the
    shards under a directory merged into the flat table they are."""
    file = CATALOG / locale / f"{namespace}.json"
    if file.is_file():
        return json.loads(file.read_text("utf-8"))
    merged: dict[str, str] = {}
    for shard in sorted((CATALOG / locale / namespace).glob("*.json")):
        for key, value in json.loads(shard.read_text("utf-8")).items():
            assert key not in merged, f"{key} 在两个分片里都有：{shard.name}"
            merged[key] = value
    return merged


def _placeholders(template: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", template))


#: 目录一致性的修法（三处目录/键对齐断言共用）。
_REPAIR_CATALOG = (
    "修法：后端与前端共用一份目录。每个键要同时存在于 frontend/src/i18n/messages/ "
    "每个 locale（zh-CN 与 en）下的 roomNotice/（后端在房间里说的句子）或 "
    "apiError/（后端拒绝时说的句子）——两个都按说出这句话的后端模块分片，"
    "文件名不是键的一部分；"
    "两份 locale 的 {占位符} 集合必须一致，"
    "en 不能有 zh-CN 没有的键，值也不能为空。"
    "规则：docs/i18n.md 第 2 节（目录与命名空间）、第 4 节（缺翻译怎么办）、"
    "第 5 节（机器闸门）。"
    "复查：cd backend && uv run pytest tests/unit/test_sentences.py -q"
)

#: say() 与目录对不上的修法（键不在目录里、或参数不是该键的占位符）。
_REPAIR_SAY = (
    "修法：say(键, ...) 的键必须在目录里，关键字参数名要正好等于该键模板里的 "
    "{占位符} 集合。加新键就在 frontend/src/i18n/messages 的两个 locale 的 "
    "roomNotice/ 或 apiError/ 里同时补中英两份（说到哪个后端域，就在那个域的 "
    "分片里加）；参数对不上就改调用点的关键字，"
    "不要改模板。规则：docs/i18n.md 第 4 节（缺翻译怎么办）、第 5 节（机器闸门）。"
    "复查：cd backend && uv run pytest tests/unit/test_sentences.py -q"
)

#: 孤儿键（目录里有、没有 say() 说过）的修法。
_REPAIR_ORPHAN = (
    "修法：每个现用键都要有源码里的 say(...) 调用点。确实没人用的键，从 "
    "frontend/src/i18n/messages 两个 locale 的 roomNotice/ 里删掉；只该留给旧房间"
    "重放的退役句，把它加进 backend/app/core/sentences.py 的 HISTORICAL_NOTICE_KEYS"
    "（键仍留在目录里，旧房间重放才渲染得出来）；"
    "还在用的句，在说它的地方写 say(键, ...)。"
    "规则：docs/i18n.md 第 4 节、第 5 节。"
    "复查：cd backend && uv run pytest tests/unit/test_sentences.py -q"
)


def _catalog_diff(backend: dict[str, str], catalog: dict[str, str]) -> list[str]:
    """后端 Python 目录与某 locale 的 json 目录逐键比，报出每一处不一致。"""
    out: list[str] = []
    for key in sorted(set(backend) | set(catalog)):
        if key not in catalog:
            out.append(f"  {key}: 只在后端有，json 目录缺这个键")
        elif key not in backend:
            out.append(f"  {key}: 只在 json 目录有，后端不认这个键")
        elif backend[key] != catalog[key]:
            out.append(f"  {key}: 后端={backend[key]!r} json={catalog[key]!r}")
    return out


@pytest.mark.parametrize(("namespace", "templates"), CATALOGS)
def test_the_backend_says_the_frontend_chinese_catalog(namespace, templates):
    diff = _catalog_diff(templates, _catalog("zh-CN", namespace))
    one_file = CATALOG / "zh-CN" / f"{namespace}.json"
    where = f"{namespace}{'.json' if one_file.is_file() else '/'}"
    assert diff == [], (
        f"后端 {namespace} 目录与 frontend/src/i18n/messages/zh-CN/{where} 对不上：\n"
        + "\n".join(diff)
        + "\n"
        + _REPAIR_CATALOG
    )


def test_every_language_a_person_can_pick_has_both_catalogs():
    problems: list[str] = []
    for locale in LOCALES:
        for namespace, templates in CATALOGS:
            catalog = set(_catalog(locale, namespace))
            expected = set(templates)
            if catalog != expected:
                problems.append(
                    f"{locale}/{namespace}.json: 缺 {sorted(expected - catalog)}，"
                    f"多 {sorted(catalog - expected)}"
                )
    assert problems == [], (
        "这些语言的目录键集合与 zh-CN 不一致（运行时回落到中文，但仓库里不允许缺）：\n"
        + "\n".join(problems)
        + "\n"
        + _REPAIR_CATALOG
    )


@pytest.mark.parametrize(("namespace", "templates"), CATALOGS)
def test_every_sentence_has_english_with_the_same_placeholders(namespace, templates):
    english = _catalog("en", namespace)
    problems: list[str] = []
    if set(english) != set(templates):
        problems.append(
            f"en/{namespace}.json 的键集合与 zh-CN 不一致：缺 "
            f"{sorted(set(templates) - set(english))}，"
            f"多 {sorted(set(english) - set(templates))}"
        )
    for key in templates:
        if key in english:
            zh_placeholders = _placeholders(templates[key])
            en_placeholders = _placeholders(english[key])
            if zh_placeholders != en_placeholders:
                problems.append(
                    f"{key}: zh-CN {sorted(zh_placeholders)} != "
                    f"en {sorted(en_placeholders)}"
                )
    assert problems == [], (
        f"en/{namespace}.json 缺中文里的句子，或同一键的占位符对不上——"
        "译文会渲染出 undefined：\n" + "\n".join(problems) + "\n" + _REPAIR_CATALOG
    )


def _say_calls() -> list[tuple[str, int, str | None, set[str] | None]]:
    """Every ``say(...)`` in the app: (file, line, key, keyword names).

    The key is None when it is not a string literal, and the names None when
    the call spreads a mapping."""
    calls = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "say"
            ):
                continue
            first = node.args[0] if node.args else None
            key = (
                first.value
                if isinstance(first, ast.Constant) and isinstance(first.value, str)
                else None
            )
            names = (
                None
                if any(k.arg is None for k in node.keywords)
                else {k.arg for k in node.keywords if k.arg}
            )
            calls.append((str(path.relative_to(ROOT)), node.lineno, key, names))
    return calls


def test_every_say_names_a_sentence_and_fills_exactly_its_placeholders():
    calls = _say_calls()
    assert calls, "no say() call found — is the scan looking in the right place?"
    # A key chosen from a table (`say(_LABEL[kind])`) is checked where it is
    # named, by the test below; its parameters only when the call is made.
    wrong: list[str] = []
    for file, line, key, names in calls:
        if key is None:
            continue
        if key not in MESSAGES:
            wrong.append(f"{file}:{line} say({key!r})：目录里没有这个键 {key!r}")
        elif names is not None and names != _placeholders(MESSAGES[key]):
            wrong.append(
                f"{file}:{line} say({key!r}) 传了 {sorted(names)}，"
                f"而该句的关键字是 {sorted(_placeholders(MESSAGES[key]))}"
            )
    assert wrong == [], (
        "这些 say() 的键或参数与目录对不上（只有在那句话真的被说出来时才会抛错）：\n"
        + "\n".join(wrong)
        + "\n"
        + _REPAIR_SAY
    )


def _unnamed_current_sentences():
    # The catalog/retirement declaration is not a generating call site.
    named = {
        node.value
        for path in APP.rglob("*.py")
        if path != APP / "core/sentences.py"
        for node in ast.walk(ast.parse(path.read_text("utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    return sorted(set(MESSAGES) - HISTORICAL_NOTICE_KEYS - named)


def test_every_sentence_in_the_catalog_is_said_somewhere():
    """Current keys must be named in the app; only explicit historical keys
    survive without a generator so stored descriptors can still be replayed."""
    orphans = _unnamed_current_sentences()
    assert orphans == [], (
        "目录里有、但没有一处 say(...) 说过的键（没人用的键该删；退役留给旧房间重放的"
        "才豁免）：\n" + "\n".join(orphans) + "\n" + _REPAIR_ORPHAN
    )


def test_an_unknown_orphan_is_not_exempted(monkeypatch):
    monkeypatch.setitem(MESSAGES, "unregisteredHistoricalSentence", "旧消息")
    assert _unnamed_current_sentences() == ["unregisteredHistoricalSentence"]
    with pytest.raises(AssertionError, match="unregisteredHistoricalSentence"):
        test_every_sentence_in_the_catalog_is_said_somewhere()


def test_an_orphan_names_its_repair_and_the_doc(monkeypatch):
    """孤儿键红了要有修法：删双方键，或声明进 HISTORICAL_NOTICE_KEYS。"""
    monkeypatch.setitem(MESSAGES, "unregisteredHistoricalSentence", "旧消息")
    with pytest.raises(AssertionError) as caught:
        test_every_sentence_in_the_catalog_is_said_somewhere()
    message = str(caught.value)
    assert "unregisteredHistoricalSentence" in message
    assert "HISTORICAL_NOTICE_KEYS" in message
    assert "roomNotice/" in message
    assert "docs/i18n.md" in message
    assert "test_sentences.py" in message


def test_a_catalog_drift_names_the_key_and_the_localized_files(monkeypatch):
    """后端目录与某个 locale 的 json 不一致时，报出具体键和该动哪些文件。"""
    templates = {"acceptReady": "PR #{pr} 可以合并了，等 {reviewer} 采纳"}
    drifted = {"acceptReady": "PR #{pr} 可以合并了"}
    diff = _catalog_diff(templates, drifted)
    assert diff and "acceptReady" in diff[0]
    assert "json=" in diff[0]
    # 另一头：json 多一个后端不认的键也要报出来。
    extra = _catalog_diff(templates, {**templates, "goneKey": "旧"})
    assert any("goneKey" in line for line in extra)
    assert "roomNotice/" in _REPAIR_CATALOG
    assert "docs/i18n.md" in _REPAIR_CATALOG
    assert "第 5 节" in _REPAIR_CATALOG


def test_a_wrong_placeholder_set_is_named(monkeypatch):
    """同一键中英占位符不一致要报到键上，并给出修法。"""
    monkeypatch.setattr(
        "tests.unit.test_sentences._catalog",
        lambda locale, namespace: (
            {"acceptReady": "PR #{pr} 可以合并了，等 {reviewer} 采纳"}
            if locale == "zh-CN"
            else {"acceptReady": "PR #{pr} is ready to merge"}
        ),
    )
    with pytest.raises(AssertionError) as caught:
        test_every_sentence_has_english_with_the_same_placeholders(
            "roomNotice", {"acceptReady": "PR #{pr} 可以合并了，等 {reviewer} 采纳"}
        )
    message = str(caught.value)
    assert "acceptReady" in message
    assert "reviewer" in message
    assert "占位符" in message
    assert "docs/i18n.md" in message


def test_a_say_key_or_parameter_mismatch_names_the_call_site(monkeypatch):
    """say() 的键不在目录里、或参数不是该键的占位符，要报出 file:line 和修法。"""
    monkeypatch.setattr(
        "tests.unit.test_sentences._say_calls",
        lambda: [
            ("app/example.py", 7, "noSuchSentence", {"x"}),
            ("app/example.py", 9, "acceptReady", {"pr"}),
        ],
    )
    with pytest.raises(AssertionError) as caught:
        test_every_say_names_a_sentence_and_fills_exactly_its_placeholders()
    message = str(caught.value)
    assert "app/example.py:7" in message and "noSuchSentence" in message
    assert "app/example.py:9" in message and "reviewer" in message
    assert "roomNotice/" in message
    assert "test_sentences.py" in message


def test_only_retired_notices_stored_on_old_rooms_are_historical():
    assert HISTORICAL_NOTICE_KEYS == {
        "memoryDreamChanged",
        "memoryDreamRefused",
        "memoryDreamRefusedDetail",
        "labelWhichChanged",
        "labelWhyStopped",
        "deployedTo",
        "deployFailedTo",
        "mainChecksFailed",
        "mainChecksFailedReopened",
        "actorCheese",
        "askAnswerUndelivered",
        "askAnswerUndeliveredDetail",
        "docCommented",
        "docCommentedHandedTo",
        "docCommentMentioned",
        "actionDecision",
        "actionMilestone",
        "taskDispatched",
        "blockUpgradedToTask",
        "blockUpgradedTaskId",
        "blockUpgradedToRoom",
        "blockUpgradedRoomId",
        "labelUpgradedTo",
        "labelRoutineWork",
        "labelRoutineToConfirm",
        "routineProposalConfirmed",
        "routineProposalConfirmedBy",
        "routineProposalDeleted",
        "routineProposed",
        "routineProposedBy",
        "routineSummary",
        "routineScopeUnset",
        "routineOutputRoot",
        "skillProposalNotSaved",
        "skillProposalSaved",
        "skillProposed",
        "skillEditNotSaved",
        "skillEditProposed",
        "skillEditSaved",
        "mailDrafted",
        "labelResult",
        "routineResult",
        "subagentStart",
        "subagentStopEmpty",
        "titleAutoRenamed",
        "computeAnyDevice",
        "computeCloud",
        "computeCloudVm",
        "computeDevice",
        "policyResourceMachine",
        "sandboxReleasedDiskFull",
        "sandboxReleasedOverBudget",
        "spaceTaskRequirementsChanged",
        "turnResumeScheduled",
        "turnFailedRecoversLate",
        "turnFailedRecoversLateHint",
        "turnEndedRateLimited",
        "turnEndedOverloaded",
        "turnEndedStreamEnded",
        "turnEndedUnreachable",
        "turnEndedEgressOffline",
        "turnEndedSubscriptionCooling",
        "turnEndedQuotaReset",
        "turnEndedRunnerReplaced",
        "turnFailedOutOfCredit",
        "turnFailedOutOfCreditHint",
        "turnFailedOwnOutOfCredit",
        "turnFailedOwnOutOfCreditHint",
        "cliProviderUnreachable",
        "cliProviderUnreachableHint",
        "cliProviderOverloaded",
        "cliProviderOverloadedHint",
        "cliSubscriptionEgressOffline",
        "cliSubscriptionEgressOfflineHint",
        "cliControlPlaneUnavailable",
        "cliControlPlaneUnavailableHint",
        "cliSubscriptionAccountsCooling",
        "cliSubscriptionAccountsCoolingHint",
        "cliModelLimitReached",
        "cliModelLimitReachedHint",
        "turnWedged",
        "turnWedgedDetail",
    }
    assert HISTORICAL_NOTICE_KEYS <= NOTICE_MESSAGES.keys()
    assert not HISTORICAL_NOTICE_KEYS & ERROR_MESSAGES.keys()
    assert not {key for _, _, key, _ in _say_calls()} & HISTORICAL_NOTICE_KEYS


@pytest.mark.parametrize(
    ("key", "params", "stored"),
    [
        ("docCommented", {"actor": "ana😀"}, "ana😀 评论了文档"),
        (
            "docCommentedHandedTo",
            {"actor": "ana😀", "seat": "<@cheese-test>"},
            "ana😀 评论了文档，已交给 <@cheese-test>",
        ),
        ("actionDecision", {"actor": "<@cheese-test>"}, "<@cheese-test> 记录了决策"),
        ("actionMilestone", {"actor": "<@cheese-test>"}, "<@cheese-test> 添加了里程碑"),
        ("skillProposed", {"title": "周报"}, "芝士提议新增技能「周报」"),
        (
            "routineProposed",
            {"title": "周报"},
            "芝士起草了「周报」，要你确认后才会执行",
        ),
        ("skillProposalSaved", {"title": "周报"}, "芝士提议的技能「周报」已保存"),
    ],
)
def test_historical_descriptor_replays_without_allowing_generation(key, params, stored):
    descriptor = json.loads(json.dumps({"key": key, "params": params}))
    line = from_descriptor(descriptor)
    assert line == stored
    assert line.descriptor() == descriptor
    meta = {"i18n": {"content": descriptor}}
    assert with_keys(meta, content=stored) == meta
    assert notice_message(meta) == {"message": descriptor}
    assert from_descriptor({"key": key, "params": {}}) is None
    with pytest.raises(ValueError, match="historical notice cannot be generated"):
        say(key, **params)


def test_a_sentence_is_its_chinese_text_and_carries_its_key():
    line = say("acceptReady", pr=12, reviewer="ana")
    assert line == "PR #12 可以合并了，等 ana 采纳"
    meta = with_keys({"event_type": "accept_ready"}, content=line)
    assert meta is not None
    assert meta[I18N_META_KEY]["content"] == {
        "key": "acceptReady",
        "params": {"pr": 12, "reviewer": "ana"},
    }
    assert notice_message(meta) == {"message": meta[I18N_META_KEY]["content"]}


def test_nested_and_list_parameters_are_stored_as_keys():
    detail = say(
        "lines",
        items=[
            say("artifactVersion", name="报告", version=2),
            say("artifactUndelivered", name="数据"),
        ],
    )
    assert detail == "《报告》 第 2 版\n《数据》 尚未交付"
    meta = with_keys({"detail": detail}, content="x")
    assert meta is not None
    assert meta[I18N_META_KEY]["detail"] == {
        "key": "lines",
        "params": {
            "items": [
                {"key": "artifactVersion", "params": {"name": "报告", "version": 2}},
                {"key": "artifactUndelivered", "params": {"name": "数据"}},
            ]
        },
    }


def test_a_list_said_inside_a_sentence_keeps_its_items():
    rooms = listing(["定价", "搜索"], quoted=True)
    line = say("migrationCollisionDetail", rooms=rooms)
    assert line.startswith("另一项改动在：「定价」、「搜索」。")
    assert line.descriptor()["params"]["rooms"] == {
        "list": ["定价", "搜索"],
        "quoted": True,
    }

    # An item may be a sentence of its own; unquoted items are joined bare.
    created = say(
        "taskCreated",
        actor="<@ana>",
        owner="<@ana>",
        title=listing(["周报", say("taskUntitled")]),
    )
    assert "周报、新任务" in created
    assert created.descriptor()["params"]["title"] == {
        "list": ["周报", {"key": "taskUntitled", "params": {}}],
        "quoted": False,
    }


def test_a_list_parameter_survives_json():
    line = say("revisionNumbersMissing", rows=listing([3, 5]), total=4)
    stored = json.loads(json.dumps(line.descriptor(), ensure_ascii=False))

    replayed = from_descriptor(stored)

    assert replayed == line
    assert replayed is not None and replayed.descriptor() == stored


def test_a_key_survives_meta_copied_through_json():
    """`meta` can be serialised on its way to the row (a retry queue, a copy):
    the sentence becomes a plain ``str`` but its key is already in ``i18n``."""
    line = say("acceptReady", pr=3, reviewer="ana")
    meta = with_keys({"detail_label": say("labelNextStep")}, content=line)
    assert meta is not None
    copied = json.loads(json.dumps(meta))
    again = with_keys(copied, content=str(line))
    assert again is not None
    assert again[I18N_META_KEY] == meta[I18N_META_KEY]


@pytest.mark.parametrize("field", ["content", "detail_label"])
def test_a_line_restated_in_other_words_drops_its_old_key(field):
    """An in-place update to text the catalog does not have must not leave the
    old key behind — a screen would render the old sentence over the new."""
    meta = with_keys(
        {"detail_label": say("labelNextStep")},
        content=say("acceptReady", pr=3, reviewer="ana"),
    )
    assert meta is not None
    if field == "content":
        restated = with_keys(meta, content="别的话")
    else:
        restated = with_keys({**meta, "detail_label": "别的话"}, content="x")
    assert restated is not None
    assert field not in restated.get(I18N_META_KEY, {})


def test_a_stored_sentence_is_said_in_the_language_asked_for():
    line = say("acceptReady", pr=12, reviewer="ana")
    stored = json.loads(json.dumps(line.descriptor()))
    assert render(stored, "en") == "PR #12 is ready to merge, waiting for ana to accept"
    assert render(stored, "zh-CN") == "PR #12 可以合并了，等 ana 采纳"


def test_a_nested_sentence_is_said_in_the_same_language():
    detail = say(
        "lines",
        items=[
            say("artifactVersion", name="报告", version=2),
            say("artifactUndelivered", name="数据"),
        ],
    )
    assert render(detail.descriptor(), "en") == (
        "“报告” version 2\n“数据” not delivered yet"
    )


@pytest.mark.parametrize(
    "descriptor",
    [
        {"key": "noSuchSentence", "params": {}},
        {"key": "acceptReady", "params": {"pr": 1}},  # a placeholder unfilled
        {"key": "lines", "params": {"items": [{"key": "noSuchSentence"}]}},
        "not a descriptor",
        None,
    ],
)
def test_what_cannot_be_said_is_left_to_the_stored_text(descriptor):
    assert render(descriptor, "en") is None


@pytest.mark.parametrize("locale", [None, "", "fr", "zh"])
def test_a_language_nobody_can_pick_is_not_guessed(locale):
    line = say("acceptReady", pr=12, reviewer="ana")
    assert render(line.descriptor(), locale) is None
    assert in_language(line, locale) == "PR #12 可以合并了，等 ana 采纳"


@pytest.mark.parametrize(
    ("items", "quoted", "english", "chinese"),
    [
        (["a"], False, "a", "a"),
        (["a", "b"], False, "a and b", "a、b"),
        (["a", "b", "c"], True, "“a”, “b”, and “c”", "「a」、「b」、「c」"),
    ],
)
def test_a_listing_is_joined_the_way_the_screen_joins_it(
    items, quoted, english, chinese
):
    """The frontend joins with ``Intl.ListFormat`` (English long, Chinese
    narrow) and quotes with ``global.listItemQuoted``; a push says the same."""
    line = say("pushInRoom", room=listing(items, quoted=quoted))
    stored = json.loads(json.dumps(line.descriptor()))
    assert render(stored, "en") == f"In “{english}”"
    assert render(stored, "zh-CN") == f"在「{chinese}」"
    assert str(line) == f"在「{chinese}」"
