// Command cheesed is the always-on, tmux-backed agent daemon (contract §0):
// a thin, transparent relay + local process manager sitting between the
// backend orchestrator and a Claude Code session living in a private tmux
// server.
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/urfave/cli/v2"

	"cheese/connector/cheesed/internal/app"
	"cheese/connector/cheesed/internal/config"
)

func main() {
	cliApp := &cli.App{
		Name:  "cheesed",
		Usage: "tmux-backed agent daemon: relays a Claude Code session to the cheese backend",
		Commands: []*cli.Command{
			runCommand(),
		},
	}

	if err := cliApp.Run(os.Args); err != nil {
		log.Fatal(err)
	}
}

func runCommand() *cli.Command {
	return &cli.Command{
		Name:  "run",
		Usage: "start the daemon: manage the tmux session, dial the backend, and serve the local tool API",
		Flags: []cli.Flag{
			&cli.StringFlag{
				Name:     "config",
				Usage:    "path to the cheesed JSON config file",
				Required: true,
			},
		},
		Action: runAction,
	}
}

func runAction(c *cli.Context) error {
	cfg, err := config.Load(c.String("config"))
	if err != nil {
		return fmt.Errorf("cheesed: %w", err)
	}

	a, err := app.New(cfg)
	if err != nil {
		return fmt.Errorf("cheesed: %w", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := a.Run(ctx); err != nil {
		return fmt.Errorf("cheesed: %w", err)
	}
	return nil
}
