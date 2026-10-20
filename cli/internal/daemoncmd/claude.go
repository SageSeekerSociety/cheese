package daemoncmd

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/SageSeekerSociety/cheese/cli/internal/claudecode"
	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/place"
	"github.com/SageSeekerSociety/cheese/cli/internal/ui"
)

// claudeCmd is the owner's own Claude Code as the platform runs it: each login
// made here is kept in a directory of the platform's and used by the sessions
// of the owner's Claude Code added with it. The owner's own `~/.claude` is not
// read or touched.
func claudeCmd(cfgPath *string, withConfig func(*cobra.Command) *cobra.Command) *cobra.Command {
	var loginID string
	var loginTicket, loginServer string
	login := withConfig(&cobra.Command{
		Use:   "login",
		Short: "Log in the Claude Code the platform runs on this machine",
		Long: "Installs the Claude Code build the platform runs, if this machine does not\n" +
			"have it yet, and adds a login with your Claude subscription. Each\n" +
			"run adds another, so one machine can hold several accounts; --login logs\n" +
			"one of them in again. Logins are kept apart from your own Claude Code\n" +
			"(~/.claude), which keeps its own login and settings. The login opens in\n" +
			"the browser; with no terminal attached it finishes there, so the desktop\n" +
			"app can run it too.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			if loginTicket == "" && loginID == "" {
				return errors.New("start a subscription login from the team's Models page and use its command")
			}
			if loginTicket != "" {
				var err error
				loginID, err = registerModelLogin(cfgPath, loginServer, loginTicket)
				if err != nil {
					return err
				}
			}
			base, err := serverBase(*cfgPath)
			if err != nil {
				return err
			}
			ctx := context.Background()
			binary, err := claudecode.Ensure(ctx, base, os.Stdout)
			if err != nil {
				return err
			}
			id, loginDir, made, err := loginDirectory(loginID)
			if err != nil {
				return err
			}
			// The latest choice wins: logging in with an account stops using a
			// model service set before.
			if err := claudecode.ForgetModelService(loginDir); err != nil {
				return err
			}
			cmd := claudecode.Command(ctx, binary, loginDir, "auth", "login", "--claudeai")
			cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
			if err := cmd.Run(); err != nil {
				// A login that never finished is not one to keep.
				if made {
					os.RemoveAll(loginDir)
				}
				return fmt.Errorf("claude: login did not finish: %w", err)
			}
			fmt.Printf("Login %s:\n", id)
			if err := printClaudeStatus(ctx, binary, id, loginDir); err != nil {
				return err
			}
			if loginTicket != "" {
				return connectModelLogin(*cfgPath)
			}
			return nil
		},
	})

	login.Flags().StringVar(&loginTicket, "ticket", "", "team model-login authorization from the Models page")
	login.Flags().StringVar(&loginServer, "server", "", "server URL shown with the model-login authorization")
	login.MarkFlagsRequiredTogether("ticket", "server")
	login.Flags().StringVar(&loginID, "login", "",
		"log in again the login with this id (`cheesehost claude status` lists them) instead of adding one")

	status := withConfig(&cobra.Command{
		Use:   "status",
		Short: "Show whether the platform's Claude Code on this machine is logged in",
		Args:  cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			base, err := serverBase(*cfgPath)
			if err != nil {
				return err
			}
			ctx := context.Background()
			binary, installed, err := claudecode.Installed(ctx, base)
			if err != nil {
				return err
			}
			if !installed {
				ui.Warn("Claude Code is not set up on this machine yet.")
				ui.Hint("`cheesehost claude login` to install and log it in")
				return nil
			}
			ids, err := claudecode.Logins()
			if err != nil {
				return err
			}
			if len(ids) == 0 {
				ui.Warn("Claude Code is not logged in.")
				ui.Hint("`cheesehost claude login` to log in")
				return nil
			}
			for _, id := range ids {
				loginDir, err := claudecode.LoginDir(id)
				if err != nil {
					return err
				}
				fmt.Printf("Login %s:\n", id)
				if err := printClaudeStatus(ctx, binary, id, loginDir); err != nil {
					return err
				}
			}
			return nil
		},
	})

	logout := withConfig(&cobra.Command{
		Use:   "logout [login-id]",
		Short: "Log out one of the platform's Claude Code logins on this machine",
		Long: "Logs out one login of the Claude Code the platform runs and forgets its\n" +
			"model service; `cheesehost claude status` lists them, and a machine with\n" +
			"only one needs no id. The other logins and your own Claude Code (~/.claude)\n" +
			"stay logged in.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			id, err := loginToLogOut(args)
			if err != nil {
				return err
			}
			loginDir, err := claudecode.LoginDir(id)
			if err != nil {
				return err
			}
			if err := claudecode.ForgetModelService(loginDir); err != nil {
				return err
			}
			base, err := serverBase(*cfgPath)
			if err != nil {
				return err
			}
			ctx := context.Background()
			binary, installed, err := claudecode.Installed(ctx, base)
			if err != nil {
				return err
			}
			if installed {
				cmd := claudecode.Command(ctx, binary, loginDir, "auth", "logout")
				cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
				if err := cmd.Run(); err != nil {
					return fmt.Errorf("claude: logout: %w", err)
				}
			}
			if err := claudecode.RemoveLogin(id); err != nil {
				return err
			}
			ui.OK("Logged out. Your own Claude Code is unchanged.")
			return nil
		},
	})

	group := &cobra.Command{
		Use:   "claude",
		Short: "Your own Claude Code, as the platform runs it on this machine",
	}
	group.AddCommand(login, status, logout)
	return group
}

