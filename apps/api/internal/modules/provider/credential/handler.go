package credential

import (
	"context"
	"errors"

	"agent-task-platform/apps/api/internal/shared/actor"
	"agent-task-platform/apps/api/internal/transport/http/httpctx"
	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

type Handler struct {
	service service
}

type service interface {
	TestCredential(context.Context, actor.User, TestCredentialRequest) (*TestCredentialResponse, error)
}

func NewHandler(service service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) TestCredential(c *gin.Context) {
	currentUser, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req TestCredentialRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.TestCredential(c.Request.Context(), currentUser, req)
	if err != nil {
		if errors.Is(err, ErrInvalidRequest) {
			response.BadRequest(c, err.Error(), nil)
			return
		}

		response.InternalServerError(c)
		return
	}

	response.OK(c, resp)
}
