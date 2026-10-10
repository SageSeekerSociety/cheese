package daemoncmd

import (
	"errors"
	"os/user"
	"strings"
	"testing"
)

func TestTheProfileNamesTheOwnersBwrapAndAllowsAUserNamespace(t *testing.T) {
	got := bwrapProfile("cheese-bwrap-ada", "/home/ada/.cheese/runtime/bubblewrap/bwrap")
	for _, want := range []string{
		"abi <abi/4.0>,",
		"profile cheese-bwrap-ada /home/ada/.cheese/runtime/bubblewrap/bwrap flags=(unconfined) {",
		"  userns,",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("profile lacks %q:\n%s", want, got)
		}
	}
}

func TestUnderSudoTheProfileIsForWhoeverRanSudo(t *testing.T) {
	lookup := func(name string) (*user.User, error) {
		return &user.User{Username: name, HomeDir: "/home/" + name}, nil
	}
	current := func() (*user.User, error) { return nil, errors.New("asked for root") }

	owner, err := sandboxOwner("ada", lookup, current)
	if err != nil || owner.Username != "ada" || owner.HomeDir != "/home/ada" {
		t.Fatalf("owner = %+v, %v", owner, err)
	}
}

func TestWithoutSudoTheProfileIsForRoot(t *testing.T) {
	lookup := func(string) (*user.User, error) { return nil, errors.New("looked someone up") }
	current := func() (*user.User, error) { return &user.User{Username: "root", HomeDir: "/root"}, nil }

	for _, sudoUser := range []string{"", "root"} {
		owner, err := sandboxOwner(sudoUser, lookup, current)
		if err != nil || owner.Username != "root" {
			t.Fatalf("SUDO_USER=%q: owner = %+v, %v", sudoUser, owner, err)
		}
	}
}
