package main

import (
	"context"
	"flag"
	"log"
	"os/signal"
	"syscall"

	"agent-task-platform/apps/api/internal/app"
)

func main() {
	if err := run(parseConfigPath()); err != nil {
		log.Fatal(err)
	}
}

func parseConfigPath() string {
	confPath := flag.String("conf", "./apps/api/config/config.yaml", "config file path")
	flag.Parse()

	return *confPath
}

func run(confPath string) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	application, err := app.New(confPath)
	if err != nil {
		return err
	}
	defer application.Close()

	return application.Run(ctx)
}
