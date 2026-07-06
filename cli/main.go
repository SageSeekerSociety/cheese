// Command cheese is the single binary a user installs to attach a machine to a
// server. To a human it is just `cheese` — `cheese auth login` to log in,
// `cheese autoconnect enable` to stay connected in the background. The full
// request/response API the server publishes (generated live from its OpenAPI)
// lives under `cheese api`, for scripts and automated callers. There is
// deliberately no separate "daemon" the user must know about.
package main

import (
	"os"

	"github.com/rest-sh/restish/cli"

	"github.com/SageSeekerSociety/cheese-backend-py/cli/internal/apicli"
	"github.com/SageSeekerSociety/cheese-backend-py/cli/internal/daemoncmd"
)

// version is overridable at link time:
//
//	-ldflags "-X main.version=1.2.3"
var version = "dev"

func main() {
	// apicli.Setup initializes Restish (its cobra root becomes `cheese`) and
	// returns the `api` subcommand hosting every generated operation.
	apiCmd := apicli.Setup(version)

	// The user-facing service lifecycle: connect / install / start / stop / …
	for _, c := range daemoncmd.Commands() {
		cli.Root.AddCommand(c)
	}

	// Only `cheese api …` needs the live spec; hydrating it costs a network
	// round trip, so daemon commands (which must work offline / on a fresh box)
	// never trigger it.
	if len(os.Args) > 1 && os.Args[1] == "api" {
		apicli.LoadOperations(apiCmd)
	}

	os.Exit(apicli.Run())
}
