package main

import (
	"flag"
	"log"
)

func main() {
	if err := run(parseConfigPath()); err != nil {
		log.Fatal(err)
	}
}

func parseConfigPath() string {
	confPath := flag.String("conf", "./configs/config.yaml", "config file path")
	flag.Parse()

	return *confPath
}

func run(confPath string) error {
	app, err := newApplication(confPath)
	if err != nil {
		return err
	}
	defer app.Close()

	log.Printf("server listening on %s", app.cfg.Server.Address())

	return app.Run()
}
