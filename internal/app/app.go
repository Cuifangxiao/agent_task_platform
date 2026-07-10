package app

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"agent-task-platform/internal/agent"
	"agent-task-platform/internal/agentconfig"
	"agent-task-platform/internal/auth"
	"agent-task-platform/internal/bootstrap"
	"agent-task-platform/internal/middleware"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/pptagent"
	"agent-task-platform/internal/providercatalog"
	"agent-task-platform/internal/providercredential"
	"agent-task-platform/internal/pythonruntime"
	"agent-task-platform/internal/router"
	"agent-task-platform/internal/user"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const shutdownTimeout = 5 * time.Second

type Application struct {
	db          *sql.DB
	redisClient *redis.Client
	grpcConn    *grpc.ClientConn
	httpServer  *http.Server
	logger      *slog.Logger
}

type httpDependencies struct {
	userHandler               *user.Handler
	agentHandler              *agent.Handler
	agentConfigHandler        *agentconfig.Handler
	pptAgentHandler           *pptagent.Handler
	modelConfigHandler        *modelconfig.Handler
	providerCatalogHandler    *providercatalog.Handler
	providerCredentialHandler *providercredential.Handler
	jwtManager                *auth.Manager
	sessionStore              auth.SessionStore
}

func New(confPath string) (*Application, error) {
	cfg, err := bootstrap.LoadConfig(confPath)
	if err != nil {
		return nil, fmt.Errorf("load config failed: %w", err)
	}

	logger := bootstrap.NewLogger(cfg.App.Env)
	slog.SetDefault(logger)

	if cfg.App.Env == "production" {
		gin.SetMode(gin.ReleaseMode)
	}

	db, err := bootstrap.NewMySQL(cfg.MySQL)
	if err != nil {
		return nil, fmt.Errorf("connect mysql failed: %w", err)
	}

	migrationCtx, cancelMigrations := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancelMigrations()

	appliedMigrations, err := bootstrap.ApplyMySQLMigrations(migrationCtx, db, confPath)
	if err != nil {
		closeWithLog(logger, "mysql", db.Close)
		return nil, fmt.Errorf("apply mysql migrations failed: %w", err)
	}
	if len(appliedMigrations) > 0 {
		logger.Info("mysql migrations applied", "migrations", appliedMigrations)
	}

	grpcConn, err := grpc.NewClient(
		cfg.PythonAgent.GRPCAddress(),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		closeWithLog(logger, "mysql", db.Close)
		return nil, fmt.Errorf("connect python agent grpc failed: %w", err)
	}

	redisClient, err := bootstrap.NewRedis(cfg.Redis)
	if err != nil {
		closeWithLog(logger, "python agent grpc", grpcConn.Close)
		closeWithLog(logger, "mysql", db.Close)
		return nil, fmt.Errorf("connect redis failed: %w", err)
	}

	logger.Info("mysql connected", "address", cfg.MySQL.Address(), "database", cfg.MySQL.Database)
	logger.Info("redis connected", "address", cfg.Redis.Address(), "db", cfg.Redis.DB)
	logger.Info("python agent grpc target configured", "target", cfg.PythonAgent.GRPCAddress())

	deps := buildHTTPDependencies(cfg, db, redisClient, grpcConn)
	engine := gin.New()
	engine.Use(
		middleware.RequestLogger(logger),
		middleware.Recovery(logger),
	)
	router.Register(
		engine,
		deps.userHandler,
		deps.agentHandler,
		deps.agentConfigHandler,
		deps.pptAgentHandler,
		deps.modelConfigHandler,
		deps.providerCatalogHandler,
		deps.providerCredentialHandler,
		deps.jwtManager,
		deps.sessionStore,
	)

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
	}, nil
}

func buildHTTPDependencies(
	cfg *bootstrap.Config,
	db *sql.DB,
	redisClient *redis.Client,
	grpcConn *grpc.ClientConn,
) httpDependencies {
	userRepo := user.NewRepository(db)
	modelConfigRepo := modelconfig.NewRepository(db)
	agentConfigRepo := agentconfig.NewRepository(db)
	pptHistoryRepo := pptagent.NewHistoryRepository(db)
	pptJobRepo := pptagent.NewJobRepository(db)
	pythonRuntimeClient := pythonruntime.NewClient(grpcConn)

	jwtManager := auth.NewManager(
		cfg.JWT.Secret,
		cfg.JWT.Issuer,
		cfg.JWT.AccessExpireDuration(),
		cfg.JWT.RefreshExpireDuration(),
	)
	sessionStore := auth.NewRedisSessionStore(redisClient)

	userService := user.NewService(userRepo, jwtManager, sessionStore)
	providerCatalogService := providercatalog.NewService(pythonRuntimeClient)
	providerCredentialService := providercredential.NewService(pythonRuntimeClient)
	modelConfigService := modelconfig.NewService(
		modelConfigRepo,
		providerCredentialService,
		providerCatalogService,
	)
	agentConfigService := agentconfig.NewService(agentConfigRepo, modelConfigRepo)
	agentService := agent.NewService(modelConfigRepo, pythonRuntimeClient)
	pptAgentService := pptagent.NewService(modelConfigRepo, agentConfigRepo, pptHistoryRepo, pythonRuntimeClient, "", pptJobRepo)
	if err := pptAgentService.RecoverJobs(context.Background()); err != nil {
		slog.Error("recover ppt deck jobs", "error", err)
	}

	return httpDependencies{
		userHandler:               user.NewHandler(userService),
		agentHandler:              agent.NewHandler(agentService),
		agentConfigHandler:        agentconfig.NewHandler(agentConfigService),
		pptAgentHandler:           pptagent.NewHandler(pptAgentService),
		modelConfigHandler:        modelconfig.NewHandler(modelConfigService),
		providerCatalogHandler:    providercatalog.NewHandler(providerCatalogService),
		providerCredentialHandler: providercredential.NewHandler(providerCredentialService),
		jwtManager:                jwtManager,
		sessionStore:              sessionStore,
	}
}

func (a *Application) Run(ctx context.Context) error {
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
			return fmt.Errorf("shutdown server failed: %w", err)
		}

		a.logger.Info("server shutdown complete")
		return nil
	case err := <-errCh:
		if err != nil {
			return fmt.Errorf("run server failed: %w", err)
		}

		return nil
	}
}

func (a *Application) Close() {
	if a == nil {
		return
	}

	if a.grpcConn != nil {
		closeWithLog(a.logger, "python agent grpc", a.grpcConn.Close)
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
		logger.Error("close resource failed", "resource", name, "error", err)
	}
}
