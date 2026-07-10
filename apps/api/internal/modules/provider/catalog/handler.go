package catalog

import (
	"context"

	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

type Handler struct {
	service service
}

type service interface {
	List(context.Context) (*ListResponse, error)
}

func NewHandler(service service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) List(c *gin.Context) {
	resp, err := h.service.List(c.Request.Context())
	if err != nil {
		response.InternalServerError(c)
		return
	}

	response.OK(c, resp)
}
