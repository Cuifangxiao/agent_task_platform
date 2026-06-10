package main

import (
	"flag"
	"log"

	"agent-task-platform/internal/bootstrap"
	"agent-task-platform/internal/router"
	"agent-task-platform/internal/user"
	"github.com/gin-gonic/gin"
)

func main() {
	confPath := flag.String("conf", "./configs/config.yaml", "config file path")
	flag.Parse()

	cfg, err := bootstrap.LoadConfig(*confPath)
	if err != nil {
		log.Fatalf("load config failed: %v", err)
	}

	db, err := bootstrap.NewMySQL(cfg.MySQL)
	if err != nil {
		log.Fatalf("connect mysql failed: %v", err)
	}
	defer db.Close()

	log.Printf("mysql connected: %s/%s", cfg.MySQL.Address(), cfg.MySQL.Database)

	userRepo := user.NewRepository(db)
	userService := user.NewService(userRepo)
	userHandler := user.NewHandler(userService)

	r := gin.Default()
	router.Register(r, userHandler)

	if err := r.Run(cfg.Server.Address()); err != nil {
		panic(err)
	}
}
