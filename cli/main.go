// Command cheese is the single binary a user installs to attach a machine to a
// server. To a human it is just `cheese` — `cheese auth login` to log in,
// `cheese link auto-connect` to stay connected in the background. The full
// request/response API the server publishes (generated live from its OpenAPI)
// lives under `cheese api`, for scripts and automated callers. There is
// deliberately no separate "daemon" the user must know about.
package main

import (
	"fmt"
	"os"

	"github.com/rest-sh/restish/cli"
	"github.com/spf13/pflag"

	"github.com/SageSeekerSociety/cheese/cli/internal/apicli"
	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/daemoncmd"
	"github.com/SageSeekerSociety/cheese/cli/internal/sysproxy"
)

// version is overridable at link time:
//
//	-ldflags "-X main.version=1.2.3"
var version = "dev"

func main() {
	// Everything this binary reaches out to — the control link, the runtime it
	// fetches, its own update, the server's API — should go the way this
	// computer is set to reach the network. On Windows that setting lives in
	// the system proxy, not the environment Go reads, so a campus network that
	// only admits the proxy left the connector dialling into a wall. Wired
	// before anything can make a request.
	sysproxy.Install()

	// API hydration happens before Cobra executes, so select the profile first.
	selection := pflag.NewFlagSet("profile", pflag.ContinueOnError)
	selection.ParseErrorsWhitelist.UnknownFlags = true
	selection.StringVar(&config.CLIProfile, "profile", "default", "enrollment profile")
	// Declared so that -h/--help is Cobra's to answer: an undeclared help flag
	// makes pflag stop here with ErrHelp.
	selection.BoolP("help", "h", false, "")
	if err := selection.Parse(os.Args[1:]); err != nil {
		os.Exit(1)
	}
	if _, err := config.ProfilePath(config.CLIProfile); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	config.CLIProfileSet = selection.Changed("profile")
	// apicli.Setup initializes Restish (its cobra root becomes `cheese`) and
	// returns the `api` subcommand hosting every generated operation.
	apiCmd := apicli.Setup(version)
	cli.Root.PersistentFlags().String("profile", config.CLIProfile, "enrollment profile to use")

	// The user-facing service lifecycle: connect / install / start / stop / …
	for _, c := range daemoncmd.Commands() {
		cli.Root.AddCommand(c)
	}

	// Only `cheese api …` needs the live spec; hydrating it costs a network
	// round trip, so daemon commands (which must work offline / on a fresh box)
	// never trigger it.
	if args := selection.Args(); len(args) > 0 && args[0] == "api" {
		apicli.LoadOperations(apiCmd)
	}

	os.Exit(apicli.Run())
}
