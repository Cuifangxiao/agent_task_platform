package modelconfig

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"agent-task-platform/internal/providercatalog"
	"agent-task-platform/internal/providercredential"
)

type stubRepository struct {
	createCalls int
	updateCalls int
	lastCreated ModelConfig
	lastUpdated ModelConfig
	getResp     *ModelConfig
}

func (s *stubRepository) Create(_ context.Context, config ModelConfig) (*ModelConfig, error) {
	s.createCalls++
	s.lastCreated = config
	now := time.Now()
	return &ModelConfig{
		ID:          1,
		UserID:      config.UserID,
		Name:        config.Name,
		Provider:    config.Provider,
		ModelName:   config.ModelName,
		BaseURL:     config.BaseURL,
		APIKey:      config.APIKey,
		Temperature: config.Temperature,
		MaxTokens:   config.MaxTokens,
		IsDefault:   config.IsDefault,
		Status:      config.Status,
		CreatedAt:   now,
		UpdatedAt:   now,
	}, nil
}

func (s *stubRepository) ListByUserID(_ context.Context, _ int64) ([]*ModelConfig, error) {
	return nil, nil
}

func (s *stubRepository) GetByID(_ context.Context, _ int64, _ int64) (*ModelConfig, error) {
	if s.getResp == nil {
		return nil, ErrNotFound
	}
	return s.getResp, nil
}

func (s *stubRepository) Update(_ context.Context, config ModelConfig) (*ModelConfig, error) {
	s.updateCalls++
	s.lastUpdated = config
	now := time.Now()
	return &ModelConfig{
		ID:          config.ID,
		UserID:      config.UserID,
		Name:        config.Name,
		Provider:    config.Provider,
		ModelName:   config.ModelName,
		BaseURL:     config.BaseURL,
		APIKey:      config.APIKey,
		Temperature: config.Temperature,
		MaxTokens:   config.MaxTokens,
		IsDefault:   config.IsDefault,
		Status:      config.Status,
		CreatedAt:   now,
		UpdatedAt:   now,
	}, nil
}

func (s *stubRepository) Delete(_ context.Context, _ int64, _ int64) error {
	return nil
}

type stubCredentialService struct {
	calls    int
	lastUser providercredential.UserContext
	lastReq  providercredential.TestCredentialRequest
	resp     *providercredential.TestCredentialResponse
	err      error
}

func (s *stubCredentialService) TestCredential(_ context.Context, user providercredential.UserContext, req providercredential.TestCredentialRequest) (*providercredential.TestCredentialResponse, error) {
	s.calls++
	s.lastUser = user
	s.lastReq = req
	if s.err != nil {
		return nil, s.err
	}
	return s.resp, nil
}

type stubProviderCatalogService struct {
	resp *providercatalog.ListResponse
	err  error
}

func (s *stubProviderCatalogService) List(_ context.Context) (*providercatalog.ListResponse, error) {
	if s.err != nil {
		return nil, s.err
	}
	return s.resp, nil
}

func testCatalog() *providercatalog.ListResponse {
	return &providercatalog.ListResponse{
		Providers: []providercatalog.ProviderResponse{
			{
				Name:           "openai",
				DefaultBaseURL: "https://api.openai.com/v1",
				Models: []providercatalog.ModelResponse{
					{
						ID:        "gpt-4o-mini",
						ModelName: "gpt-4o-mini",
					},
					{
						ID:        "mini-alias",
						ModelName: "gpt-4o-mini",
					},
				},
			},
		},
	}
}

func testUser() providercredential.UserContext {
	return providercredential.UserContext{
		UserID:    1,
		Username:  "alice",
		SessionID: "session-1",
		Device:    "web",
	}
}

