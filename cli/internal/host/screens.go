package host

import "errors"

var errNoScreens = errors.New("this machine hosts no screens")

// screens is where a machine keeps the programs it hosts: a private tmux
// server on Unix, plain background processes on Windows (procscreen). Either
// way a screen outlives the connector, and the next one adopts it.
type screens interface {
	HasSession(name string) bool
	Adopt(name string) screen
	SaveIdentity(name, owner, data string) error
	Identities(owner string) (map[string]string, error)
	Spawn(name string, argv, env []string, cols, rows int) (screen, error)
}

// screen is one hosted program.
type screen interface {
	// Attach opens a terminal a viewer watches and types into.
	Attach(cols, rows int, onData func([]byte)) (viewer, error)
	// Close ends the program and forgets the screen.
	Close() error
}

// viewer is a terminal attached to a screen for someone watching it.
type viewer interface {
	Write([]byte) error
	Resize(cols, rows int) error
	Close()
}
