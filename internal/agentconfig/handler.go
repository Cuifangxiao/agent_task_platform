package agentconfig

import (
	"errors"
	"strconv"
	"strings"

	"agent-task-platform/internal/httpctx"
	"agent-task-platform/internal/providercredential"
	"agent-task-platform/pkg/response"

	"github.com/gin-gonic/gin"
)

type Handler struct {
	service *Service
}

func NewHandler(service *Service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) ListAgents(c *gin.Context) {
	resp, err := h.service.ListAgents(c.Request.Context())
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) GetAgent(c *gin.Context) {
	var req PathRequest
	if err := c.ShouldBindUri(&req); err != nil {
		response.BadRequest(c, "invalid path", err)
		return
	}

	resp, err := h.service.GetAgent(c.Request.Context(), req.ID)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) ListConfigs(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	agentID, err := parseOptionalInt64(c.Query("agent_id"))
	if err != nil {
		response.BadRequest(c, "invalid agent_id", err)
		return
	}

	resp, err := h.service.ListConfigs(c.Request.Context(), claims.UserID, agentID)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) CreateConfig(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req CreateConfigRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.CreateConfig(c.Request.Context(), providercredential.UserContext{
		UserID:    claims.UserID,
		Username:  claims.Username,
		SessionID: claims.SessionID,
		Device:    claims.Device,
	}, req)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.Created(c, resp)
}

func (h *Handler) GetConfig(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req PathRequest
	if err := c.ShouldBindUri(&req); err != nil {
		response.BadRequest(c, "invalid path", err)
		return
	}

	resp, err := h.service.GetConfig(c.Request.Context(), claims.UserID, req.ID)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) UpdateConfig(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var pathReq PathRequest
	if err := c.ShouldBindUri(&pathReq); err != nil {
		response.BadRequest(c, "invalid path", err)
		return
	}

	var req UpdateConfigRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.UpdateConfig(c.Request.Context(), providercredential.UserContext{
		UserID:    claims.UserID,
		Username:  claims.Username,
		SessionID: claims.SessionID,
		Device:    claims.Device,
	}, pathReq.ID, req)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) DeleteConfig(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req PathRequest
	if err := c.ShouldBindUri(&req); err != nil {
		response.BadRequest(c, "invalid path", err)
		return
	}

	if err := h.service.DeleteConfig(c.Request.Context(), claims.UserID, req.ID); err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, DeleteResponse{Message: "agent config deleted"})
}

func (h *Handler) respondError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrInvalidRequest):
		response.BadRequest(c, err.Error(), nil)
	case errors.Is(err, ErrNameAlreadyExists):
		response.Conflict(c, err.Error())
	case errors.Is(err, ErrNotFound):
		response.NotFound(c, err.Error())
	default:
		response.InternalServerError(c)
	}
}

func parseOptionalInt64(raw string) (int64, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return 0, nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value <= 0 {
		return 0, ErrInvalidRequest
	}
	return value, nil
}
