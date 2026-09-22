"""SRP-6a server implementation — thin wrapper around the `srp_rs` Rust extension.

The Rust extension (srp_rs) is a PyO3 binding of the same SRP-6a logic used in
`@ruc-cheese/node-srp-rs`, ensuring byte-exact compatibility with the
`secure-remote-password` JS frontend library.
"""

import re

# srp_rs is a compiled Rust extension (PyO3) with no type stubs, so pyright can't
# see its exported functions — they exist at runtime (verified via dir(srp_rs)).
import srp_rs

__all__ = ["generate_server_ephemeral", "is_srp_hex", "verify_session"]

_HEX_BYTES = re.compile(r"(?:[0-9a-fA-F]{2})+")


def is_srp_hex(value: str) -> bool:
    """Whether ``value`` is hex the SRP functions accept: whole, non-empty bytes."""
    return _HEX_BYTES.fullmatch(value) is not None


def generate_server_ephemeral(verifier_hex: str) -> tuple[str, str]:
    """Return ``(public_hex, secret_hex)``; raises ValueError on malformed hex."""
    return srp_rs.generate_server_ephemeral(verifier_hex)  # type: ignore[attr-defined]


def verify_session(
    *,
    server_secret_hex: str,
    client_public_hex: str,
    salt_hex: str,
    username: str,
    verifier_hex: str,
    client_proof_hex: str,
) -> tuple[bool, str]:
    """Return ``(success, server_proof_hex)``.

    A value that is not valid hex is a failed proof like any other: the client
    supplies most of them, and it must get the same answer, and spend the same
    attempt, as it would for a wrong password.
    """
    try:
        return srp_rs.verify_session(  # type: ignore[attr-defined]
            server_secret_hex,
            client_public_hex,
            salt_hex,
            username,
            verifier_hex,
            client_proof_hex,
        )
    except ValueError:
        return False, ""
