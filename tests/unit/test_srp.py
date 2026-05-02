"""Tests for the srp_rs Rust SRP-6a binding.

Simulates the full client-server SRP flow using the same math
as the secure-remote-password JS library to verify compatibility.
"""

import hashlib
import os

import pytest

from srp_rs import generate_server_ephemeral, verify_session

# ---- Minimal SrpInteger that matches Rust/JS hex_length semantics ----

N_HEX = (
    "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050"
    "A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50"
    "E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B8"
    "55F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773B"
    "CA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748"
    "544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE"
    "6AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB"
    "694B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73"
)


class _SI:
    """Tiny SrpInteger mimic for client-side test math."""

    __slots__ = ("v", "hl")

    def __init__(self, v: int, hl: int) -> None:
        self.v = v
        self.hl = hl

    @classmethod
    def hex(cls, h: str) -> "_SI":
        return cls(int(h, 16), len(h))

    def to_hex(self) -> str:
        h = format(self.v, "x")
        return "0" * max(0, self.hl - len(h)) + h

    def to_bytes(self) -> bytes:
        return bytes.fromhex(self.to_hex())

    def mod_pow(self, exp: "_SI", m: "_SI") -> "_SI":
        return _SI(pow(self.v, exp.v, m.v), m.hl)

    def mul(self, o: "_SI") -> "_SI":
        return _SI(self.v * o.v, self.hl)

    def add(self, o: "_SI") -> "_SI":
        return _SI(self.v + o.v, self.hl)

    def sub(self, o: "_SI") -> "_SI":
        return _SI(self.v - o.v, self.hl)

    def mod(self, m: "_SI") -> "_SI":
        r = self.v % m.v
        return _SI(r + m.v if r < 0 else r, m.hl)

    def xor(self, o: "_SI") -> "_SI":
        a, b = self.to_bytes(), o.to_bytes()
        mx = max(len(a), len(b))
        a = b"\x00" * (mx - len(a)) + a
        b = b"\x00" * (mx - len(b)) + b
        return _SI(int(bytes(x ^ y for x, y in zip(a, b)).hex(), 16), self.hl)


def _H(*args: _SI) -> _SI:
    h = hashlib.sha256()
    for a in args:
        h.update(a.to_bytes())
    return _SI(int(h.hexdigest(), 16), 64)


def _H_str(s: str) -> _SI:
    return _SI(int(hashlib.sha256(s.encode()).hexdigest(), 16), 64)


# Precomputed params
_N = _SI.hex(N_HEX)
_g = _SI.hex("02")
_k = _H(_N, _g)


def _client_register(username: str, password: str) -> tuple[str, str]:
    """Simulate JS client registration → (salt_hex, verifier_hex)."""
    salt_hex = os.urandom(32).hex()
    s = _SI.hex(salt_hex)
    x = _H(s, _H_str(f"{username}:{password}"))
    v = _g.mod_pow(x, _N)
    return salt_hex, v.to_hex()


def _client_prove(
    username: str,
    password: str,
    salt_hex: str,
    server_public_hex: str,
) -> tuple[str, str, _SI]:
    """Simulate JS client login → (A_hex, M1_hex, K)."""
    s = _SI.hex(salt_hex)
    x = _H(s, _H_str(f"{username}:{password}"))
    B = _SI.hex(server_public_hex)

    a = _SI.hex(os.urandom(32).hex())
    A = _g.mod_pow(a, _N)
    u = _H(A, B)

    # S = (B - k*g^x)^(a + u*x) mod N
    gx = _g.mod_pow(x, _N)
    diff = B.sub(_k.mul(gx).mod(_N)).mod(_N)
    exp = _SI(a.v + u.v * x.v, a.hl)
    S = diff.mod_pow(exp, _N)
    K = _H(S)

    hN_xor_hg = _H(_N).xor(_H(_g))
    hI = _H_str(username)
    M1 = _H(hN_xor_hg, hI, s, A, B, K)

    return A.to_hex(), M1.to_hex(), K


