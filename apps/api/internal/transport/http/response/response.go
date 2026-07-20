// Package response defines the JSON contract shared by all versioned HTTP
// endpoints. Legacy routes keep their historical payload shape while v1
// routes use a stable data/error envelope.
package response

import (
	"errors"
	"net/http"
	"strings"

	"agent-task-platform/apps/api/internal/transport/http/httpctx"

	"github.com/gin-gonic/gin"
	"github.com/go-playground/validator/v10"
)

const envelopeContextKey = "http.response.envelope"

type Meta struct {
	RequestID string `json:"request_id"`
}

type SuccessEnvelope struct {
	Data any  `json:"data"`
	Meta Meta `json:"meta"`
}

type ErrorEnvelope struct {
	Error ErrorBody `json:"error"`
}

type ErrorBody struct {
	Code      string        `json:"code"`
	Message   string        `json:"message"`
	Details   []ErrorDetail `json:"details,omitempty"`
	RequestID string        `json:"request_id"`
}

type ErrorDetail struct {
	Field  string `json:"field,omitempty"`
	Reason string `json:"reason"`
}

// Versioned enables the v1 response envelope for a route group.
func Versioned() gin.HandlerFunc {
	return func(c *gin.Context) {
		MarkVersioned(c)
		c.Next()
	}
}

func MarkVersioned(c *gin.Context) {
	c.Set(envelopeContextKey, true)
}

func OK(c *gin.Context, payload any) {
	success(c, http.StatusOK, payload)
}

func Created(c *gin.Context, payload any) {
	success(c, http.StatusCreated, payload)
}

func Accepted(c *gin.Context, payload any) {
	success(c, http.StatusAccepted, payload)
}

func BadRequest(c *gin.Context, message string, err error) {
	if !usesEnvelope(c) {
		body := gin.H{"message": message}
		if err != nil {
			body["error"] = err.Error()
		}
		c.JSON(http.StatusBadRequest, body)
		return
	}

	fail(c, http.StatusBadRequest, "invalid_request", message, validationDetails(err))
}

func Unauthorized(c *gin.Context, message string) {
	fail(c, http.StatusUnauthorized, "unauthorized", message, nil)
}

func Conflict(c *gin.Context, message string) {
	fail(c, http.StatusConflict, "conflict", message, nil)
}

func ConflictCode(c *gin.Context, code, message string) {
	code = strings.TrimSpace(code)
	if code == "" {
		code = "conflict"
	}
	fail(c, http.StatusConflict, code, message, nil)
}

func PayloadTooLarge(c *gin.Context, message string) {
	fail(c, http.StatusRequestEntityTooLarge, "payload_too_large", message, nil)
}

func UnsupportedMediaType(c *gin.Context, message string) {
	fail(c, http.StatusUnsupportedMediaType, "unsupported_media_type", message, nil)
}

func NotFound(c *gin.Context, message string) {
	fail(c, http.StatusNotFound, "not_found", message, nil)
}

func MethodNotAllowed(c *gin.Context) {
	fail(c, http.StatusMethodNotAllowed, "method_not_allowed", "method not allowed", nil)
}

func Internal(c *gin.Context, message string) {
	fail(c, http.StatusInternalServerError, "internal_error", message, nil)
}

func InternalServerError(c *gin.Context) {
	fail(c, http.StatusInternalServerError, "internal_error", "internal server error", nil)
}

func Error(c *gin.Context, statusCode int, message string) {
	fail(c, statusCode, httpErrorCode(statusCode), message, nil)
}

func success(c *gin.Context, statusCode int, payload any) {
	if !usesEnvelope(c) {
		c.JSON(statusCode, payload)
		return
	}

	c.JSON(statusCode, SuccessEnvelope{
		Data: payload,
		Meta: Meta{RequestID: httpctx.RequestID(c)},
	})
}

func fail(c *gin.Context, statusCode int, code string, message string, details []ErrorDetail) {
	if !usesEnvelope(c) {
		c.JSON(statusCode, gin.H{"message": message})
		return
	}

	c.JSON(statusCode, ErrorEnvelope{Error: ErrorBody{
		Code:      code,
		Message:   message,
		Details:   details,
		RequestID: httpctx.RequestID(c),
	}})
}

func usesEnvelope(c *gin.Context) bool {
	value, ok := c.Get(envelopeContextKey)
	enabled, valid := value.(bool)
	return ok && valid && enabled
}

func validationDetails(err error) []ErrorDetail {
	if err == nil {
		return nil
	}

	var validationErrors validator.ValidationErrors
	if errors.As(err, &validationErrors) {
		details := make([]ErrorDetail, 0, len(validationErrors))
		for _, item := range validationErrors {
			details = append(details, ErrorDetail{
				Field:  strings.ToLower(item.Field()),
				Reason: validationReason(item),
			})
		}
		return details
	}

	return []ErrorDetail{{Reason: "request payload is malformed"}}
}

func validationReason(fieldError validator.FieldError) string {
	if parameter := fieldError.Param(); parameter != "" {
		return fieldError.Tag() + "=" + parameter
	}
	return fieldError.Tag()
}

func httpErrorCode(statusCode int) string {
	switch statusCode {
	case http.StatusBadRequest:
		return "invalid_request"
	case http.StatusUnauthorized:
		return "unauthorized"
	case http.StatusForbidden:
		return "forbidden"
	case http.StatusNotFound:
		return "not_found"
	case http.StatusConflict:
		return "conflict"
	case http.StatusRequestEntityTooLarge:
		return "payload_too_large"
	case http.StatusUnsupportedMediaType:
		return "unsupported_media_type"
	case http.StatusMethodNotAllowed:
		return "method_not_allowed"
	default:
		if statusCode >= http.StatusInternalServerError {
			return "internal_error"
		}
		return "request_failed"
	}
}
