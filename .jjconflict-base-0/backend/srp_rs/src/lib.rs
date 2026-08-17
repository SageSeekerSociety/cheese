mod params;
mod srp_integer;

use params::{hash_ints, hash_str, G, H_N_XOR_H_G, K, N, HASH_OUTPUT_BYTES};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use srp_integer::SrpInteger;

/// Generate server's ephemeral key pair.
/// Returns (public_hex, secret_hex).
#[pyfunction]
fn generate_server_ephemeral(verifier: &str) -> PyResult<(String, String)> {
    let v = SrpInteger::from_hex(verifier).map_err(|e| PyValueError::new_err(e))?;

    // B = kv + g^b (b = random)
    let b = SrpInteger::random_integer(HASH_OUTPUT_BYTES);
    let gb = G.mod_pow(&b, &N);
    let kv = K.multiply(&v).modulo(&N);
    let big_b = kv.add(&gb).modulo(&N);

    Ok((big_b.to_hex(), b.to_hex()))
}

/// Verify the client's SRP proof and derive the server session.
/// Returns (success, server_proof_hex).
#[pyfunction]
fn verify_session(
    server_secret_hex: &str,
    client_public_hex: &str,
    salt_hex: &str,
    username: &str,
    verifier_hex: &str,
    client_proof_hex: &str,
) -> PyResult<(bool, String)> {
    let b = SrpInteger::from_hex(server_secret_hex).map_err(|e| PyValueError::new_err(e))?;
    let big_a = SrpInteger::from_hex(client_public_hex).map_err(|e| PyValueError::new_err(e))?;
    let s = SrpInteger::from_hex(salt_hex).map_err(|e| PyValueError::new_err(e))?;
    let v = SrpInteger::from_hex(verifier_hex).map_err(|e| PyValueError::new_err(e))?;
    let m1 = SrpInteger::from_hex(client_proof_hex).map_err(|e| PyValueError::new_err(e))?;

    // A % N must not be zero
    if big_a.is_zero() || big_a.modulo(&N).is_zero() {
        return Ok((false, String::new()));
    }

    // B = kv + g^b
    let gb = G.mod_pow(&b, &N);
    let kv = K.multiply(&v).modulo(&N);
    let big_b = kv.add(&gb).modulo(&N);

    // u = H(A, B)
    let u = hash_ints(&[&big_a, &big_b]);

    // S = (A * v^u) ^ b mod N
    let vu = v.mod_pow(&u, &N);
    let a_vu = big_a.multiply(&vu).modulo(&N);
    let big_s = a_vu.mod_pow(&b, &N);

    // K = H(S)
    let big_k = hash_ints(&[&big_s]);

    // H(I)
    let i_hash = hash_str(username);

    // M = H(H(N) xor H(g), H(I), s, A, B, K)
    let expected_m1 = hash_ints(&[&H_N_XOR_H_G, &i_hash, &s, &big_a, &big_b, &big_k]);

    if !expected_m1.equals(&m1) {
        return Ok((false, String::new()));
    }

    // P = H(A, M, K) — server proof
    let m2 = hash_ints(&[&big_a, &m1, &big_k]);
    Ok((true, m2.to_hex()))
}

#[pymodule]
fn srp_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(generate_server_ephemeral, m)?)?;
    m.add_function(wrap_pyfunction!(verify_session, m)?)?;
    Ok(())
}
