"""A TLS endpoint on localhost with a certificate of the test's choosing.

For tests of whatever dials the model tunnel's gateway: the helper on a machine,
and the backend that watches the gateway's certificate. Each test gets its own
CA, so trusting it means passing that CA's file and nothing else.
"""

import contextlib
import datetime
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def write_pki(
    directory: Path, *, expires_in: datetime.timedelta
) -> tuple[str, str, str]:
    """A CA and a ``localhost`` certificate it signed that expires ``expires_in``
    from now (negative: already expired). Returns (ca, cert, key) file paths."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("test ca"))
        .issuer_name(_name("test ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=60))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    expires = now + expires_in
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(min(now, expires) - datetime.timedelta(days=30))
        .not_valid_after(expires)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = (
        directory / n for n in ("ca.pem", "leaf.pem", "leaf.key")
    )
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(ca_path), str(cert_path), str(key_path)


@contextlib.contextmanager
def tls_gateway(
    directory: Path, *, expires_in: datetime.timedelta
) -> Iterator[tuple[str, str]]:
    """Serve TLS on localhost, answering any upgrade with 101.
    Yields (wss url, ca file that trusts it)."""
    ca_path, cert_path, key_path = write_pki(directory, expires_in=expires_in)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.2)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                raw, _ = listener.accept()
            except OSError:
                continue
            try:
                with context.wrap_socket(raw, server_side=True) as conn:
                    head = b""
                    while b"\r\n\r\n" not in head:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        head += chunk
                    conn.sendall(
                        b"HTTP/1.1 101 Switching Protocols\r\n"
                        b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
                    )
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"wss://localhost:{listener.getsockname()[1]}/llm/tunnel", ca_path
    finally:
        stop.set()
        thread.join(timeout=2)
        listener.close()
