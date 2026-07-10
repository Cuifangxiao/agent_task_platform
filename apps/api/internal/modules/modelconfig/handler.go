package modelconfig

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
	Create(context.Context, actor.User, CreateRequest) (*Response, error)
	List(context.Context, int64) ([]Response, error)
	Get(context.Context, int64, int64) (*Response, error)
	Update(context.Context, actor.User, int64, UpdateRequest) (*Response, error)
	Delete(context.Context, int64, int64) error
}

func NewHandler(service service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) Create(c *gin.Context) {
	currentUser, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req CreateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.Create(c.Request.Context(), currentUser, req)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.Created(c, resp)
}

func (h *Handler) List(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	resp, err := h.service.List(c.Request.Context(), claims.UserID)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) Get(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var pathReq PathRequest
	if err := c.ShouldBindUri(&pathReq); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.Get(c.Request.Context(), claims.UserID, pathReq.ID)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) Update(c *gin.Context) {
	currentUser, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var pathReq PathRequest
	if err := c.ShouldBindUri(&pathReq); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	var req UpdateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	resp, err := h.service.Update(c.Request.Context(), currentUser, pathReq.ID, req)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) Delete(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var pathReq PathRequest
	if err := c.ShouldBindUri(&pathReq); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	if err := h.service.Delete(c.Request.Context(), claims.UserID, pathReq.ID); err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, DeleteResponse{
		Message: "model config deleted",
	})
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
