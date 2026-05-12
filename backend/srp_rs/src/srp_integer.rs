use num_bigint::{BigInt, BigUint, Sign};
use num_traits::Zero;
use rand::{thread_rng, RngCore};
use std::fmt;

#[derive(Clone)]
pub struct SrpInteger {
    value: BigInt,
    hex_length: Option<usize>,
}

impl SrpInteger {
    pub fn zero() -> Self {
        Self {
            value: BigInt::zero(),
            hex_length: None,
        }
    }

    pub fn from_bytes(bytes: &[u8]) -> Self {
        let value: BigInt = BigUint::from_bytes_be(bytes).into();
        Self {
            value,
            hex_length: Some(bytes.len() * 2),
        }
    }

    pub fn from_hex(hex: &str) -> Result<Self, String> {
        let cleaned_hex = hex.trim().replace(' ', "").replace('\n', "");
        match BigUint::parse_bytes(cleaned_hex.as_bytes(), 16) {
            Some(value) => Ok(Self {
                value: value.into(),
                hex_length: Some(cleaned_hex.len()),
            }),
            None => Err(format!("Invalid hex string: {}", hex)),
        }
    }

    pub fn to_hex(&self) -> String {
        if self.hex_length.is_none() {
            panic!("This SrpInteger has no specified length");
        }
        let hex = self.value.to_str_radix(16).to_lowercase();
        if let Some(len) = self.hex_length {
            if hex.len() < len {
                return "0".repeat(len - hex.len()) + &hex;
            }
        }
        hex
    }

    pub fn random_integer(bytes: usize) -> Self {
        let mut rng = thread_rng();
        let mut buf = vec![0u8; bytes];
        rng.fill_bytes(&mut buf);
        let hex = hex::encode(&buf);
        Self::from_hex(&hex).expect("Failed to parse valid hex")
    }

    pub fn equals(&self, other: &Self) -> bool {
        self.value == other.value
    }

    pub fn mod_pow(&self, exp: &Self, modulus: &Self) -> Self {
        let result = self.value.modpow(&exp.value, &modulus.value);
        Self {
            value: result,
            hex_length: modulus.hex_length,
        }
    }

    pub fn multiply(&self, other: &Self) -> Self {
        let result = &self.value * &other.value;
        Self {
            value: result,
            hex_length: self.hex_length.or(other.hex_length),
        }
    }

    pub fn add(&self, other: &Self) -> Self {
        let result = &self.value + &other.value;
        Self {
            value: result,
            hex_length: self.hex_length.or(other.hex_length),
        }
    }

    pub fn subtract(&self, other: &Self) -> Self {
        let result = &self.value - &other.value;
        Self {
            value: result,
            hex_length: self.hex_length.or(other.hex_length),
        }
    }

    pub fn mod_(&self, modulus: &Self) -> Self {
        let mut result = &self.value % &modulus.value;
        if result < BigInt::from(0) {
            result += &modulus.value;
        }
        Self {
            value: result,
            hex_length: modulus.hex_length,
        }
    }

    pub fn xor(&self, other: &Self) -> Self {
        let a_hex = self.to_hex();
        let b_hex = other.to_hex();
        let a_bytes = hex::decode(&a_hex).unwrap();
        let b_bytes = hex::decode(&b_hex).unwrap();
        let max_len = std::cmp::max(a_bytes.len(), b_bytes.len());
        let mut a_padded = vec![0; max_len - a_bytes.len()];
        let mut b_padded = vec![0; max_len - b_bytes.len()];
        a_padded.extend_from_slice(&a_bytes);
        b_padded.extend_from_slice(&b_bytes);
        let xor_result: Vec<u8> = a_padded
            .iter()
            .zip(b_padded.iter())
            .map(|(a, b)| a ^ b)
            .collect();
        Self {
            value: BigInt::from_bytes_be(Sign::Plus, &xor_result),
            hex_length: self.hex_length,
        }
    }

    pub fn is_zero(&self) -> bool {
        self.value.is_zero()
    }

    pub fn modulo(&self, modulus: &Self) -> Self {
        self.mod_(modulus)
    }
}

impl fmt::Debug for SrpInteger {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        let hex = self.value.to_str_radix(16);
        if hex.len() > 16 {
            write!(f, "<SrpInteger {}{}>", &hex[0..16], "...")
        } else {
            write!(f, "<SrpInteger {}>", hex)
        }
    }
}
