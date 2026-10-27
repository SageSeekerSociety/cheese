"""The documentation sites a person's own 芝士 reads with no look by its gate.

Claude Code's auto mode reads these without asking (``isPreapprovedUrl``,
Claude Code 2.1.306); we take its list as it is, so 芝士 asks no more than
Claude Code does. Any other page still goes through the gate, because its
address can carry what 芝士 read out to the host it names
(``cheese_web.fetch`` reaches others, ``sandbox/cheese``).

An entry with a path covers that path and what lies under it on that host
alone. On GitHub only Anthropic's own repositories count, and under
``anthropic-experimental`` and on raw.githubusercontent.com only a
repository's main branch.

One difference stays: Claude Code does not follow a redirect off the address
it was given, while our fetch follows redirects to any public host
(``fetch.layers``). A listed site that redirected to an address its visitor
names would carry the query past the gate; none of them was found to.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: Copied from Claude Code 2.1.306 in its order, so a later copy diffs cleanly;
#: its second ``learn.microsoft.com`` is left out.
SITES = (
    "platform.claude.com",
    "code.claude.com",
    "claude.dev",
    "www.claude.dev",
    "claude.com/docs",
    "modelcontextprotocol.io",
    "github.com/anthropics",
    "github.com/anthropic-experimental",
    "raw.githubusercontent.com/anthropics",
    "raw.githubusercontent.com/anthropic-experimental",
    "agentskills.io",
    "docs.python.org",
    "en.cppreference.com",
    "docs.oracle.com",
    "learn.microsoft.com",
    "developer.mozilla.org",
    "go.dev/doc",
    "go.dev/ref",
    "www.php.net",
    "docs.swift.org",
    "kotlinlang.org",
    "ruby-doc.org",
    "doc.rust-lang.org",
    "www.typescriptlang.org",
    "react.dev",
    "angular.io",
    "vuejs.org",
    "nextjs.org",
    "expressjs.com",
    "nodejs.org",
    "bun.sh",
    "jquery.com",
    "getbootstrap.com",
    "tailwindcss.com",
    "d3js.org",
    "threejs.org",
    "redux.js.org",
    "webpack.js.org",
    "jestjs.io",
    "reactrouter.com",
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "pandas.pydata.org",
    "numpy.org",
    "www.tensorflow.org",
    "pytorch.org",
    "scikit-learn.org",
    "matplotlib.org",
    "requests.readthedocs.io",
    "jupyter.org",
    "laravel.com",
    "symfony.com",
    "wordpress.org/documentation",
    "docs.spring.io",
    "hibernate.org",
    "tomcat.apache.org",
    "gradle.org",
    "maven.apache.org",
    "asp.net",
    "dotnet.microsoft.com",
    "blazor.net",
    "reactnative.dev",
    "docs.flutter.dev",
    "developer.apple.com",
    "developer.android.com",
    "keras.io",
    "spark.apache.org",
    "huggingface.co/docs",
    "www.kaggle.com/docs",
    "www.mongodb.com",
    "redis.io",
    "www.postgresql.org",
    "dev.mysql.com",
    "www.sqlite.org",
    "graphql.org",
    "prisma.io",
    "docs.getdbt.com",
    "docs.aws.amazon.com",
    "cloud.google.com",
    "kubernetes.io",
    "www.docker.com",
    "www.terraform.io",
    "www.ansible.com",
    "vercel.com/docs",
    "docs.stripe.com",
    "docs.netlify.com",
    "devcenter.heroku.com",
    "dev.wix.com/docs",
    "cypress.io",
    "selenium.dev",
    "docs.unity.com",
    "docs.unrealengine.com",
    "git-scm.com",
    "nginx.org",
    "httpd.apache.org",
)

_WHOLE_HOSTS = frozenset(site for site in SITES if "/" not in site)
_PATHS: dict[str, list[str]] = {}
for _site in SITES:
    if "/" in _site:
        _host, _, _path = _site.partition("/")
        _PATHS.setdefault(_host, []).append("/" + _path)

_GITHUB = frozenset({"github.com", "raw.githubusercontent.com"})
#: An escaped slash, backslash or dot could climb out of the covered path.
_ESCAPED = re.compile(r"%(25)*(2f|5c|2e)", re.IGNORECASE)
_MAIN_REPO = re.compile(r"^/[^/]+/[^/]+(/?|/(tree|blob)/main(/.*)?)$")
_MAIN_RAW = re.compile(r"^/[^/]+/[^/]+/refs/heads/main/.")


def _main_branch(host: str, path: str) -> bool:
    if host == "raw.githubusercontent.com":
        return bool(_MAIN_RAW.match(path))
    return bool(_MAIN_REPO.match(path))


def preapproved(url: str) -> bool:
    """Whether ``url`` is on one of :data:`SITES`."""
    # The browser rung reads the address as a browser does, not as urlsplit:
    # a backslash is a slash to it, so ``https://evil.example\@docs.python.org/``
    # goes to evil.example. Credentials, a backslash, whitespace or any scheme
    # but http(s) leave the address to the gate.
    if "\\" in url or any(c.isspace() or ord(c) < 0x20 for c in url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or "@" in parts.netloc:
        return False
    path = parts.path or "/"
    if host in _WHOLE_HOSTS:
        return True
    prefixes = _PATHS.get(host)
    # A browser resolves ``..`` before sending, so ``/anthropics/../x`` is
    # not under ``/anthropics``.
    dots = any(segment in (".", "..") for segment in path.split("/"))
    if not prefixes or dots or _ESCAPED.search(path):
        return False
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            return (
                host not in _GITHUB
                or host + prefix == "github.com/anthropics"
                or _main_branch(host, path)
            )
    return False
