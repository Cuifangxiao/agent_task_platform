package router

import (
	"net/http"

	"agent-task-platform/internal/auth"
	"agent-task-platform/internal/middleware"
	"agent-task-platform/internal/user"

	"github.com/gin-gonic/gin"
)

func Register(r *gin.Engine, userHandler *user.Handler, jwtManager *auth.Manager) {
	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"status": "ok",
		})
	})

	r.POST("/login", userHandler.Login)
	r.POST("/register", userHandler.Register)

	authGroup := r.Group("")
	authGroup.Use(middleware.Auth(jwtManager))
	authGroup.GET("/me", userHandler.Me)
}
