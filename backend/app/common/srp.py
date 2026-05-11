"""SRP-6a server implementation — thin wrapper around the `srp_rs` Rust extension.

The Rust extension (srp_rs) is a PyO3 binding of the same SRP-6a logic used in
`@ruc-cheese/node-srp-rs`, ensuring byte-exact compatibility with the
`secure-remote-password` JS frontend library.

Building srp_rs requires a Rust toolchain (rustc + cargo). On Windows, MSVC
Build Tools are also needed. Install Rust from https://rustup.rs/ then run
`uv sync` to build from source.
"""

try:
    from srp_rs import generate_server_ephemeral, verify_session
except ImportError as e:
    raise ImportError(
        "Failed to import srp_rs. Ensure the Rust toolchain is installed "
        "(https://rustup.rs/) and run `uv sync` to build the extension. "
        "On Windows, MSVC Build Tools are also required."
    ) from e

__all__ = ["generate_server_ephemeral", "verify_session"]
