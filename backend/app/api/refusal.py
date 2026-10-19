"""The refusal answer the two ask-the-assistant routes share.

A refusal is ``{code, message}`` plus, when the message is a catalog sentence,
``error = {message, i18n}`` so the browser renders it in its reader's language.
``Retry-After`` is sent only when there is a wait to report."""

from fastapi.responses import JSONResponse

from app.core.errors import message_key


def refuse(status: int, message: str, retry_after: int = 0) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    body: dict = {"code": status, "message": message}
    key = message_key(message)
    if key is not None:
        # A catalog sentence: the browser renders it in its reader's language.
        body["error"] = {"message": message, "i18n": key}
    return JSONResponse(body, status_code=status, headers=headers)
