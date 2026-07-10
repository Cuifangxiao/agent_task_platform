// Package router owns the public HTTP surface. Business handlers stay grouped
// by module while this package defines versioning and compatibility policy.
package router

import (
	"log/slog"
	"strings"

	"agent-task-platform/apps/api/internal/modules/agentconfig"
	"agent-task-platform/apps/api/internal/modules/auth"
	"agent-task-platform/apps/api/internal/modules/modelconfig"
	"agent-task-platform/apps/api/internal/modules/pptagent"
	"agent-task-platform/apps/api/internal/modules/provider/catalog"
	"agent-task-platform/apps/api/internal/modules/provider/credential"
	"agent-task-platform/apps/api/internal/modules/textgeneration"
	"agent-task-platform/apps/api/internal/modules/user"
	"agent-task-platform/apps/api/internal/transport/http/middleware"
	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

type Dependencies struct {
	Users               *user.Handler
	TextGeneration      *textgeneration.Handler
	AgentConfigs        *agentconfig.Handler
	Presentations       *pptagent.Handler
	ModelConfigs        *modelconfig.Handler
	ProviderCatalog     *catalog.Handler
	ProviderCredentials *credential.Handler
	Tokens              *auth.Manager
	Sessions            auth.SessionStore
}

func New(deps Dependencies, logger *slog.Logger) *gin.Engine {
	engine := gin.New()
	engine.HandleMethodNotAllowed = true
	engine.Use(
		middleware.RequestID(),
		middleware.RequestLogger(logger),
		middleware.Recovery(logger),
	)

	engine.NoRoute(func(c *gin.Context) {
		markVersionedPath(c)
		response.NotFound(c, "route not found")
	})
	engine.NoMethod(func(c *gin.Context) {
		markVersionedPath(c)
		response.MethodNotAllowed(c)
	})

	registerProbe(engine)
	registerV1(engine, deps)
	registerLegacy(engine, deps)
	return engine
}

func registerProbe(engine *gin.Engine) {
	engine.GET("/healthz", health)
}

func registerV1(engine *gin.Engine, deps Dependencies) {
	v1 := engine.Group("/api/v1")
	v1.Use(response.Versioned())
	v1.GET("/health", health)

	authRoutes := v1.Group("/auth")
	authRoutes.POST("/login", deps.Users.Login)
	authRoutes.POST("/refresh", deps.Users.Refresh)
	authRoutes.POST("/register", deps.Users.Register)

	protected := v1.Group("")
	protected.Use(middleware.Auth(deps.Tokens, deps.Sessions))
	protected.GET("/auth/me", deps.Users.Me)
	protected.POST("/auth/logout", deps.Users.Logout)

	protected.GET("/providers", deps.ProviderCatalog.List)
	protected.POST("/providers/credential-tests", deps.ProviderCredentials.TestCredential)

	protected.GET("/model-configs", deps.ModelConfigs.List)
	protected.POST("/model-configs", deps.ModelConfigs.Create)
	protected.GET("/model-configs/:id", deps.ModelConfigs.Get)
	protected.PATCH("/model-configs/:id", deps.ModelConfigs.Update)
	protected.DELETE("/model-configs/:id", deps.ModelConfigs.Delete)

	protected.GET("/agents", deps.AgentConfigs.ListAgents)
	protected.GET("/agents/:id", deps.AgentConfigs.GetAgent)
	protected.GET("/agent-configs", deps.AgentConfigs.ListConfigs)
	protected.POST("/agent-configs", deps.AgentConfigs.CreateConfig)
	protected.GET("/agent-configs/:id", deps.AgentConfigs.GetConfig)
	protected.PATCH("/agent-configs/:id", deps.AgentConfigs.UpdateConfig)
	protected.DELETE("/agent-configs/:id", deps.AgentConfigs.DeleteConfig)

	protected.POST("/text-generations", deps.TextGeneration.StreamText)

	presentations := protected.Group("/presentations")
	presentations.GET("/conversations", deps.Presentations.ListConversations)
	presentations.GET("/conversations/:id", deps.Presentations.GetConversation)
	presentations.POST("/jobs", deps.Presentations.CreateJob)
	presentations.GET("/jobs/:job_id", deps.Presentations.GetJob)
	presentations.GET("/jobs/:job_id/events", deps.Presentations.SubscribeJob)
	presentations.POST("/jobs/:job_id/actions", deps.Presentations.JobAction)
	presentations.POST("/jobs/:job_id/cancel", deps.Presentations.CancelJob)
	presentations.GET("/metrics", deps.Presentations.ProductMetrics)
	presentations.GET("/artifacts/:request_id", deps.Presentations.DownloadArtifact)
	presentations.GET("/artifacts/:request_id/*asset_path", deps.Presentations.DownloadArtifact)
}

func registerLegacy(engine *gin.Engine, deps Dependencies) {
	engine.POST("/login", deps.Users.Login)
	engine.POST("/refresh", deps.Users.Refresh)
	engine.POST("/register", deps.Users.Register)

	protected := engine.Group("")
	protected.Use(middleware.Auth(deps.Tokens, deps.Sessions))
	protected.GET("/me", deps.Users.Me)
	protected.POST("/logout", deps.Users.Logout)
	protected.GET("/providers", deps.ProviderCatalog.List)
	protected.GET("/model-configs", deps.ModelConfigs.List)
	protected.POST("/model-configs", deps.ModelConfigs.Create)
	protected.GET("/model-configs/:id", deps.ModelConfigs.Get)
	protected.PUT("/model-configs/:id", deps.ModelConfigs.Update)
	protected.DELETE("/model-configs/:id", deps.ModelConfigs.Delete)
	protected.GET("/agents", deps.AgentConfigs.ListAgents)
	protected.GET("/agents/:id", deps.AgentConfigs.GetAgent)
	protected.GET("/agent-configs", deps.AgentConfigs.ListConfigs)
	protected.POST("/agent-configs", deps.AgentConfigs.CreateConfig)
	protected.GET("/agent-configs/:id", deps.AgentConfigs.GetConfig)
	protected.PUT("/agent-configs/:id", deps.AgentConfigs.UpdateConfig)
	protected.DELETE("/agent-configs/:id", deps.AgentConfigs.DeleteConfig)
	protected.POST("/provider-credentials/test", deps.ProviderCredentials.TestCredential)
	protected.POST("/agent/stream-text", deps.TextGeneration.StreamText)
	protected.GET("/ppt-agent/conversations", deps.Presentations.ListConversations)
	protected.GET("/ppt-agent/conversations/:id", deps.Presentations.GetConversation)
	protected.POST("/ppt-agent/jobs", deps.Presentations.CreateJob)
	protected.GET("/ppt-agent/jobs/:job_id", deps.Presentations.GetJob)
	protected.GET("/ppt-agent/jobs/:job_id/events", deps.Presentations.SubscribeJob)
	protected.POST("/ppt-agent/jobs/:job_id/actions", deps.Presentations.JobAction)
	protected.POST("/ppt-agent/jobs/:job_id/cancel", deps.Presentations.CancelJob)
	protected.GET("/ppt-agent/metrics", deps.Presentations.ProductMetrics)
	protected.POST("/ppt-agent/generate", deprecated("/api/v1/presentations/jobs"), deps.Presentations.GenerateDeck)
	protected.POST("/ppt-agent/resume", deprecated("/api/v1/presentations/jobs/:job_id/actions"), deps.Presentations.ResumeDeck)
	protected.GET("/ppt-agent/artifacts/:request_id", deps.Presentations.DownloadArtifact)
	protected.GET("/ppt-agent/artifacts/:request_id/*asset_path", deps.Presentations.DownloadArtifact)
}

func health(c *gin.Context) {
	response.OK(c, gin.H{"status": "ok"})
}

func deprecated(successor string) gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Deprecation", "true")
		c.Header("Link", "<"+successor+">; rel=\"successor-version\"")
		c.Next()
	}
}

func markVersionedPath(c *gin.Context) {
	if strings.HasPrefix(c.Request.URL.Path, "/api/v1/") || c.Request.URL.Path == "/api/v1" {
		response.MarkVersioned(c)
	}
}
