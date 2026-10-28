"""Each call a person's 芝士 makes says what it acted on, by name, so its step
shows that rather than the reference it was given: what the backend named,
or what the call's own words name — a search's words, a help page, a site."""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_names_steps", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class Host:
    def __init__(self, answers):
        self.environ = {"CHEESE_TOPIC": "", "CHEESE_PROJECT": ""}
        self.answers = answers
        self.doc_versions: dict = {}

    def request(self, plan):
        path = plan["path"].split("?", 1)[0]
        return {"data": self.answers[path]}


def _named(tool, args, answers) -> str:
    said = cheese.run_platform_tool(tool, args, Host(answers), delegated=True)
    return said.subject


def test_a_read_says_what_the_backend_named():
    answers = {
        "/assistant/agent/read": {"text": "…", "subject": "空间「操作系统」的成员"}
    }
    assert _named("cheese_read", {"ref": "spaces/3/people"}, answers) == (
        "空间「操作系统」的成员"
    )


def test_a_read_of_the_problems_the_person_took_says_so():
    assert _named("cheese_read", {"ref": "me"}, {"/tasks/joined": []}) == "你领取的题目"


def test_a_help_search_says_its_words_and_a_help_page_its_title():
    searched = _named(
        "cheese_help", {"query": "删除题目"}, {"/docs/agent/search": {"hits": []}}
    )
    page = _named(
        "cheese_help",
        {"page": "accept"},
        {
            "/docs/agent/read": {
                "page": "accept",
                "markdown": "…",
                "title": "验收与采纳",
            }
        },
    )
    assert searched == "帮助文档：删除题目"
    assert page == "帮助文档「验收与采纳」"


def test_a_page_fetched_is_named_by_its_site_and_the_help_index_as_such():
    def fetched(url):
        answers = {
            "/assistant/agent/review": {"allowed": True},
            "/assistant/agent/plan": {
                "calls": [{"method": "POST", "path": "/fetch", "body": {"url": url}}],
                "said": "",
            },
            "/fetch": {"ok": True, "text": "正文"},
        }
        return _named("cheese_web", {"action": "fetch", "url": url}, answers)

    assert fetched("https://docs.okcheese.com/llms.txt") == "帮助文档目录"
    assert fetched("https://example.edu/syllabus") == "example.edu"
