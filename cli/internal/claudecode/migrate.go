package claudecode

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
	"golang.org/x/text/unicode/norm"
)

// MigrateLogins moves the old shared store into ownerRoot once, and records
// that it has. It returns the old directories it left where they were because
// their login had already moved: a session started before the move keeps its
// old CLAUDE_CONFIG_DIR and writes a fresh .claude.json there, recreating the
// old path. Such a directory is never moved over a login or deleted, and once
// the move is recorded no old path is read again, so a login logged out later
// is never brought back from it. Failure leaves the source available for the
// next start, never overwritten.
func MigrateLogins(ownerRoot string) ([]string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	shared := filepath.Join(home, place.Root)
	done := filepath.Join(shared, ".claude-logins-migrated")
	first := filepath.Join(shared, "claude-login")
	more := filepath.Join(shared, "claude-logins")
	if migrated(done) {
		return nil, nil
	}
	_, firstErr := os.Stat(first)
	_, moreErr := os.Stat(more)
	if errors.Is(firstErr, os.ErrNotExist) && errors.Is(moreErr, os.ErrNotExist) {
		return nil, nil
	}
	unlock, err := lockFile(filepath.Join(shared, ".claude-logins-migration.lock"))
	if err != nil {
		return nil, fmt.Errorf("claude: lock login migration: %w", err)
	}
	defer unlock()
	if migrated(done) {
		return nil, nil
	}
	root := filepath.Join(home, ownerRoot)
	var retained []string
	moved, err := moveLogin(first, filepath.Join(root, place.ClaudeLogin))
	if err != nil {
		return nil, err
	}
	if !moved {
		retained = append(retained, first)
	}
	entries, err := os.ReadDir(more)
	moreExists := err == nil
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	keptInMore := false
	for _, entry := range entries {
		source := filepath.Join(more, entry.Name())
		moved, err := moveLogin(source, filepath.Join(root, place.ClaudeLogins, entry.Name()))
		if err != nil {
			return nil, err
		}
		if !moved {
			retained = append(retained, source)
			keptInMore = true
		}
	}
	if moreExists && !keptInMore {
		if err := os.Remove(more); err != nil {
			return nil, err
		}
	}
	if err := os.WriteFile(done, nil, 0600); err != nil {
		return nil, err
	}
	return retained, nil
}

func migrated(marker string) bool {
	_, err := os.Stat(marker)
	return err == nil
}

// moveLogin moves source to destination and reports whether it did. An
// existing destination is the login already moved: source stays as it is.
func moveLogin(source, destination string) (bool, error) {
	info, err := os.Stat(source)
	if errors.Is(err, os.ErrNotExist) {
		return true, nil
	} else if err != nil {
		return false, err
	}
	if _, err := os.Stat(destination); err == nil {
		return false, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return false, err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return false, err
	}
	if info.IsDir() && runtime.GOOS == "darwin" {
		if err := preserveKeychain(source, destination); err != nil {
			return false, err
		}
	}
	if err := os.Rename(source, destination); err != nil {
		return false, fmt.Errorf("claude: move login: %w", err)
	}
	return true, nil
}

func preserveKeychain(source, destination string) error {
	security, err := exec.LookPath("security")
	if err != nil {
		return err
	}
	oldHash := sha256.Sum256([]byte(norm.NFC.String(source)))
	newHash := sha256.Sum256([]byte(norm.NFC.String(destination)))
	// Pinned Claude 2.1.299 hashes the storage directory into two Keychain
	// services (OAuth and Console). Map only those service arguments: keeping
	// SECURESTORAGE at the old directory would break .credentials.json fallback.
	// Retained sessions still update the original item, so never copy/delete it.
	script := fmt.Sprintf(`#!/bin/sh
old=%[1]x
new=%[2]x
if [ "$1" = -i ]; then
  sed -e "s/ -s \"Claude Code-$new\"/ -s \"Claude Code-$old\"/g" \
      -e "s/ -s \"Claude Code-credentials-$new\"/ -s \"Claude Code-credentials-$old\"/g" |
    %[3]s "$@"
  exit $?
fi
remaining=$#
service_arg=0
while [ "$remaining" -gt 0 ]; do
  arg=$1
  shift
  if [ "$service_arg" -eq 1 ]; then
    case "$arg" in
      "Claude Code-$new"|"Claude Code-credentials-$new") arg="${arg%%$new}$old" ;;
    esac
    service_arg=0
  fi
  if [ "$arg" = -s ]; then service_arg=1; fi
  set -- "$@" "$arg"
  remaining=$((remaining - 1))
done
exec %[3]s "$@"
`, oldHash[:4], newHash[:4], "'"+strings.ReplaceAll(security, "'", "'\"'\"'")+"'")
	dir := filepath.Join(source, place.ClaudeKeychain)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "security"), []byte(script), 0700)
}
