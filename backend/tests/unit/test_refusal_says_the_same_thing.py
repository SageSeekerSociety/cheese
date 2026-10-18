"""Both ask routes refuse through one function, so the answer has one shape."""

import json

from app.api.refusal import refuse
from app.core.sentences import say


def test_retry_after_header_only_when_there_is_a_wait():
    assert refuse(429, "slow down", retry_after=7).headers["Retry-After"] == "7"
    assert "Retry-After" not in refuse(429, "slow down", retry_after=0).headers


def test_catalog_sentence_carries_its_i18n_key():
    response = refuse(503, say("assistantNotOpen"), 60)
    body = json.loads(response.body)
    assert response.status_code == 503
    assert body["code"] == 503
    assert body["error"]["i18n"]["key"] == "assistantNotOpen"
    assert body["error"]["message"] == body["message"]


def test_plain_sentence_has_no_error_field():
    body = json.loads(refuse(429, "slow down").body)
    assert body == {"code": 429, "message": "slow down"}
