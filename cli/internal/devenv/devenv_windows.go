// Package devenv places what the server's commands expect to find on a
// Windows machine: `python3` for the executor and the scripts it runs, and a
// POSIX shell with git, curl and coreutils (Git for Windows, which Claude Code
// on Windows needs anyway). Both come from the server's toolchain route, pinned
// and checksummed there, and live under the footprint root, so uninstall takes
// them with everything else. The connector puts them first on its own PATH,
// which every command it runs inherits — so the argv the server already sends
// (`python3 -`, `sh -c …`, `git …`) resolves here unchanged.
package devenv

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

const platform = "windows-x64"

var tools = []tool{
	{"python", suffix("python"), extractPython},
	{"git", suffix("git"), extractGit},
}

// Ensure makes the runtime current against the server at base (the
// connector's `<origin>/connector`) and puts it on this process's PATH.
func Ensure(ctx context.Context, base string, log io.Writer) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	root := filepath.Join(home, place.Root, "runtime")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	for _, t := range tools {
		if err := ensureTool(ctx, strings.TrimRight(base, "/"), root, platform, t, log); err != nil {
			return fmt.Errorf("%s: %w", t.name, err)
		}
	}
	git := filepath.Join(root, "git")
	path := []string{
		filepath.Join(root, "python"),
		filepath.Join(git, "cmd"),
		filepath.Join(git, "usr", "bin"),
		filepath.Join(git, "mingw64", "bin"),
	}
	current := os.Getenv("PATH")
	if !strings.HasPrefix(current, path[0]+";") {
		os.Setenv("PATH", strings.Join(append(path, current), ";"))
	}
	os.Setenv("CLAUDE_CODE_GIT_BASH_PATH", filepath.Join(git, "bin", "bash.exe"))
	return pinPythonPath(filepath.Join(root, "python"))
}

func suffix(name string) string {
	if name == "git" {
		return ".exe" // a self-extracting archive, run to unpack
	}
	return ".zip"
}

// extractPython unpacks the embeddable distribution with Windows' own tar and
// names it the way the server's commands call it. Its ._pth is replaced on
// every start by pinPythonPath.
func extractPython(archive, dir string) error {
	if out, err := hidden("tar.exe", "-xf", archive, "-C", dir).CombinedOutput(); err != nil {
		return fmt.Errorf("unpack: %v: %s", err, out)
	}
	return copyFile(filepath.Join(dir, "python.exe"), filepath.Join(dir, "python3.exe"))
}

// extractGit runs PortableGit's self-extractor into dir.
func extractGit(archive, dir string) error {
	cmd := hidden(archive)
	// The extractor takes its destination as -o"<dir>", quote after the -o;
	// the default quoting would wrap the whole argument, and a profile path
	// with a space in it is ordinary.
	cmd.SysProcAttr.CmdLine = fmt.Sprintf(`"%s" -y -o"%s"`, archive, dir)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("unpack: %v: %s", err, out)
	}
	if _, err := os.Stat(filepath.Join(dir, "bin", "bash.exe")); err != nil {
		return fmt.Errorf("unpacked, but no bash: %w", err)
	}
	return nil
}

func copyFile(from, to string) error {
	data, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	return os.WriteFile(to, data, 0o755)
}

func hidden(name string, args ...string) *exec.Cmd {
	cmd := exec.Command(name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	return cmd
}
