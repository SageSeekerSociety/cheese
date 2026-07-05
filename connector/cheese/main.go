// Command cheese is a thin, AI-friendly CLI wrapper over cheesed's local
// tool-RPC HTTP API (contract §7). All behavior lives in internal/cliapp;
// this file only builds and runs the urfave/cli App.
package main

import (
	"fmt"
	"os"

	"cheese/connector/cheese/internal/cliapp"
)

func main() {
	app := cliapp.New()
	if err := app.Run(os.Args); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
