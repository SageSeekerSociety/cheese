use crate::srp_integer::SrpInteger;
use lazy_static::lazy_static;
use sha2::{Digest, Sha256};

// RFC 5054 2048-bit Group (default, matches secure-remote-password JS lib)
const N_2048_HEX: &str = "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B855F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773BCA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB694B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73";
const G_2048_HEX: &str = "02";

pub const HASH_OUTPUT_BYTES: usize = 32; // SHA-256

lazy_static! {
    pub static ref N: SrpInteger = SrpInteger::from_hex(N_2048_HEX).unwrap();
    pub static ref G: SrpInteger = SrpInteger::from_hex(G_2048_HEX).unwrap();
    pub static ref K: SrpInteger = hash_ints(&[&N, &G]);
    pub static ref H_N: SrpInteger = hash_ints(&[&N]);
    pub static ref H_G: SrpInteger = hash_ints(&[&G]);
    pub static ref H_N_XOR_H_G: SrpInteger = H_N.xor(&H_G);
}

/// Hash function for SRP protocol (SHA-256) — operates on SrpIntegers
pub fn hash_ints(args: &[&SrpInteger]) -> SrpInteger {
    let mut hasher = Sha256::new();
    for arg in args {
        let hex = arg.to_hex();
        let bytes = hex::decode(&hex).unwrap();
        hasher.update(&bytes);
    }
    let result = hasher.finalize();
    let hex_result = hex::encode(result);
    SrpInteger::from_hex(&hex_result).unwrap()
}

/// Hash a string with SHA-256
pub fn hash_str(s: &str) -> SrpInteger {
    let mut hasher = Sha256::new();
    hasher.update(s.as_bytes());
    let result = hasher.finalize();
    let hex_result = hex::encode(result);
    SrpInteger::from_hex(&hex_result).unwrap()
}
