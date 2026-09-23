"""What an account-creation request has to carry since #1486: consent to the
current version of every legal document. Built from the registry, so a new
version does not have to be copied into every test that creates an account."""

from app.domain.legal.documents import DOCUMENTS

CURRENT_VERSIONS = {key: doc.current.version for key, doc in DOCUMENTS.items()}

#: The ``consent`` field of ``POST /users``.
SIGNUP_CONSENT = {"documents": CURRENT_VERSIONS, "method": "checkbox"}

#: The consent fields of the ``POST /users/oauth/create`` form.
OAUTH_CONSENT_FORM = {
    "consentTerms": CURRENT_VERSIONS["terms"],
    "consentPrivacy": CURRENT_VERSIONS["privacy"],
    "consentMethod": "checkbox",
}
