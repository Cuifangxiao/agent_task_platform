package response

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

func OK(c *gin.Context, payload any) {
	c.JSON(http.StatusOK, payload)
}

func Created(c *gin.Context, payload any) {
	c.JSON(http.StatusCreated, payload)
}

func BadRequest(c *gin.Context, message string, err error) {
	body := gin.H{
		"message": message,
	}
	if err != nil {
		body["error"] = err.Error()
	}

	c.JSON(http.StatusBadRequest, body)
}

func Unauthorized(c *gin.Context, message string) {
	Error(c, http.StatusUnauthorized, message)
}

func Conflict(c *gin.Context, message string) {
	Error(c, http.StatusConflict, message)
}

func NotFound(c *gin.Context, message string) {
	Error(c, http.StatusNotFound, message)
}

func Internal(c *gin.Context, message string) {
	Error(c, http.StatusInternalServerError, message)
}

func InternalServerError(c *gin.Context) {
	Error(c, http.StatusInternalServerError, "internal server error")
}

func Error(c *gin.Context, statusCode int, message string) {
	c.JSON(statusCode, gin.H{
		"message": message,
	})
}
