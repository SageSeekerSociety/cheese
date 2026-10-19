package claudecode

import (
	"bytes"
	"crypto/sha256"
	"errors"
	"fmt"
	"os/exec"
	"os/user"
	"strconv"
	"strings"
)

// Claude Code 2.1.299 names non-default Keychain entries by the first eight
// hex digits of SHA-256(CLAUDE_CONFIG_DIR). A file move alone loses the login.
func keychainService(directory string) string {
	hash := sha256.Sum256([]byte(directory))
	return fmt.Sprintf("Claude Code-credentials-%x", hash[:4])
}

func readKeychain(service string) ([]byte, error) {
	value, err := exec.Command("security", "find-generic-password", "-s", service, "-w").Output()
	var exit *exec.ExitError
	if errors.As(err, &exit) && exit.ExitCode() == 44 {
		return nil, nil // No Keychain item: Linux-style files or a model service.
	}
	if err != nil {
		return nil, fmt.Errorf("claude: read login Keychain item: %w", err)
	}
	return bytes.TrimSuffix(value, []byte("\n")), nil
}

func relocateKeychain(source, destination string) (func() error, error) {
	oldService, newService := keychainService(source), keychainService(destination)
	secret, err := readKeychain(oldService)
	if err != nil || len(secret) == 0 {
		return func() error { return nil }, err
	}
	existing, err := readKeychain(newService)
	if err != nil {
		return nil, err
	}
	if len(existing) > 0 && !bytes.Equal(existing, secret) {
		return nil, errors.New("claude: destination Keychain login already exists; both retained")
	}
	current, err := user.Current()
	if err != nil {
		return nil, err
	}
	// security's interactive input keeps credentials out of process arguments.
	// Hex input avoids interpreting quotes or newlines from the credential.
	command := exec.Command("security", "-i")
	command.Stdin = strings.NewReader(fmt.Sprintf("add-generic-password -U -a %s -s %s -X %x\n",
		strconv.Quote(current.Username), strconv.Quote(newService), secret))
	if err := command.Run(); err != nil {
		return nil, fmt.Errorf("claude: copy login Keychain item: %w", err)
	}
	copied, err := readKeychain(newService)
	if err != nil || !bytes.Equal(copied, secret) {
		return nil, errors.New("claude: could not confirm migrated Keychain login; original retained")
	}
	return func() error {
		if err := exec.Command("security", "delete-generic-password", "-s", oldService).Run(); err != nil {
			return fmt.Errorf("claude: remove old Keychain login: %w", err)
		}
		return nil
	}, nil
}
