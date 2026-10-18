"""The headers that make a served file inert in the app's own origin.

Several routes hand back bytes a user supplied: `/uploads/…` (the local storage
backend's URLs) and a room file's `/topics/{id}/attachments/raw`. The browser is
on this origin, and the frontend keeps its JWT in localStorage, so an HTML or
SVG document loaded as a document would run with the reader's session behind it.
Two headers stop that: `nosniff`, so the browser does not go looking for a
better content type than the one we sent, and a CSP that denies every capability
and sandboxes the document.

They are written once, here, because a rule copied into each response is a rule
that can disagree with itself: when one copy learns something the others do not
have, nothing points at the other copies. A response that renders a page on
purpose — the office preview — takes `NO_SNIFF` and adds its own policy, because
that page has to be allowed its own inline script and style.
"""

# Deny every capability an HTML document could ask for, and sandbox it besides,
# so even a mis-typed response cannot reach the session it was served next to.
INERT_CSP = "default-src 'none'; sandbox"

# One field of the pair, for responses whose policy is their own (`_PAGE_POLICY`
# in `routes/topics_attachments.py`): `nosniff` does not change with it.
NO_SNIFF = {"X-Content-Type-Options": "nosniff"}

# The whole of it, for a response that must be inert as-is.
INERT_HEADERS = {**NO_SNIFF, "Content-Security-Policy": INERT_CSP}
