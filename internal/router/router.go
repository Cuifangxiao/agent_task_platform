package router

import (
	"agent-task-platform/internal/agent"
	"agent-task-platform/internal/agentconfig"
	"agent-task-platform/internal/auth"
	"agent-task-platform/internal/middleware"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/pptagent"
	"agent-task-platform/internal/providercatalog"
	"agent-task-platform/internal/providercredential"
	"agent-task-platform/internal/user"
	"agent-task-platform/pkg/response"

	"github.com/gin-gonic/gin"
)

func Register(
	r *gin.Engine,
	userHandler *user.Handler,
	agentHandler *agent.Handler,
	agentConfigHandler *agentconfig.Handler,
	pptAgentHandler *pptagent.Handler,
	modelConfigHandler *modelconfig.Handler,
	providerCatalogHandler *providercatalog.Handler,
	providerCredentialHandler *providercredential.Handler,
	jwtManager *auth.Manager,
	sessionStore auth.SessionStore,
) {
	r.GET("/healthz", func(c *gin.Context) {
		response.OK(c, gin.H{
			"status": "ok",
		})
	})

	r.POST("/login", userHandler.Login)
	r.POST("/refresh", userHandler.Refresh)
	r.POST("/register", userHandler.Register)

	authGroup := r.Group("")
	authGroup.Use(middleware.Auth(jwtManager, sessionStore))
	authGroup.GET("/me", userHandler.Me)
	authGroup.POST("/logout", userHandler.Logout)
	authGroup.GET("/providers", providerCatalogHandler.List)
	authGroup.GET("/model-configs", modelConfigHandler.List)
	authGroup.POST("/model-configs", modelConfigHandler.Create)
	authGroup.GET("/model-configs/:id", modelConfigHandler.Get)
	authGroup.PUT("/model-configs/:id", modelConfigHandler.Update)
	authGroup.DELETE("/model-configs/:id", modelConfigHandler.Delete)
	authGroup.GET("/agents", agentConfigHandler.ListAgents)
	authGroup.GET("/agents/:id", agentConfigHandler.GetAgent)
	authGroup.GET("/agent-configs", agentConfigHandler.ListConfigs)
	authGroup.POST("/agent-configs", agentConfigHandler.CreateConfig)
	authGroup.GET("/agent-configs/:id", agentConfigHandler.GetConfig)
	authGroup.PUT("/agent-configs/:id", agentConfigHandler.UpdateConfig)
	authGroup.DELETE("/agent-configs/:id", agentConfigHandler.DeleteConfig)
	authGroup.POST("/provider-credentials/test", providerCredentialHandler.TestCredential)
	authGroup.POST("/agent/stream-text", agentHandler.StreamText)
	authGroup.POST("/ppt-agent/generate", pptAgentHandler.GenerateDeck)
	authGroup.GET("/ppt-agent/artifacts/:request_id", pptAgentHandler.DownloadArtifact)
}
