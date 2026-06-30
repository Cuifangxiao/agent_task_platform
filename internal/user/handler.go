package user

import (
	"errors"

	"agent-task-platform/internal/httpctx"
	"agent-task-platform/pkg/response"
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
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.Login(c.Request.Context(), req)
	if err != nil {
		if errors.Is(err, ErrInvalidCredentials) {
			response.Unauthorized(c, err.Error())
			return
		}

		response.InternalServerError(c)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) Refresh(c *gin.Context) {
	var req RefreshRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.Refresh(c.Request.Context(), req)
	if err != nil {
		if errors.Is(err, ErrInvalidRefreshToken) || errors.Is(err, ErrSessionNotActive) {
			response.Unauthorized(c, err.Error())
			return
		}

		response.InternalServerError(c)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) Register(c *gin.Context) {
	var req RegisterRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.Register(c.Request.Context(), req)
	if err != nil {
		if errors.Is(err, ErrUsernameAlreadyExists) {
			response.Conflict(c, err.Error())
			return
		}

		response.InternalServerError(c)
		return
	}

	response.Created(c, resp)
}

func (h *Handler) Me(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	response.OK(c, gin.H{
		"user_id":    claims.UserID,
		"username":   claims.Username,
		"device":     claims.Device,
		"session_id": claims.SessionID,
	})
}

func (h *Handler) Logout(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	if err := h.service.Logout(c.Request.Context(), claims); err != nil {
		if errors.Is(err, ErrSessionNotActive) {
			response.Unauthorized(c, err.Error())
			return
		}

		response.InternalServerError(c)
		return
	}

	response.OK(c, LogoutResponse{
		Message: "logout ok",
	})
}
