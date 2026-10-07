package daemoncmd

import (
	"context"
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/SageSeekerSociety/cheese/cli/internal/claudecode"
	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/ui"
)

// claudeCmd is the owner's own Claude Code as the platform runs it: logged in
// once here, under a directory of the platform's, and used by every session of
// the owner's Claude Code on this machine. The owner's own `~/.claude` is not
// read or touched.
func claudeCmd(cfgPath *string, withConfig func(*cobra.Command) *cobra.Command) *cobra.Command {
	login := withConfig(&cobra.Command{
		Use:   "login",
		Short: "Log in the Claude Code the platform runs on this machine",
		Long: "Installs the Claude Code build the platform runs, if this machine does not\n" +
			"have it yet, and logs it in with your own Claude account or API key. The\n" +
			"login is kept apart from your own Claude Code (~/.claude), which keeps its\n" +
			"own login and settings.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			base, err := serverBase(*cfgPath)
			if err != nil {
				return err
			}
			ctx := context.Background()
			binary, err := claudecode.Ensure(ctx, base, os.Stdout)
			if err != nil {
				return err
			}
			loginDir, err := claudecode.LoginDir()
			if err != nil {
				return err
			}
			if err := os.MkdirAll(loginDir, 0o700); err != nil {
				return err
			}
			cmd := claudecode.Command(ctx, binary, loginDir, "auth", "login")
			cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("claude: login did not finish: %w", err)
			}
			return printClaudeStatus(ctx, binary, loginDir)
		},
	})

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
			loginDir, err := claudecode.LoginDir()
			if err != nil {
				return err
			}
			return printClaudeStatus(ctx, binary, loginDir)
		},
	})

	logout := withConfig(&cobra.Command{
		Use:   "logout",
		Short: "Log out the platform's Claude Code on this machine",
		Long:  "Logs out the Claude Code the platform runs. Your own Claude Code (~/.claude) stays logged in.",
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
				fmt.Println("Already logged out.")
				return nil
			}
			loginDir, err := claudecode.LoginDir()
			if err != nil {
				return err
			}
			cmd := claudecode.Command(ctx, binary, loginDir, "auth", "logout")
			cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("claude: logout: %w", err)
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

// serverBase is the server this machine belongs to, which names the Claude Code
// build to run and serves it.
func serverBase(cfgPath string) (string, error) {
	cfg, err := config.Load(cfgPath)
	if err != nil || cfg == nil || cfg.Base == "" {
		return "", fmt.Errorf("no server known yet — run `cheesehost link connect <server-url>` first")
	}
	return cfg.Base, nil
}

func printClaudeStatus(ctx context.Context, binary, loginDir string) error {
	status, err := claudecode.ReadStatus(ctx, binary, loginDir)
	if err != nil {
		return err
	}
	if !status.LoggedIn {
		ui.Warn("Claude Code is not logged in.")
		ui.Hint("`cheesehost claude login` to log in")
		return nil
	}
	detail := status.AuthMethod
	if status.SubscriptionType != "" {
		detail += " · " + status.SubscriptionType
	}
	ui.OK("Claude Code is logged in (%s).", detail)
	return nil
}
