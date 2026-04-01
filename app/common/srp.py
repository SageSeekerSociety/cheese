"""SRP-6a server implementation — thin wrapper around the `srp_rs` Rust extension.

The Rust extension (srp_rs) is a PyO3 binding of the same SRP-6a logic used in
`@ruc-cheese/node-srp-rs`, ensuring byte-exact compatibility with the
`secure-remote-password` JS frontend library.
"""

from srp_rs import generate_server_ephemeral, verify_session

__all__ = ["generate_server_ephemeral", "verify_session"]
