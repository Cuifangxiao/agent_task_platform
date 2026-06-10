package router

import (
	"net/http"

	"agent-task-platform/internal/user"
	"github.com/gin-gonic/gin"
)

func Register(r *gin.Engine, userHandler *user.Handler) {
	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"status": "ok",
		})
	})

	r.POST("/login", userHandler.Login)
}
