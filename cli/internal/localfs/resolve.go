package localfs

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// ResolvedPath is a path after this machine has had its say about it.
//
// There are two answers here on purpose, and both are used:
//
//   - Lexical is the path as written, expanded (~) and normalized. It is the one
//     the owner recognizes, and it is what makes 「我授权的是这个目录名」 mean the
//     name they typed.
//   - Real/OnDisk is where the path actually leads, with symlinks followed. It is
//     the one that catches a link planted inside a granted directory pointing out
//     of it — an escape a lexical check cannot see.
//
// The containment decision requires both (see containsResolved). Only the device
// can compute Real: resolving a symlink means reading the disk, and the disk that
// matters is this one.
type ResolvedPath struct {
	Lexical NormalizedPath
	// OnDisk is the path to open. It is Real's text, reconstructed, so that the
	// open cannot be pointed somewhere else by the same link that was just checked.
	OnDisk string
	// Real is the normalized form of OnDisk. Only meaningful when RealKnown.
	Real NormalizedPath
	// RealKnown is false when nothing along the path exists, so there was nothing
	// to resolve. The lexical answer still stands; the real check is skipped rather
	// than failed, because failing it would refuse a write of a file that does not
	// exist yet inside a directory the owner plainly granted.
	RealKnown bool
}

// LocalPlatform is the filesystem convention of the machine this code is running
// on — which, for this package, is also the machine the paths are on. That is the
// whole reason resolution lives here and not on the platform.
func LocalPlatform() Platform {
	switch runtime.GOOS {
	case "windows":
		return PlatformWindows
	case "darwin":
		return PlatformMacOS
	default:
		return PlatformLinux
	}
}

// ResolvePath turns what the caller wrote into what this machine will do.
//
// The order matters. The tilde is expanded first, because the kernel does not
// expand it and a path that still had one would be normalized into a directory
// nobody authorized. Then the path is normalized lexically, which is where a
// relative path is refused by name — it denotes a directory that depends on where
// the process is standing, so it cannot be authorized. Only then is the disk
// consulted, because resolution is the expensive part and it is meaningless for a
// path that has already been refused.
func ResolvePath(raw string) (ResolvedPath, error) {
	var zero ResolvedPath
	expanded, err := expandTilde(raw)
	if err != nil {
		return zero, err
	}
	platform := LocalPlatform()

	lexical, err := Normalize(expanded, platform)
	if err != nil {
		return zero, err
	}

	onDisk, known := resolveSymlinks(expanded)
	real, err := Normalize(onDisk, platform)
	if err != nil {
		// EvalSymlinks does not produce a path that climbs out of its own root, so
		// arriving here means the resolved form is not something this package can
		// reason about — too long, or too deep. Judging it on the lexical form
		// alone would skip the check that exists precisely for this case, so the
		// whole request is refused instead.
		if refusal, ok := AsRefusal(err); ok {
			return zero, refusal
		}
		return zero, err
	}
	return ResolvedPath{
		Lexical:   lexical,
		OnDisk:    onDisk,
		Real:      real,
		RealKnown: known,
	}, nil
}

// expandTilde replaces a leading ~ with this user's home directory.
//
// The shell expands a tilde and the kernel does not, so a device that passed one
// through would either fail or — worse — resolve it to a home directory the
// platform never agreed to. ~user is refused rather than guessed: resolving
// somebody else's home means either reading the password database or inventing a
// convention, and the person who typed it meant a specific directory that this
// code would be guessing at.
func expandTilde(raw string) (string, error) {
	if raw == "" || raw[0] != '~' {
		return raw, nil
	}
	rest := raw[1:]
	if rest != "" && rest[0] != '/' && rest[0] != '\\' {
		return "", refuse("other_user_home", "不支持 ~用户名 形式的路径", raw)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", refuse("no_home", "无法确定当前用户的主目录", raw)
	}
	if rest == "" {
		return home, nil
	}
	return home + rest, nil
}

// resolveSymlinks follows every symlink on the path and returns where it lands,
// plus whether anything along it existed to be followed.
//
// The leaf of a write does not exist yet, so the walk goes up to the deepest
// ancestor that does, resolves that, and puts the remaining names back. Those
// remaining names cannot contain a symlink — they do not exist — which is what
// makes resolving the ancestor sufficient.
//
// There is still a window between resolving here and opening below, where a link
// could be swapped in. Closing it completely means opening with O_NOFOLLOW on
// every segment, which is not portable; the window is small, it requires write
// access inside the granted directory already, and the check that matters — the
// resolved path is inside the grant — has happened by then.
func resolveSymlinks(path string) (string, bool) {
	clean := filepath.Clean(path)
	prefix := clean
	rest := make([]string, 0, 8)
	for {
		if _, err := os.Lstat(prefix); err == nil {
			break
		}
		parent := filepath.Dir(prefix)
		if parent == prefix {
			// Nothing on this path exists at all — not even the root. There is
			// nothing to resolve, and saying so is better than claiming a real
			// path was computed.
			return clean, false
		}
		rest = append([]string{filepath.Base(prefix)}, rest...)
		prefix = parent
	}
	resolved, err := filepath.EvalSymlinks(prefix)
	if err != nil {
		return clean, false
	}
	if len(rest) == 0 {
		return resolved, true
	}
	return filepath.Join(append([]string{resolved}, rest...)...), true
}

// DisplayPath renders a path for a log line or an error message without offering
// it as something to open. Kept here so the one place that prints a path is the
// one place that can be audited.
func DisplayPath(p string) string {
	if p == "" {
		return "(empty)"
	}
	if strings.ContainsRune(p, 0) {
		return "(invalid path)"
	}
	return p
}
