// Package logging configures structured application logging.
package logging

import (
	"log/slog"
	"os"
	"strings"
)

func New(env string) *slog.Logger {
	env = strings.ToLower(strings.TrimSpace(env))

	opts := &slog.HandlerOptions{
		Level: loggerLevel(env),
	}

	if env == "production" || env == "prod" {
		return slog.New(slog.NewJSONHandler(os.Stdout, opts))
	}

	return slog.New(slog.NewTextHandler(os.Stdout, opts))
}

func loggerLevel(env string) slog.Level {
	switch env {
	case "production", "prod":
		return slog.LevelInfo
	case "test":
		return slog.LevelWarn
	default:
		return slog.LevelDebug
	}
}
