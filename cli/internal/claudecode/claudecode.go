// Package claudecode is the machine owner's own Claude Code, as the platform
// runs it: the build the platform pins, logged in once under a directory of the
// platform's own (`~/.cheese/claude-login`), apart from the owner's `~/.claude`.
//
// The owner logs it in with `cheesehost claude login`. Every session of the
// owner's Claude Code on this machine keeps its own config directory and reads
// this one as its credential store, so a login here is the only one the
// platform's sessions need and the owner's own Claude Code never sees it.
package claudecode

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

// LoginDir is where the platform's Claude Code login lives on this machine.
func LoginDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("claude: locate home: %w", err)
	}
	return filepath.Join(home, place.Root, place.ClaudeLogin), nil
}

// vendorPlatform names a machine the way Anthropic's builds are published, the
// same names the platform's download route accepts (`claude_dist.PLATFORM_RE`).
func vendorPlatform(goos, goarch string, musl bool) (string, error) {
	var system string
	switch goos {
	case "linux":
		system = "linux"
	case "darwin":
		system = "darwin"
	case "windows":
		system = "win32"
	default:
		return "", fmt.Errorf("claude: no build for %s", goos)
	}
	var arch string
	switch goarch {
	case "amd64":
		arch = "x64"
	case "arm64":
		arch = "arm64"
	default:
		return "", fmt.Errorf("claude: no build for %s", goarch)
	}
	platform := system + "-" + arch
	if system == "linux" && musl {
		platform += "-musl"
	}
	return platform, nil
}

// usesMusl reports whether this Linux machine's C library is musl, which the
// vendor builds separately for.
func usesMusl() bool {
	matches, _ := filepath.Glob("/lib/ld-musl-*")
	return len(matches) > 0
}

func binaryName(platform string) string {
	if strings.HasPrefix(platform, "win32-") {
		return "claude.exe"
	}
	return "claude"
}

// origin is scheme://host of the stored server base: the connector's artifacts
// are served at the origin root, never under an edge prefix.
func origin(base string) (string, error) {
	u, err := url.Parse(base)
	if err != nil || u.Host == "" {
		return "", fmt.Errorf("claude: bad server address %q", base)
	}
	return u.Scheme + "://" + u.Host, nil
}

