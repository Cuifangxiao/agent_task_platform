package user

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

func (h *Handler) Login(c *gin.Context) {
	var req LoginRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"message": "invalid request",
			"error":   err.Error(),
		})
		return
	}

	resp, err := h.service.Login(c.Request.Context(), req)
	if err != nil {
		if errors.Is(err, ErrInvalidCredentials) {
			c.JSON(http.StatusUnauthorized, gin.H{
				"message": err.Error(),
			})
			return
		}

		c.JSON(http.StatusInternalServerError, gin.H{
			"message": "internal server error",
		})
		return
	}

	c.JSON(http.StatusOK, resp)
}

func (h *Handler) Refresh(c *gin.Context) {
	var req RefreshRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"message": "invalid request",
			"error":   err.Error(),
		})
		return
	}

	resp, err := h.service.Refresh(c.Request.Context(), req)
	if err != nil {
		if errors.Is(err, ErrInvalidRefreshToken) || errors.Is(err, ErrSessionNotActive) {
			c.JSON(http.StatusUnauthorized, gin.H{
				"message": err.Error(),
			})
			return
		}

		c.JSON(http.StatusInternalServerError, gin.H{
			"message": "internal server error",
		})
		return
	}

	c.JSON(http.StatusOK, resp)
}

func (h *Handler) Register(c *gin.Context) {
	var req RegisterRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"message": "invalid request",
			"error":   err.Error(),
		})
		return
	}

	resp, err := h.service.Register(c.Request.Context(), req)
	if err != nil {
		if errors.Is(err, ErrUsernameAlreadyExists) {
			c.JSON(http.StatusConflict, gin.H{
				"message": err.Error(),
			})
			return
		}

		c.JSON(http.StatusInternalServerError, gin.H{
			"message": "internal server error",
		})
		return
	}

	c.JSON(http.StatusCreated, resp)
}

func (h *Handler) Me(c *gin.Context) {
	claimsValue, _ := c.Get(auth.ContextKeyClaims)
	claims, _ := claimsValue.(*auth.Claims)
	if claims == nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"message": "missing auth claims",
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"user_id":    claims.UserID,
		"username":   claims.Username,
		"device":     claims.Device,
		"session_id": claims.SessionID,
	})
}

func (h *Handler) Logout(c *gin.Context) {
	claimsValue, _ := c.Get(auth.ContextKeyClaims)
	claims, _ := claimsValue.(*auth.Claims)
	if claims == nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"message": "missing auth claims",
		})
		return
	}

	if err := h.service.Logout(c.Request.Context(), claims); err != nil {
		if errors.Is(err, ErrSessionNotActive) {
			c.JSON(http.StatusUnauthorized, gin.H{
				"message": err.Error(),
			})
			return
		}

		c.JSON(http.StatusInternalServerError, gin.H{
			"message": "internal server error",
		})
		return
	}

	c.JSON(http.StatusOK, LogoutResponse{
		Message: "logout ok",
	})
}
