package modelconfig

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

func (h *Handler) Create(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req CreateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.Create(c.Request.Context(), claims.UserID, req)
	if err != nil {
		switch {
		case errors.Is(err, ErrInvalidRequest):
			response.BadRequest(c, err.Error(), nil)
		case errors.Is(err, ErrNameAlreadyExists):
			response.Conflict(c, err.Error())
		default:
			response.InternalServerError(c)
		}
		return
	}

	response.Created(c, resp)
}
