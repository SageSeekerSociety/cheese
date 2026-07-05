package app

import (
	"os"
	"path/filepath"
)

// resolveRuntimeDir returns a private, mode-0700 directory to hold the
// cheesed-managed tmux socket (contract §3): XDG_RUNTIME_DIR if set,
// otherwise a fresh temporary directory (os.MkdirTemp already creates its
// result with mode 0700).
func resolveRuntimeDir() (string, error) {
	if xdg := os.Getenv("XDG_RUNTIME_DIR"); xdg != "" {
		dir := filepath.Join(xdg, "cheesed")
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return "", err
		}
		return dir, nil
	}
	return os.MkdirTemp("", "cheesed-")
}
