package router

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHealthContracts(t *testing.T) {
	engine := New(Dependencies{}, slog.New(slog.NewTextHandler(io.Discard, nil)))

	t.Run("legacy payload remains compatible", func(t *testing.T) {
		response := performRequest(engine, http.MethodGet, "/healthz")
		if response.Code != http.StatusOK {
			t.Fatalf("status = %d, want %d", response.Code, http.StatusOK)
		}
		var body map[string]any
		decodeJSON(t, response, &body)
		if body["status"] != "ok" {
			t.Fatalf("unexpected body: %#v", body)
		}
		if _, wrapped := body["data"]; wrapped {
			t.Fatalf("legacy response must not use the v1 envelope: %#v", body)
		}
	})

	t.Run("v1 payload includes data and request metadata", func(t *testing.T) {
		response := performRequest(engine, http.MethodGet, "/api/v1/health")
		if response.Code != http.StatusOK {
			t.Fatalf("status = %d, want %d", response.Code, http.StatusOK)
		}
		var body struct {
			Data map[string]any `json:"data"`
			Meta struct {
				RequestID string `json:"request_id"`
			} `json:"meta"`
		}
		decodeJSON(t, response, &body)
		if body.Data["status"] != "ok" || body.Meta.RequestID == "" {
			t.Fatalf("unexpected body: %#v", body)
		}
		if response.Header().Get("X-Request-ID") != body.Meta.RequestID {
			t.Fatalf("request ID header and body differ")
		}
	})
}

func TestVersionedRoutingErrorsUseErrorEnvelope(t *testing.T) {
	engine := New(Dependencies{}, slog.New(slog.NewTextHandler(io.Discard, nil)))

	tests := []struct {
		method string
		path   string
		status int
		code   string
	}{
		{method: http.MethodGet, path: "/api/v1/missing", status: http.StatusNotFound, code: "not_found"},
		{method: http.MethodPost, path: "/api/v1/health", status: http.StatusMethodNotAllowed, code: "method_not_allowed"},
	}

	for _, test := range tests {
		t.Run(test.code, func(t *testing.T) {
			response := performRequest(engine, test.method, test.path)
			if response.Code != test.status {
				t.Fatalf("status = %d, want %d", response.Code, test.status)
			}
			var body struct {
				Error struct {
					Code      string `json:"code"`
					RequestID string `json:"request_id"`
				} `json:"error"`
			}
			decodeJSON(t, response, &body)
			if body.Error.Code != test.code || body.Error.RequestID == "" {
				t.Fatalf("unexpected body: %#v", body)
			}
		})
	}
}

func TestCanonicalRouteTable(t *testing.T) {
	engine := New(Dependencies{}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	registered := make(map[string]struct{})
	for _, route := range engine.Routes() {
		registered[route.Method+" "+route.Path] = struct{}{}
	}

	expected := []string{
		"GET /api/v1/health",
		"POST /api/v1/auth/login",
		"POST /api/v1/auth/refresh",
		"POST /api/v1/auth/register",
		"GET /api/v1/auth/me",
		"POST /api/v1/auth/logout",
		"GET /api/v1/providers",
		"POST /api/v1/providers/credential-tests",
		"GET /api/v1/model-configs",
		"POST /api/v1/model-configs",
		"GET /api/v1/model-configs/:id",
		"PATCH /api/v1/model-configs/:id",
		"DELETE /api/v1/model-configs/:id",
		"GET /api/v1/agents",
		"GET /api/v1/agents/:id",
		"GET /api/v1/agent-configs",
		"POST /api/v1/agent-configs",
		"GET /api/v1/agent-configs/:id",
		"PATCH /api/v1/agent-configs/:id",
		"DELETE /api/v1/agent-configs/:id",
		"POST /api/v1/text-generations",
		"GET /api/v1/ppt/ws",
		"POST /api/v1/ppt/tasks",
		"GET /api/v1/ppt/tasks/:task_id",
		"DELETE /api/v1/ppt/tasks/:task_id",
		"POST /api/v1/ppt/tasks/:task_id/cancel",
		"GET /api/v1/ppt/tasks/:task_id/file",
		"GET /api/v1/ppt/tasks/:task_id/artifacts/*asset_path",
	}

	for _, route := range expected {
		if _, ok := registered[route]; !ok {
			t.Errorf("canonical route is not registered: %s", route)
		}
	}
	for route := range registered {
		if route == "GET /api/v1/ppt/tasks/:task_id/events" {
			t.Errorf("PPT SSE route is still registered: %s", route)
		}
		if strings.Contains(route, "/presentations/") || strings.Contains(route, "/ppt-agent/") {
			t.Errorf("obsolete PPT route is still registered: %s", route)
		}
	}
}

func TestRequestIDValidation(t *testing.T) {
	engine := New(Dependencies{}, slog.New(slog.NewTextHandler(io.Discard, nil)))

	validRequest := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	validRequest.Header.Set("X-Request-ID", "client.trace-1")
	validResponse := httptest.NewRecorder()
	engine.ServeHTTP(validResponse, validRequest)
	if got := validResponse.Header().Get("X-Request-ID"); got != "client.trace-1" {
		t.Fatalf("valid request ID = %q", got)
	}

	invalidRequest := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	invalidRequest.Header.Set("X-Request-ID", "invalid/request/id")
	invalidResponse := httptest.NewRecorder()
	engine.ServeHTTP(invalidResponse, invalidRequest)
	if got := invalidResponse.Header().Get("X-Request-ID"); got == "" || got == "invalid/request/id" {
		t.Fatalf("invalid request ID was not replaced: %q", got)
	}
}

func performRequest(engine http.Handler, method string, path string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, nil)
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)
	return response
}

func decodeJSON(t *testing.T, response *httptest.ResponseRecorder, target any) {
	t.Helper()
	if err := json.Unmarshal(response.Body.Bytes(), target); err != nil {
		t.Fatalf("decode response: %v; body=%s", err, response.Body.String())
	}
}