class TestSrpServerEphemeral:
    def test_returns_valid_hex_pair(self) -> None:
        _, verifier = _client_register("alice", "password")
        pub, sec = generate_server_ephemeral(verifier)
        assert len(pub) == 512  # 2048-bit = 256 bytes = 512 hex
        assert len(sec) == 64  # 32 bytes = 64 hex
        # Must be valid hex
        int(pub, 16)
        int(sec, 16)

    def test_different_calls_produce_different_ephemerals(self) -> None:
        _, verifier = _client_register("bob", "secret")
        pub1, sec1 = generate_server_ephemeral(verifier)
        pub2, sec2 = generate_server_ephemeral(verifier)
        assert sec1 != sec2  # random each time


class TestSrpFullFlow:
    def test_correct_password_succeeds(self) -> None:
        username, password = "testuser", "correcthorse"
        salt, verifier = _client_register(username, password)

        # Server step 1
        server_pub, server_sec = generate_server_ephemeral(verifier)

        # Client step 2
        A_hex, M1_hex, K = _client_prove(username, password, salt, server_pub)

        # Server step 3
        success, server_proof = verify_session(server_sec, A_hex, salt, username, verifier, M1_hex)
        assert success is True
        assert len(server_proof) == 64  # SHA-256 hash

        # Client verifies server proof: M2 = H(A, M1, K)
        A = _SI.hex(A_hex)
        M1 = _SI.hex(M1_hex)
        expected_M2 = _H(A, M1, K)
        assert server_proof == expected_M2.to_hex()

    def test_wrong_password_fails(self) -> None:
        username = "testuser"
        salt, verifier = _client_register(username, "realpassword")

        server_pub, server_sec = generate_server_ephemeral(verifier)
        A_hex, M1_hex, _ = _client_prove(username, "wrongpassword", salt, server_pub)

        success, server_proof = verify_session(server_sec, A_hex, salt, username, verifier, M1_hex)
        assert success is False
        assert server_proof == ""

    def test_wrong_username_fails(self) -> None:
        salt, verifier = _client_register("alice", "password123")

        server_pub, server_sec = generate_server_ephemeral(verifier)
        # Client uses correct password but server has different username
        A_hex, M1_hex, _ = _client_prove("alice", "password123", salt, server_pub)

        success, _ = verify_session(server_sec, A_hex, salt, "bob", verifier, M1_hex)
        assert success is False

    def test_tampered_client_proof_fails(self) -> None:
        username, password = "testuser", "mypassword"
        salt, verifier = _client_register(username, password)

        server_pub, server_sec = generate_server_ephemeral(verifier)
        A_hex, M1_hex, _ = _client_prove(username, password, salt, server_pub)

        # Tamper with proof
        tampered = format((int(M1_hex, 16) ^ 1), "064x")
        success, _ = verify_session(server_sec, A_hex, salt, username, verifier, tampered)
        assert success is False

    def test_multiple_users_independent(self) -> None:
        """Two users registering and logging in should not interfere."""
        s1, v1 = _client_register("user1", "pass1")
        s2, v2 = _client_register("user2", "pass2")

        pub1, sec1 = generate_server_ephemeral(v1)
        pub2, sec2 = generate_server_ephemeral(v2)

        A1, M1_1, _ = _client_prove("user1", "pass1", s1, pub1)
        A2, M1_2, _ = _client_prove("user2", "pass2", s2, pub2)

        ok1, _ = verify_session(sec1, A1, s1, "user1", v1, M1_1)
        ok2, _ = verify_session(sec2, A2, s2, "user2", v2, M1_2)
        assert ok1 is True
        assert ok2 is True

    def test_unicode_username(self) -> None:
        """SRP should work with unicode usernames (Chinese, etc)."""
        username = "测试用户"
        password = "密码123!@#"
        salt, verifier = _client_register(username, password)

        server_pub, server_sec = generate_server_ephemeral(verifier)
        A_hex, M1_hex, _ = _client_prove(username, password, salt, server_pub)

        success, _ = verify_session(server_sec, A_hex, salt, username, verifier, M1_hex)
        assert success is True
