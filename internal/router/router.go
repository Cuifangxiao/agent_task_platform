package router

import (
	"agent-task-platform/internal/auth"
	"agent-task-platform/internal/middleware"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
	"agent-task-platform/internal/user"
	"agent-task-platform/pkg/response"

	"github.com/gin-gonic/gin"
)

func Register(
	r *gin.Engine,
	userHandler *user.Handler,
	modelConfigHandler *modelconfig.Handler,
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
	authGroup.GET("/model-configs", modelConfigHandler.List)
	authGroup.POST("/model-configs", modelConfigHandler.Create)
	authGroup.GET("/model-configs/:id", modelConfigHandler.Get)
	authGroup.PUT("/model-configs/:id", modelConfigHandler.Update)
	authGroup.DELETE("/model-configs/:id", modelConfigHandler.Delete)
	authGroup.POST("/provider-credentials/test", providerCredentialHandler.TestCredential)
}
