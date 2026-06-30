package providercredential

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

func (h *Handler) TestCredential(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req TestCredentialRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.TestCredential(c.Request.Context(), UserContext{
		UserID:    claims.UserID,
		Username:  claims.Username,
		SessionID: claims.SessionID,
		Device:    claims.Device,
	}, req)
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
