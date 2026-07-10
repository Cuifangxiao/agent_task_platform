// Package app composes the API process and owns its lifecycle.
package app

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"agent-task-platform/apps/api/internal/config"
	"agent-task-platform/apps/api/internal/platform/logging"
	"agent-task-platform/apps/api/internal/platform/mysql"
	"agent-task-platform/apps/api/internal/platform/pythonruntime"
	redisstore "agent-task-platform/apps/api/internal/platform/redis"
	"agent-task-platform/apps/api/internal/transport/http/router"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
	"google.golang.org/grpc"
)

const (
	startupTimeout  = 30 * time.Second
	shutdownTimeout = 5 * time.Second
)

type Application struct {
	db          *sql.DB
	redisClient *redis.Client
	grpcConn    *grpc.ClientConn
	httpServer  *http.Server
	logger      *slog.Logger
	startup     func(context.Context) error
}

func New(configPath string) (*Application, error) {
	cfg, err := config.Load(configPath)
	if err != nil {
		return nil, fmt.Errorf("load config: %w", err)
	}

	logger := logging.New(cfg.App.Env)
	slog.SetDefault(logger)
	if cfg.App.Env == "production" {
		gin.SetMode(gin.ReleaseMode)
	}

	db, err := mysql.Open(cfg.MySQL)
	if err != nil {
		return nil, fmt.Errorf("connect mysql: %w", err)
	}

	grpcConn, err := pythonruntime.Dial(cfg.PythonAgent)
	if err != nil {
		closeWithLog(logger, "mysql", db.Close)
		return nil, fmt.Errorf("connect agent runtime: %w", err)
	}

	redisClient, err := redisstore.Open(cfg.Redis)
	if err != nil {
		closeWithLog(logger, "agent runtime grpc", grpcConn.Close)
		closeWithLog(logger, "mysql", db.Close)
		return nil, fmt.Errorf("connect redis: %w", err)
	}

	container := buildContainer(cfg, db, redisClient, grpcConn)
	engine := router.New(container.routes, logger)

	logger.Info("mysql connected", "address", cfg.MySQL.Address(), "database", cfg.MySQL.Database)
	logger.Info("redis connected", "address", cfg.Redis.Address(), "db", cfg.Redis.DB)
	logger.Info("agent runtime configured", "target", cfg.PythonAgent.GRPCAddress())

	return &Application{
		db:          db,
		redisClient: redisClient,
		grpcConn:    grpcConn,
		logger:      logger,
		httpServer: &http.Server{
			Addr:              cfg.Server.Address(),
			Handler:           engine,
			ReadHeaderTimeout: 5 * time.Second,
		},
		startup: func(ctx context.Context) error {
			applied, err := mysql.Migrate(ctx, db, configPath)
			if err != nil {
				return fmt.Errorf("apply mysql migrations: %w", err)
			}
			if len(applied) > 0 {
				logger.Info("mysql migrations applied", "migrations", applied)
			}
			return container.start(ctx)
		},
	}, nil
}

func (a *Application) Run(ctx context.Context) error {
	startupCtx, cancelStartup := context.WithTimeout(ctx, startupTimeout)
	defer cancelStartup()
	if a.startup != nil {
		if err := a.startup(startupCtx); err != nil {
			return fmt.Errorf("start application: %w", err)
		}
	}

	errCh := make(chan error, 1)
	go func() {
		a.logger.Info("server listening", "address", a.httpServer.Addr)
		if err := a.httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
		close(errCh)
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
		defer cancel()
		if err := a.httpServer.Shutdown(shutdownCtx); err != nil {
			return fmt.Errorf("shutdown server: %w", err)
		}
		a.logger.Info("server shutdown complete")
		return nil
	case err := <-errCh:
		if err != nil {
			return fmt.Errorf("run server: %w", err)
		}
		return nil
	}
}

func (a *Application) Close() {
	if a == nil {
		return
	}
	if a.grpcConn != nil {
		closeWithLog(a.logger, "agent runtime grpc", a.grpcConn.Close)
	}
	if a.redisClient != nil {
		closeWithLog(a.logger, "redis", a.redisClient.Close)
	}
	if a.db != nil {
		closeWithLog(a.logger, "mysql", a.db.Close)
	}
}

func closeWithLog(logger *slog.Logger, name string, closeFn func() error) {
	if closeFn == nil {
		return
	}
	if err := closeFn(); err != nil {
		if logger == nil {
			logger = slog.Default()
		}
		logger.Error("close resource", "resource", name, "error", err)
	}
}
