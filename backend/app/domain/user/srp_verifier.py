"""Check a plaintext password against a stored ``SRP:{salt}:{verifier}`` record.

The record was produced in the browser by ``secure-remote-password`` 0.3.1
(``derivePrivateKey`` then ``deriveVerifier``), with the account's username as
the identity string. Recomputing the verifier here lets an SRP account sign in
with its plaintext password so its credential can be rewritten as bcrypt.
"""

import hashlib
import hmac

# RFC 5054 2048-bit group, g = 2 — the library's ``lib/params.js``.
_N = int(
    "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050"
    "A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50"
    "E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B8"
    "55F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773B"
    "CA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748"
    "544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6"
    "AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB6"
    "94B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73",
    16,
)
_G = 2
_N_BYTES = (_N.bit_length() + 7) // 8


def srp_password_matches(stored: str, username: str, password: str) -> bool:
    """Whether ``password`` derives the verifier in ``stored``.

    x = H(salt_bytes || H(utf8(username ":" password))), v = g^x mod N. The
    salt is hashed as the bytes of its hex string, leading zero bytes
    included. A malformed record is a mismatch.
    """
    parts = stored.split(":", 2)
    if len(parts) != 3 or parts[0] != "SRP":
        return False
    try:
        salt = bytes.fromhex(parts[1])
        verifier = int(parts[2], 16)
    except ValueError:
        return False
    if not salt or verifier <= 0 or verifier >= _N:
        return False

    inner = hashlib.sha256(f"{username}:{password}".encode()).digest()
    x = int.from_bytes(hashlib.sha256(salt + inner).digest(), "big")
    derived = pow(_G, x, _N)
    return hmac.compare_digest(
        derived.to_bytes(_N_BYTES, "big"), verifier.to_bytes(_N_BYTES, "big")
    )
