//go:build !windows

// Package devenv places what the server's commands expect to find on a machine
// where the system does not already provide it. Linux and macOS provide it.
package devenv

import (
	"context"
	"io"
)

// Ensure has nothing to place on this platform.
func Ensure(context.Context, string, io.Writer) error { return nil }
