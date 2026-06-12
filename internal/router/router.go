package router

import (
	"net/http"

	"agent-task-platform/internal/auth"
	"agent-task-platform/internal/middleware"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/user"

	"github.com/gin-gonic/gin"
)

func Register(
	r *gin.Engine,
	userHandler *user.Handler,
	modelConfigHandler *modelconfig.Handler,
	jwtManager *auth.Manager,
	sessionStore auth.SessionStore,
) {
	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
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
	authGroup.POST("/model-configs", modelConfigHandler.Create)
}
