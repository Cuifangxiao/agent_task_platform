package main

import (
	"flag"
	"log"

	"agent-task-platform/internal/auth"
	"agent-task-platform/internal/bootstrap"
	"agent-task-platform/internal/modelconfig"
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
	defer func() {
		if err := db.Close(); err != nil {
			log.Printf("close mysql failed: %v", err)
		}
	}()
	log.Printf("mysql connected: %s/%s", cfg.MySQL.Address(), cfg.MySQL.Database)

	redisClient, err := bootstrap.NewRedis(cfg.Redis)
	if err != nil {
		log.Fatalf("connect redis failed: %v", err)
	}
	defer func() {
		if err := redisClient.Close(); err != nil {
			log.Printf("close redis failed: %v", err)
		}
	}()

	log.Printf("redis connected: %s/%d", cfg.Redis.Address(), cfg.Redis.DB)

	userRepo := user.NewRepository(db)
	modelConfigRepo := modelconfig.NewRepository(db)
	jwtManager := auth.NewManager(
		cfg.JWT.Secret,
		cfg.JWT.Issuer,
		cfg.JWT.AccessExpireDuration(),
		cfg.JWT.RefreshExpireDuration(),
	)
	sessionStore := auth.NewRedisSessionStore(redisClient)
	userService := user.NewService(userRepo, jwtManager, sessionStore)
	modelConfigService := modelconfig.NewService(modelConfigRepo)
	userHandler := user.NewHandler(userService)
	modelConfigHandler := modelconfig.NewHandler(modelConfigService)

	r := gin.Default()
	router.Register(r, userHandler, modelConfigHandler, jwtManager, sessionStore)

	if err := r.Run(cfg.Server.Address()); err != nil {
		panic(err)
	}
}