// PinnedVersion asks the server which Claude Code build it runs.
func PinnedVersion(ctx context.Context, base string) (string, error) {
	root, err := origin(base)
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, root+"/connector/claude/pin", nil)
	if err != nil {
		return "", err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("claude: ask the server for its Claude Code version: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("claude: ask the server for its Claude Code version: HTTP %d", resp.StatusCode)
	}
	var answer struct {
		Version string `json:"version"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&answer); err != nil || answer.Version == "" {
		return "", errors.New("claude: the server did not name a Claude Code version")
	}
	return answer.Version, nil
}

// binaryPath is where the platform keeps a pinned build on this machine, the
// place the executor's bootstrap looks first (`remote_execution/bootstrap.binary`).
func binaryPath(version string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("claude: locate home: %w", err)
	}
	name := version
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	return filepath.Join(home, place.Root, "claude", "versions", name), nil
}

// Installed returns the pinned build when this machine already has it.
func Installed(ctx context.Context, base string) (string, bool, error) {
	version, err := PinnedVersion(ctx, base)
	if err != nil {
		return "", false, err
	}
	path, err := binaryPath(version)
	if err != nil {
		return "", false, err
	}
	return path, runsAs(path, version), nil
}

// A download is given up when nothing has arrived for stallAfter: the build is
// a few hundred megabytes and a slow link takes minutes, but one that stops
// moving is not going to finish.
var stallAfter = 60 * time.Second

// Ensure returns the pinned build, downloading it from the server first when
// this machine does not have it yet.
func Ensure(ctx context.Context, base string, progress io.Writer) (string, error) {
	version, err := PinnedVersion(ctx, base)
	if err != nil {
		return "", err
	}
	destination, err := binaryPath(version)
	if err != nil {
		return "", err
	}
	if runsAs(destination, version) {
		return destination, nil
	}
	platform, err := vendorPlatform(runtime.GOOS, runtime.GOARCH, runtime.GOOS == "linux" && usesMusl())
	if err != nil {
		return "", err
	}
	root, err := origin(base)
	if err != nil {
		return "", err
	}
	source := fmt.Sprintf("%s/connector/claude/%s/%s/%s", root, version, platform, binaryName(platform))
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		return "", fmt.Errorf("claude: %w", err)
	}
	if progress != nil {
		fmt.Fprintf(progress, "Downloading Claude Code %s…\n", version)
	}
	temporary, err := download(ctx, source, filepath.Dir(destination))
	if err != nil {
		return "", err
	}
	if !runsAs(temporary, version) {
		os.Remove(temporary)
		return "", fmt.Errorf("claude: the downloaded build does not run as Claude Code %s", version)
	}
	if err := os.Rename(temporary, destination); err != nil {
		os.Remove(temporary)
		return "", fmt.Errorf("claude: %w", err)
	}
	return destination, nil
}

func download(ctx context.Context, source, dir string) (string, error) {
	output, err := os.CreateTemp(dir, ".claude-download-*")
	if err != nil {
		return "", fmt.Errorf("claude: %w", err)
	}
	path := output.Name()
	fail := func(err error) (string, error) {
		output.Close()
		os.Remove(path)
		return "", err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, source, nil)
	if err != nil {
		return fail(err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fail(fmt.Errorf("claude: download %s: %w", source, err))
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fail(fmt.Errorf("claude: download %s: HTTP %d", source, resp.StatusCode))
	}
	stall := time.AfterFunc(stallAfter, cancel)
	buffer := make([]byte, 1<<20)
	for {
		n, readErr := resp.Body.Read(buffer)
		if n > 0 {
			stall.Reset(stallAfter)
			if _, err := output.Write(buffer[:n]); err != nil {
				stall.Stop()
				return fail(fmt.Errorf("claude: %w", err))
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			stall.Stop()
			return fail(fmt.Errorf("claude: download %s: %w", source, readErr))
		}
	}
	stall.Stop()
	if err := output.Close(); err != nil {
		os.Remove(path)
		return "", fmt.Errorf("claude: %w", err)
	}
	if err := os.Chmod(path, 0o700); err != nil {
		os.Remove(path)
		return "", fmt.Errorf("claude: %w", err)
	}
	return path, nil
}

// runsAs reports whether path is an executable that reports version.
func runsAs(path, version string) bool {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, path, "--version").Output()
	if err != nil {
		return false
	}
	fields := strings.Fields(string(out))
	return len(fields) > 0 && fields[0] == version
}

// inherited are the variables that would point Claude Code at another login or
// another config than the platform's: an inherited token wins over any stored
// login, and the platform names its own directories.
var inherited = []string{
	"CLAUDE_CONFIG_DIR",
	"CLAUDE_SECURESTORAGE_CONFIG_DIR",
	"CLAUDE_CODE_OAUTH_TOKEN",
	"CLAUDECODE",
	"CLAUDE_CODE_ENTRYPOINT",
}

// Environment is the environment Claude Code runs in for the platform's login:
// this process's, without what would redirect it, with the login directory as
// its config directory.
func Environment(base []string, loginDir string) []string {
	env := make([]string, 0, len(base)+2)
	hasUser := false
	for _, entry := range base {
		name, _, _ := strings.Cut(entry, "=")
		skip := false
		for _, drop := range inherited {
			if name == drop {
				skip = true
				break
			}
		}
		if skip {
			continue
		}
		if name == "USER" {
			hasUser = true
		}
		env = append(env, entry)
	}
	// On macOS the login is a Keychain item, and Claude Code started from a
	// launchd service has been reported unable to find it without USER
	// (anthropics/claude-code#77213); a service environment does not set it.
	if !hasUser {
		if current, err := user.Current(); err == nil && current.Username != "" {
			env = append(env, "USER="+current.Username)
		}
	}
	return append(env, "CLAUDE_CONFIG_DIR="+loginDir)
}

// Command runs the pinned build against the platform's login.
func Command(ctx context.Context, binary, loginDir string, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, binary, args...)
	cmd.Env = Environment(os.Environ(), loginDir)
	return cmd
}

// Status is what `claude auth status` reports about the platform's login.
type Status struct {
	LoggedIn         bool   `json:"loggedIn"`
	AuthMethod       string `json:"authMethod"`
	SubscriptionType string `json:"subscriptionType"`
}

// ReadStatus asks the pinned build about the platform's login without calling a
// model.
func ReadStatus(ctx context.Context, binary, loginDir string) (Status, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	out, err := Command(ctx, binary, loginDir, "auth", "status").Output()
	var status Status
	if decodeErr := json.Unmarshal(out, &status); decodeErr != nil {
		if err != nil {
			return Status{}, fmt.Errorf("claude: auth status: %w", err)
		}
		return Status{}, fmt.Errorf("claude: auth status: %w", decodeErr)
	}
	return status, nil
}
