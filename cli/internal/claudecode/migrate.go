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

// MigrateLogins moves the old shared store once. Nothing reads it after this;
// failure leaves the source available for the next start, never overwritten.
func MigrateLogins(ownerRoot string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	shared := filepath.Join(home, place.Root)
	first := filepath.Join(shared, "claude-login")
	more := filepath.Join(shared, "claude-logins")
	_, firstErr := os.Stat(first)
	_, moreErr := os.Stat(more)
	if errors.Is(firstErr, os.ErrNotExist) && errors.Is(moreErr, os.ErrNotExist) {
		return nil
	}
	unlock, err := lockMigration(filepath.Join(shared, ".claude-logins-migration.lock"))
	if err != nil {
		return fmt.Errorf("claude: lock login migration: %w", err)
	}
	defer unlock()
	root := filepath.Join(home, ownerRoot)
	if err := moveLogin(first, filepath.Join(root, place.ClaudeLogin)); err != nil {
		return err
	}
	entries, err := os.ReadDir(more)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if err := moveLogin(filepath.Join(more, entry.Name()), filepath.Join(root, place.ClaudeLogins, entry.Name())); err != nil {
			return err
		}
	}
	return os.Remove(more)
}

func moveLogin(source, destination string) error {
	info, err := os.Stat(source)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	if _, err := os.Stat(destination); err == nil {
		return fmt.Errorf("claude: cannot migrate %s: destination %s already exists; both retained", source, destination)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return err
	}
	if info.IsDir() && runtime.GOOS == "darwin" {
		if err := preserveKeychain(source, destination); err != nil {
			return err
		}
	}
	if err := os.Rename(source, destination); err != nil {
		return fmt.Errorf("claude: move login: %w", err)
	}
	return nil
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
