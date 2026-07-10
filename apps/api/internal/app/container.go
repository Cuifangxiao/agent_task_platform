package app

import (
	"context"
	"database/sql"

	"agent-task-platform/apps/api/internal/config"
	"agent-task-platform/apps/api/internal/modules/agentconfig"
	"agent-task-platform/apps/api/internal/modules/auth"
	"agent-task-platform/apps/api/internal/modules/modelconfig"
	"agent-task-platform/apps/api/internal/modules/pptagent"
	"agent-task-platform/apps/api/internal/modules/provider/catalog"
	"agent-task-platform/apps/api/internal/modules/provider/credential"
	"agent-task-platform/apps/api/internal/modules/textgeneration"
	"agent-task-platform/apps/api/internal/modules/user"
	"agent-task-platform/apps/api/internal/platform/pythonruntime"
	"agent-task-platform/apps/api/internal/transport/http/router"

	"github.com/redis/go-redis/v9"
	"google.golang.org/grpc"
)

type container struct {
	routes router.Dependencies
	start  func(context.Context) error
}

func buildContainer(
	cfg *config.Config,
	db *sql.DB,
	redisClient *redis.Client,
	grpcConn *grpc.ClientConn,
) container {
	userRepo := user.NewRepository(db)
	modelConfigRepo := modelconfig.NewRepository(db)
	agentConfigRepo := agentconfig.NewRepository(db)
	presentationHistoryRepo := pptagent.NewHistoryRepository(db)
	presentationJobRepo := pptagent.NewJobRepository(db)
	runtimeClient := pythonruntime.NewClient(grpcConn)

	tokenManager := auth.NewManager(
		cfg.JWT.Secret,
		cfg.JWT.Issuer,
		cfg.JWT.AccessExpireDuration(),
		cfg.JWT.RefreshExpireDuration(),
	)
	sessionStore := auth.NewRedisSessionStore(redisClient)

	userService := user.NewService(userRepo, tokenManager, sessionStore)
	providerCatalogService := catalog.NewService(runtimeClient)
	providerCredentialService := credential.NewService(runtimeClient)
	modelConfigService := modelconfig.NewService(
		modelConfigRepo,
		providerCredentialService,
		providerCatalogService,
	)
	agentConfigService := agentconfig.NewService(agentConfigRepo, modelConfigRepo)
	textGenerationService := textgeneration.NewService(modelConfigRepo, runtimeClient)
	presentationService := pptagent.NewService(
		modelConfigRepo,
		agentConfigRepo,
		presentationHistoryRepo,
		runtimeClient,
		cfg.PythonAgent.ArtifactsDir,
		presentationJobRepo,
	)

	return container{
		routes: router.Dependencies{
			Users:               user.NewHandler(userService),
			TextGeneration:      textgeneration.NewHandler(textGenerationService),
			AgentConfigs:        agentconfig.NewHandler(agentConfigService),
			Presentations:       pptagent.NewHandler(presentationService),
			ModelConfigs:        modelconfig.NewHandler(modelConfigService),
			ProviderCatalog:     catalog.NewHandler(providerCatalogService),
			ProviderCredentials: credential.NewHandler(providerCredentialService),
			Tokens:              tokenManager,
			Sessions:            sessionStore,
		},
		start: presentationService.RecoverJobs,
	}
}
