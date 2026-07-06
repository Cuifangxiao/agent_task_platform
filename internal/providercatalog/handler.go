package providercatalog

import (
	"agent-task-platform/pkg/response"

	"github.com/gin-gonic/gin"
)

type Handler struct {
	service *Service
}

func NewHandler(service *Service) *Handler {
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
