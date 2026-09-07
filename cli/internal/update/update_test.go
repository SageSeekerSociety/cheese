package update

import (
	"crypto/sha256"
	"fmt"
	"os"
	"testing"
)

func TestPlatformDir(t *testing.T) {
	cases := []struct {
		goos, goarch, want string
	}{
		{"linux", "amd64", "linux-amd64"},
		{"linux", "arm64", "linux-arm64"},
		{"darwin", "amd64", "darwin-amd64"},
		{"darwin", "arm64", "darwin-arm64"},
	}
	for _, c := range cases {
		got, err := platformDir(c.goos, c.goarch)
		if err != nil {
			t.Fatalf("platformDir(%q,%q) error: %v", c.goos, c.goarch, err)
		}
		if got != c.want {
			t.Errorf("platformDir(%q,%q) = %q, want %q", c.goos, c.goarch, got, c.want)
		}
	}
}

func TestPlatformDirUnsupported(t *testing.T) {
	if _, err := platformDir("windows", "amd64"); err == nil {
		t.Error("expected error for windows")
	}
	if _, err := platformDir("linux", "riscv64"); err == nil {
		t.Error("expected error for riscv64")
	}
}

func TestBinaryURL(t *testing.T) {
	// The artifact is named cheesehost — the only name the server serves
	// (installer.py's `/latest/{target}/cheesehost`); `.../cheese` is a 404.
	// Dirs are Go-style (platformDir's output), which is also all the server's
	// target validation accepts — never uname-style linux-x86_64.
	cases := []struct {
		base, dir, want string
	}{
		{
			"https://cheese.ruc.edu.cn/api", "linux-amd64",
			"https://cheese.ruc.edu.cn/connector/latest/linux-amd64/cheesehost",
		},
		{
			"http://127.0.0.1:8080", "darwin-arm64",
			"http://127.0.0.1:8080/connector/latest/darwin-arm64/cheesehost",
		},
		{
			"https://example.com/", "linux-arm64",
			"https://example.com/connector/latest/linux-arm64/cheesehost",
		},
	}
	for _, c := range cases {
		got, err := binaryURL(c.base, c.dir)
		if err != nil {
			t.Fatalf("binaryURL(%q,%q) error: %v", c.base, c.dir, err)
		}
		if got != c.want {
			t.Errorf("binaryURL(%q,%q) = %q, want %q", c.base, c.dir, got, c.want)
		}
	}
}

func TestBinaryURLBadBase(t *testing.T) {
	if _, err := binaryURL("not-a-url", "linux-amd64"); err == nil {
		t.Error("expected error for base without host")
	}
}

// The digest a connector announces must be the digest of the very bytes on
// disk — that identity is what lets the server decide the machine is running
// something other than what it serves, and an update installs a build by
// renaming exactly those bytes into place.
func TestSelfDigestIsTheHashOfTheRunningBinary(t *testing.T) {
	got, err := SelfDigest()
	if err != nil {
		t.Fatalf("SelfDigest error: %v", err)
	}
	self, err := SelfPath()
	if err != nil {
		t.Fatalf("SelfPath error: %v", err)
	}
	raw, err := os.ReadFile(self)
	if err != nil {
		t.Fatalf("read %s: %v", self, err)
	}
	want := fmt.Sprintf("%x", sha256.Sum256(raw))
	if got != want {
		t.Errorf("SelfDigest() = %q, want %q", got, want)
	}
	// Stable: two calls on an unchanged binary must not disagree, or every
	// reconnect would look like a different build and force an update.
	again, err := SelfDigest()
	if err != nil || again != got {
		t.Errorf("SelfDigest() second call = %q (err %v), want %q", again, err, got)
	}
}
