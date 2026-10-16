"""Which upstream URLs are GitHub repositories, and so can be accepted by PR.

The platform's own route and the review clients both need this reading, and the
answer is a property of the upstream a project records, not of any one client.
"""

from app.domain.project.github_app import parse_github_repo


def test_parse_accepts_https_github_shapes():
    assert parse_github_repo("https://github.com/acme/widgets") == ("acme", "widgets")
    assert parse_github_repo("https://github.com/acme/widgets.git") == (
        "acme",
        "widgets",
    )
    assert parse_github_repo("https://github.com/acme/widgets/") == ("acme", "widgets")


def test_parse_rejects_everything_else():
    # SSH is excluded on purpose: installation tokens only work over https.
    assert parse_github_repo("git@github.com:acme/widgets.git") is None
    assert parse_github_repo("https://gitlab.com/acme/widgets") is None
    assert parse_github_repo("/home/repos/widgets") is None
    assert parse_github_repo(None) is None
    assert parse_github_repo("") is None
