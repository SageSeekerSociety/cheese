"""SRP-6a server implementation — thin wrapper around the `srp_rs` Rust extension.

The Rust extension (srp_rs) is a PyO3 binding of the same SRP-6a logic used in
`@ruc-cheese/node-srp-rs`, ensuring byte-exact compatibility with the
`secure-remote-password` JS frontend library.
"""

# srp_rs is a compiled Rust extension (PyO3) with no type stubs, so pyright can't
# see its exported functions — they exist at runtime (verified via dir(srp_rs)).
from srp_rs import (
    generate_server_ephemeral,  # type: ignore[attr-defined]
    verify_session,  # type: ignore[attr-defined]
)

__all__ = ["generate_server_ephemeral", "verify_session"]