// loginToLogOut is the login named, or the machine's only one.
func loginToLogOut(args []string) (string, error) {
	if len(args) == 1 {
		return args[0], nil
	}
	ids, err := claudecode.Logins()
	if err != nil {
		return "", err
	}
	switch len(ids) {
	case 0:
		return place.FirstClaudeLogin, nil
	case 1:
		return ids[0], nil
	}
	return "", fmt.Errorf("this machine has %d logins (%s); name the one to log out",
		len(ids), strings.Join(ids, ", "))
}

// serverBase is the server this machine belongs to, which names the Claude Code
// build to run and serves it.
func serverBase(cfgPath string) (string, error) {
	cfg, err := config.Load(cfgPath)
	if err != nil || cfg == nil || cfg.Base == "" {
		return "", errors.New("no server known yet; use the subscription login command from the team's Models page")
	}
	return cfg.Base, nil
}

// loginDirectory is the login a `claude login` is for: the one named, or a new
// one when none is (made says so, for a failed login to leave nothing behind).
func loginDirectory(id string) (string, string, bool, error) {
	if id == "" {
		id, dir, err := claudecode.NewLogin()
		return id, dir, true, err
	}
	dir, err := claudecode.LoginDir(id)
	if err != nil {
		return "", "", false, err
	}
	_, missing := os.Stat(dir)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", "", false, fmt.Errorf("claude: %w", err)
	}
	return id, dir, errors.Is(missing, os.ErrNotExist), nil
}

// printClaudeStatus says how the login this machine calls id stands. A login
// that is not logged in is named in the hint: logging that one in again brings
// back the agents added with it, where a plain `claude login` adds another.
func printClaudeStatus(ctx context.Context, binary, id, loginDir string) error {
	service, err := claudecode.LoadModelService(loginDir)
	if err != nil {
		return err
	}
	if service != nil {
		host := service.BaseURL
		if u, err := url.Parse(service.BaseURL); err == nil && u.Host != "" {
			host = u.Host
		}
		ui.OK("Claude Code uses the model service at %s (%s).", host, service.Model)
		return nil
	}
	status, err := claudecode.ReadStatus(ctx, binary, loginDir)
	if err != nil {
		return err
	}
	if !status.LoggedIn {
		ui.Warn("Claude Code is not logged in.")
		ui.Hint("`cheesehost claude login --login %s` to log it in again", id)
		return nil
	}
	detail := status.AuthMethod
	if status.SubscriptionType != "" {
		detail += " · " + status.SubscriptionType
	}
	ui.OK("Claude Code is logged in (%s).", detail)
	return nil
}
