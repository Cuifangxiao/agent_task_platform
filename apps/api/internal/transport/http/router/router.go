// Package router owns the public HTTP surface. Business handlers stay grouped
// by module while this package defines authentication and endpoint layout.
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

	// Browser WebSockets authenticate through Sec-WebSocket-Protocol, so this
	// route must translate that credential before entering the regular Auth
	// middleware instead of inheriting the protected group directly.
	v1.GET(
		"/ppt/ws",
		middleware.WebSocketSubprotocolAuth(),
		middleware.Auth(deps.Tokens, deps.Sessions),
		deps.Presentations.WebSocket,
	)

	ppt := protected.Group("/ppt")
	ppt.POST("/tasks", deps.Presentations.CreateTask)
	ppt.GET("/tasks", deps.Presentations.ListTasks)
	ppt.GET("/tasks/:task_id", deps.Presentations.GetTask)
	ppt.POST("/tasks/:task_id/cancel", deps.Presentations.CancelTask)
	ppt.GET("/tasks/:task_id/file", deps.Presentations.DownloadFile)
	ppt.GET("/tasks/:task_id/artifacts/*asset_path", deps.Presentations.DownloadArtifact)
}

func health(c *gin.Context) {
	response.OK(c, gin.H{"status": "ok"})
}

func markVersionedPath(c *gin.Context) {
	if strings.HasPrefix(c.Request.URL.Path, "/api/v1/") || c.Request.URL.Path == "/api/v1" {
		response.MarkVersioned(c)
	}
}