func TestCreateTestsCredentialBeforeSaving(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &providercredential.TestCredentialResponse{
			Status:  "ok",
			Message: "probe ok",
		},
	}
	catalog := &stubProviderCatalogService{resp: testCatalog()}
	service := NewService(repo, credential, catalog)

	resp, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:            "main",
		Provider:        "openai",
		ModelName:       "mini-alias",
		APIKey:          "sk-test",
		BaseURL:         "https://api.openai.com/v1",
		ProviderOptions: json.RawMessage(`{"reasoning_effort":"medium"}`),
	})
	if err != nil {
		t.Fatalf("Create returned error: %v", err)
	}

	if resp.ModelName != "gpt-4o-mini" {
		t.Fatalf("expected normalized model name, got %q", resp.ModelName)
	}
	if repo.createCalls != 1 {
		t.Fatalf("expected create to be called once, got %d", repo.createCalls)
	}
	if credential.calls != 1 {
		t.Fatalf("expected credential test to be called once, got %d", credential.calls)
	}
	if repo.lastCreated.ProviderOptions != `{"reasoning_effort":"medium"}` {
		t.Fatalf("unexpected provider options: %s", repo.lastCreated.ProviderOptions)
	}
	if credential.lastReq.Provider != "openai" {
		t.Fatalf("unexpected credential provider: %q", credential.lastReq.Provider)
	}
}

func TestCreateDoesNotSaveWhenCredentialTestFails(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &providercredential.TestCredentialResponse{
			Status:  "failed",
			Message: "bad api key",
		},
	}
	catalog := &stubProviderCatalogService{resp: testCatalog()}
	service := NewService(repo, credential, catalog)

	_, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:      "main",
		Provider:  "openai",
		ModelName: "gpt-4o-mini",
		APIKey:    "sk-test",
		BaseURL:   "https://api.openai.com/v1",
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("expected ErrInvalidRequest, got %v", err)
	}
	if repo.createCalls != 0 {
		t.Fatalf("expected create not to be called, got %d", repo.createCalls)
	}
}

func TestUpdateNameOnlySkipsCredentialRetest(t *testing.T) {
	repo := &stubRepository{
		getResp: &ModelConfig{
			ID:          1,
			UserID:      1,
			Name:        "main",
			Provider:    "openai",
			ModelName:   "gpt-4o-mini",
			BaseURL:     "https://api.openai.com/v1",
			APIKey:      "sk-test",
			Temperature: 1,
			MaxTokens:   4096,
			IsDefault:   false,
			Status:      StatusActive,
		},
	}
	credential := &stubCredentialService{
		resp: &providercredential.TestCredentialResponse{
			Status:  "ok",
			Message: "probe ok",
		},
	}
	catalog := &stubProviderCatalogService{resp: testCatalog()}
	service := NewService(repo, credential, catalog)

	newName := "renamed"
	resp, err := service.Update(context.Background(), testUser(), 1, UpdateRequest{
		Name: &newName,
	})
	if err != nil {
		t.Fatalf("Update returned error: %v", err)
	}

	if resp.Name != "renamed" {
		t.Fatalf("expected updated name, got %q", resp.Name)
	}
	if credential.calls != 0 {
		t.Fatalf("expected credential test not to run, got %d", credential.calls)
	}
	if repo.updateCalls != 1 {
		t.Fatalf("expected update to be called once, got %d", repo.updateCalls)
	}
}

func TestUpdateBaseURLRetestsCredential(t *testing.T) {
	repo := &stubRepository{
		getResp: &ModelConfig{
			ID:          1,
			UserID:      1,
			Name:        "main",
			Provider:    "openai",
			ModelName:   "gpt-4o-mini",
			BaseURL:     "https://api.openai.com/v1",
			APIKey:      "sk-test",
			Temperature: 1,
			MaxTokens:   4096,
			IsDefault:   false,
			Status:      StatusActive,
		},
	}
	credential := &stubCredentialService{
		resp: &providercredential.TestCredentialResponse{
			Status:  "ok",
			Message: "probe ok",
		},
	}
	catalog := &stubProviderCatalogService{resp: testCatalog()}
	service := NewService(repo, credential, catalog)

	newBaseURL := "https://proxy.example.com/v1"
	_, err := service.Update(context.Background(), testUser(), 1, UpdateRequest{
		BaseURL: &newBaseURL,
	})
	if err != nil {
		t.Fatalf("Update returned error: %v", err)
	}

	if credential.calls != 1 {
		t.Fatalf("expected credential test to run once, got %d", credential.calls)
	}
	if credential.lastReq.BaseURL != newBaseURL {
		t.Fatalf("expected credential test base_url %q, got %q", newBaseURL, credential.lastReq.BaseURL)
	}
}
