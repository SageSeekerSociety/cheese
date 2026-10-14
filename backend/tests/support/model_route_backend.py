"""One backend process serving the model route (`/llm/v1`) and its stream count.

For tests that need the backend a rollout leaves and the one it moves to as two
processes, the way a box runs them. The model upstream comes from
``MODEL_UPSTREAM``; the only thing stood in for is the key a person's
credential is given, which would otherwise be read from the database.

    python -m tests.support.model_route_backend PORT
"""

import os
import sys

import uvicorn
from fastapi import FastAPI

from app.api.deps import get_chat_service, get_db
from app.api.routes import llm_proxy

llm_proxy.settings.anthropic_base_url = os.environ["MODEL_UPSTREAM"]


async def _person_key(_claims) -> str:
    return "person-key"


llm_proxy._person_key = _person_key


async def _no_database():
    yield None


app = FastAPI()
app.include_router(llm_proxy.router)
app.include_router(llm_proxy.internal_router)
app.dependency_overrides[get_db] = _no_database
app.dependency_overrides[get_chat_service] = lambda: None

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
