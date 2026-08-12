package update

import "testing"

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
	cases := []struct {
		base, dir, want string
	}{
		// The last segment is `cheesehost`, not `cheese`: that is the name the
		// server publishes at /connector/latest/{target}/cheesehost, and the one
		// install.sh downloads. Asking for `cheese` is a plain 404.
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
	if _, err := binaryURL("not-a-url", "linux-x86_64"); err == nil {
		t.Error("expected error for base without host")
	}
}
