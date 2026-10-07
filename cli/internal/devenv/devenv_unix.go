//go:build !windows

// Package devenv places what the server's commands expect to find on a machine
// where the system does not already provide it. On Linux and macOS that is a
// recent enough `python3`: the session's runner and helpers need Python 3.11,
// and the one a Mac carries (/usr/bin/python3, from the command line tools) is
// 3.9, as is the system Python of some Linux releases still in use. Where the
// `python3` on PATH is older, or missing, the server's pinned build is placed
// under the footprint root and put first on this process's PATH, which every
// command and session it starts inherits.
package devenv

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

// minimum is the oldest Python the session's runner runs on (it uses StrEnum).
const minimum = "(3, 11)"

// Ensure places the server's Python when the system's is too old, and puts it
// first on PATH. base is the connector's `<origin>/connector`.
func Ensure(ctx context.Context, base string, log io.Writer) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	root := filepath.Join(home, place.Root, "runtime")
	bin := filepath.Join(root, "python", "bin")
	if _, err := os.Stat(filepath.Join(bin, "python3")); err != nil && recentEnough("python3") {
		return nil
	}
	platform, err := toolchainPlatform(runtime.GOOS, runtime.GOARCH)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	python := tool{"python", ".tar.gz", extractTarGz}
	if err := ensureTool(ctx, strings.TrimRight(base, "/"), root, platform, python, log); err != nil {
		return fmt.Errorf("python: %w", err)
	}
	current := os.Getenv("PATH")
	if !strings.HasPrefix(current, bin+":") {
		os.Setenv("PATH", bin+":"+current)
	}
	return nil
}

// recentEnough reports whether the named python runs and is new enough.
func recentEnough(python string) bool {
	check := fmt.Sprintf("import sys; sys.exit(0 if sys.version_info >= %s else 1)", minimum)
	return exec.Command(python, "-c", check).Run() == nil
}

// toolchainPlatform names this machine as the server's toolchain route does.
func toolchainPlatform(goos, goarch string) (string, error) {
	arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[goarch]
	if (goos != "linux" && goos != "darwin") || arch == "" {
		return "", fmt.Errorf("no Python build for %s/%s", goos, goarch)
	}
	return goos + "-" + arch, nil
}

// extractTarGz unpacks a python-build-standalone archive into dir, dropping
// its top directory (`python/`), so the interpreter is at dir/bin/python3.
// Nothing may land outside dir, by name or through a link.
func extractTarGz(archive, dir string) error {
	file, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer file.Close()
	unzipped, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	reader := tar.NewReader(unzipped)
	for {
		header, err := reader.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		name := path.Clean(header.Name)
		if name == ".." || strings.HasPrefix(name, "../") || path.IsAbs(name) {
			return fmt.Errorf("unpack: %q is outside the archive", header.Name)
		}
		_, rest, _ := strings.Cut(name, "/")
		if rest == "" {
			continue
		}
		target := filepath.Join(dir, filepath.FromSlash(rest))
		if !inside(dir, target) {
			return fmt.Errorf("unpack: %q is outside the archive", header.Name)
		}
		mode := os.FileMode(header.Mode).Perm()
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
			if err != nil {
				return err
			}
			_, err = io.Copy(out, reader)
			out.Close()
			if err != nil {
				return err
			}
		case tar.TypeSymlink:
			if !inside(dir, filepath.Join(filepath.Dir(target), header.Linkname)) {
				return fmt.Errorf("unpack: %q links outside the archive", header.Name)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			if err := os.Symlink(header.Linkname, target); err != nil {
				return err
			}
		}
	}
}

func inside(dir, target string) bool {
	rel, err := filepath.Rel(dir, target)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}
