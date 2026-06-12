package modelconfig

import (
	"errors"
	"net/http"

	"agent-task-platform/internal/auth"
	"github.com/gin-gonic/gin"
)

type Handler struct {
	service *Service
}

func NewHandler(service *Service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) Create(c *gin.Context) {
	claimsValue, _ := c.Get(auth.ContextKeyClaims)
	claims, _ := claimsValue.(*auth.Claims)
	if claims == nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"message": "missing auth claims",
		})
		return
	}

	var req CreateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"message": "invalid request",
			"error":   err.Error(),
		})
		return
	}

	resp, err := h.service.Create(c.Request.Context(), claims.UserID, req)
	if err != nil {
		switch {
		case errors.Is(err, ErrInvalidRequest):
			c.JSON(http.StatusBadRequest, gin.H{
				"message": err.Error(),
			})
		case errors.Is(err, ErrNameAlreadyExists):
			c.JSON(http.StatusConflict, gin.H{
				"message": err.Error(),
			})
		default:
			c.JSON(http.StatusInternalServerError, gin.H{
				"message": "internal server error",
			})
		}
		return
	}

	c.JSON(http.StatusCreated, resp)
}
