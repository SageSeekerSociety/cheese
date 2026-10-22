//! What the server sends when a machine asks for the packed connector copy.
//!
//! The response says `Content-Encoding: gzip`, but curl (without
//! `--compressed`) writes the stream as it came, which is what keeps a resumed
//! range meaningful. So the file is unpacked here afterwards, and what decides
//! whether it needs unpacking is the gzip magic rather than a header: a
//! resumed transfer's own response may not repeat the header, and what gets
//! unpacked is the file assembled across attempts.

use std::path::Path;

const GZIP_MAGIC: [u8; 2] = [0x1f, 0x8b];

/// Whether the file starts with the gzip magic bytes.
pub fn is_gzip(file: &Path) -> Result<bool, String> {
    use std::io::Read;
    let mut magic = [0u8; 2];
    match std::fs::File::open(file).and_then(|mut handle| handle.read_exact(&mut magic)) {
        Ok(()) => Ok(magic == GZIP_MAGIC),
        // A file too short to hold the magic arrived plain and short.
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Ok(false),
        Err(e) => Err(e.to_string()),
    }
}

/// Unpacks the connector into `path`, replacing whatever is there.
pub fn unpack(packed: &Path, path: &Path) -> std::io::Result<()> {
    let plain = std::path::PathBuf::from(format!("{}.unpacked", path.display()));
    let source = std::fs::File::open(packed)?;
    let mut target = std::fs::File::create(&plain)?;
    let mut decoder = flate2::read::GzDecoder::new(source);
    std::io::copy(&mut decoder, &mut target)?;
    drop(target);
    std::fs::rename(&plain, path)
}

#[cfg(test)]
mod tests {
    use super::{is_gzip, unpack};

    fn dir(name: &str) -> std::path::PathBuf {
        let path =
            std::env::temp_dir().join(format!("cheese-packed-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn the_gzip_magic_names_the_packed_copy() {
        let root = dir("magic");
        let plain = root.join("plain");
        std::fs::write(&plain, b"MZ\x90\x00 not packed").unwrap();
        let packed = root.join("packed");
        std::fs::write(&packed, [0x1f, 0x8b, 0x08, 0x00]).unwrap();
        assert!(!is_gzip(&plain).unwrap());
        assert!(is_gzip(&packed).unwrap());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_packed_connector_is_unpacked_to_the_bytes_it_holds() {
        let payload = b"MZ connector bytes ".repeat(64);
        let root = dir("unpack");
        let packed = root.join("cheesehost.exe.part");
        let mut packed_stream = Vec::new();
        {
            use std::io::Write;
            let mut encoder =
                flate2::write::GzEncoder::new(&mut packed_stream, flate2::Compression::default());
            encoder.write_all(&payload).unwrap();
            encoder.finish().unwrap();
        }
        std::fs::write(&packed, &packed_stream).unwrap();
        let target = root.join("cheesehost.exe");

        unpack(&packed, &target).unwrap();

        assert_eq!(std::fs::read(&target).unwrap(), payload);
        assert!(!root.join("cheesehost.exe.unpacked").exists());
        let _ = std::fs::remove_dir_all(&root);
    }
}
