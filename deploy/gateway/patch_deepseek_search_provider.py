"""Register DeepSeek as a search provider (`deepseek_search.py`).

LiteLLM 1.103.3 resolves a search tool's `search_provider` through a closed
enum (`SearchProviders`) and a fixed map from it to a config class
(`ProviderConfigManager.get_provider_search_config`); a provider outside both
is "Search is not supported for provider". This adds `deepseek` to each,
pointing at `cheese_deepseek_search.DeepSeekSearchConfig`.
"""

import hashlib
from pathlib import Path

SITE = Path("/app/.venv/lib/python3.13/site-packages/litellm")


def patch(relative: str, digest: str, old: bytes, new: bytes) -> None:
    path = SITE / relative
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == digest, (
        f"Review LiteLLM's search provider registry in {relative} before updating this patch"
    )
    assert source.count(old) == 1, relative
    path.write_bytes(source.replace(old, new))


patch(
    "types/utils.py",
    "9da58881a911ffb4bfafcb2bda1f33256209c8a8d89fd80163609ec2fb90e59d",
    b'    BING_GROUNDING = "bing_grounding"\n',
    b'    BING_GROUNDING = "bing_grounding"\n    DEEPSEEK = "deepseek"\n',
)
patch(
    "utils.py",
    "0002b29053e16961f7aa26ad86db4b371915fbce7ad3d55a45688189059ea3a2",
    b"            SearchProviders.BING_GROUNDING: BingGroundingSearchConfig,\n",
    b"            SearchProviders.BING_GROUNDING: BingGroundingSearchConfig,\n"
    b"            SearchProviders.DEEPSEEK: __import__(\n"
    b'                "cheese_deepseek_search"\n'
    b"            ).DeepSeekSearchConfig,\n",
)
