package main

import (
	"database/sql"
	"fmt"
	"log"

	"agent-task-platform/internal/auth"
	"agent-task-platform/internal/bootstrap"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/router"
	"agent-task-platform/internal/user"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
)

type application struct {
	cfg         *bootstrap.Config
	db          *sql.DB
	redisClient *redis.Client
	engine      *gin.Engine
}

type httpDependencies struct {
	userHandler        *user.Handler
	modelConfigHandler *modelconfig.Handler
	jwtManager         *auth.Manager
	sessionStore       auth.SessionStore
}

func newApplication(confPath string) (*application, error) {
	cfg, err := bootstrap.LoadConfig(confPath)
	if err != nil {
		return nil, fmt.Errorf("load config failed: %w", err)
	}

	db, err := bootstrap.NewMySQL(cfg.MySQL)
	if err != nil {
		return nil, fmt.Errorf("connect mysql failed: %w", err)
	}

	redisClient, err := bootstrap.NewRedis(cfg.Redis)
	if err != nil {
		closeWithLog("mysql", db.Close)
		return nil, fmt.Errorf("connect redis failed: %w", err)
	}

	log.Printf("mysql connected: %s/%s", cfg.MySQL.Address(), cfg.MySQL.Database)
	log.Printf("redis connected: %s/%d", cfg.Redis.Address(), cfg.Redis.DB)

	deps := buildHTTPDependencies(cfg, db, redisClient)
	engine := gin.Default()
	router.Register(
		engine,
		deps.userHandler,
		deps.modelConfigHandler,
		deps.jwtManager,
		deps.sessionStore,
	)

	return &application{
		cfg:         cfg,
		db:          db,
		redisClient: redisClient,
		engine:      engine,
	}, nil
}

func buildHTTPDependencies(cfg *bootstrap.Config, db *sql.DB, redisClient *redis.Client) httpDependencies {
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

	return httpDependencies{
		userHandler:        user.NewHandler(userService),
		modelConfigHandler: modelconfig.NewHandler(modelConfigService),
		jwtManager:         jwtManager,
		sessionStore:       sessionStore,
	}
}

func (a *application) Run() error {
	if err := a.engine.Run(a.cfg.Server.Address()); err != nil {
		return fmt.Errorf("run server failed: %w", err)
	}

	return nil
}

func (a *application) Close() {
	closeWithLog("redis", a.redisClient.Close)
	closeWithLog("mysql", a.db.Close)
}

func closeWithLog(name string, closeFn func() error) {
	if closeFn == nil {
		return
	}

	if err := closeFn(); err != nil {
		log.Printf("close %s failed: %v", name, err)
	}
}
