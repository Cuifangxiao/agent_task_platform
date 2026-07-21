package modelconfig

import (
	"agent-task-platform/apps/api/internal/shared/actor"
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"agent-task-platform/apps/api/internal/modules/provider/catalog"
	"agent-task-platform/apps/api/internal/modules/provider/credential"
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
		ID:              1,
		UserID:          config.UserID,
		Name:            config.Name,
		Provider:        config.Provider,
		ModelName:       config.ModelName,
		ModelType:       config.ModelType,
		BaseURL:         config.BaseURL,
		APIKey:          config.APIKey,
		ProviderOptions: config.ProviderOptions,
		Temperature:     config.Temperature,
		MaxTokens:       config.MaxTokens,
		IsDefault:       config.IsDefault,
		Status:          config.Status,
		CreatedAt:       now,
		UpdatedAt:       now,
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
		ID:              config.ID,
		UserID:          config.UserID,
		Name:            config.Name,
		Provider:        config.Provider,
		ModelName:       config.ModelName,
		ModelType:       config.ModelType,
		BaseURL:         config.BaseURL,
		APIKey:          config.APIKey,
		ProviderOptions: config.ProviderOptions,
		Temperature:     config.Temperature,
		MaxTokens:       config.MaxTokens,
		IsDefault:       config.IsDefault,
		Status:          config.Status,
		CreatedAt:       now,
		UpdatedAt:       now,
	}, nil
}

func (s *stubRepository) Delete(_ context.Context, _ int64, _ int64) error {
	return nil
}

type stubCredentialService struct {
	calls    int
	lastUser actor.User
	lastReq  credential.TestCredentialRequest
	resp     *credential.TestCredentialResponse
	err      error
}

func (s *stubCredentialService) TestCredential(_ context.Context, user actor.User, req credential.TestCredentialRequest) (*credential.TestCredentialResponse, error) {
	s.calls++
	s.lastUser = user
	s.lastReq = req
	if s.err != nil {
		return nil, s.err
	}
	return s.resp, nil
}

type stubProviderCatalogService struct {
	resp *catalog.ListResponse
	err  error
}

func (s *stubProviderCatalogService) List(_ context.Context) (*catalog.ListResponse, error) {
	if s.err != nil {
		return nil, s.err
	}
	return s.resp, nil
}

func testCatalog() *catalog.ListResponse {
	return testCatalogWithModelType("chat")
}

func testCatalogWithModelType(modelType string) *catalog.ListResponse {
	return &catalog.ListResponse{
		Providers: []catalog.ProviderResponse{
			{
				Name:           "openai",
				DefaultBaseURL: "https://api.openai.com/v1",
				Models: []catalog.ModelResponse{
					{
						ID:        "gpt-4o-mini",
						ModelName: "gpt-4o-mini",
						Type:      modelType,
					},
					{
						ID:        "mini-alias",
						ModelName: "gpt-4o-mini",
						Type:      modelType,
					},
				},
			},
		},
	}
}

func testDeepSeekCatalog() *catalog.ListResponse {
	minTemperature := 0.0
	maxTemperature := 2.0
	minTokens := 1.0
	maxTokens := 384000.0
	return &catalog.ListResponse{
		Providers: []catalog.ProviderResponse{
			{
				Name: "deepseek",
				Models: []catalog.ModelResponse{
					{
						ID:        "deepseek-v4-flash",
						ModelName: "deepseek-v4-flash",
						Type:      "text",
						Parameters: []catalog.ModelParameterResponse{
							{Name: "thinking", Type: "enum", DefaultValue: "enabled", Options: []string{"enabled", "disabled"}},
							{Name: "reasoning_effort", Type: "enum", DefaultValue: "high", Options: []string{"high", "max"}},
							{Name: "temperature", Type: "number", DefaultValue: "1", Min: &minTemperature, Max: &maxTemperature},
							{Name: "max_tokens", Type: "integer", DefaultValue: "4096", Min: &minTokens, Max: &maxTokens},
						},
					},
				},
			},
		},
	}
}

func testQwenCatalog() *catalog.ListResponse {
	minThinkingBudget := 1.0
	return &catalog.ListResponse{
		Providers: []catalog.ProviderResponse{
			{
				Name: "qwen",
				Models: []catalog.ModelResponse{
					{
						ID:        "qwen3.6-flash",
						ModelName: "qwen3.6-flash",
						Type:      "multimodal",
						Parameters: []catalog.ModelParameterResponse{
							{Name: "enable_thinking", Type: "boolean", DefaultValue: "true"},
							{Name: "thinking_budget", Type: "integer", Min: &minThinkingBudget},
						},
					},
				},
			},
		},
	}
}

func testUser() actor.User {
	return actor.User{
		UserID:    1,
		Username:  "alice",
		SessionID: "session-1",
		Device:    "web",
	}
}

func TestCreateTestsCredentialBeforeSaving(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{
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
	if resp.ModelType != string(ModelTypeMultimodal) {
		t.Fatalf("expected legacy catalog model type %q, got %q", ModelTypeMultimodal, resp.ModelType)
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
		resp: &credential.TestCredentialResponse{
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

func TestCreateDropsReasoningEffortWhenThinkingIsDisabled(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{Status: "ok"},
	}
	providerCatalog := &stubProviderCatalogService{resp: testDeepSeekCatalog()}
	service := NewService(repo, credential, providerCatalog)

	_, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:            "deepseek",
		Provider:        "deepseek",
		ModelName:       "deepseek-v4-flash",
		APIKey:          "sk-test",
		ProviderOptions: json.RawMessage(`{"thinking":"disabled","reasoning_effort":"max"}`),
	})
	if err != nil {
		t.Fatalf("Create returned error: %v", err)
	}
	if repo.lastCreated.ProviderOptions != `{"thinking":"disabled"}` {
		t.Fatalf("unexpected provider options: %s", repo.lastCreated.ProviderOptions)
	}
}

func TestCreateDropsQwenThinkingBudgetWhenThinkingIsDisabled(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{Status: "ok"},
	}
	providerCatalog := &stubProviderCatalogService{resp: testQwenCatalog()}
	service := NewService(repo, credential, providerCatalog)

	_, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:            "qwen",
		Provider:        "qwen",
		ModelName:       "qwen3.6-flash",
		APIKey:          "sk-test",
		ProviderOptions: json.RawMessage(`{"enable_thinking":false,"thinking_budget":16384}`),
	})
	if err != nil {
		t.Fatalf("Create returned error: %v", err)
	}
	if repo.lastCreated.ProviderOptions != `{"enable_thinking":false}` {
		t.Fatalf("unexpected provider options: %s", repo.lastCreated.ProviderOptions)
	}
}

func TestCreateRejectsInvalidQwenThinkingBudget(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{Status: "ok"},
	}
	providerCatalog := &stubProviderCatalogService{resp: testQwenCatalog()}
	service := NewService(repo, credential, providerCatalog)

	_, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:            "qwen",
		Provider:        "qwen",
		ModelName:       "qwen3.6-flash",
		APIKey:          "sk-test",
		ProviderOptions: json.RawMessage(`{"enable_thinking":true,"thinking_budget":0}`),
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("expected ErrInvalidRequest, got %v", err)
	}
	if repo.createCalls != 0 || credential.calls != 0 {
		t.Fatalf("invalid thinking budget must fail before credential test and save")
	}
}

func TestCreateRejectsInvalidCatalogProviderOption(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{Status: "ok"},
	}
	providerCatalog := &stubProviderCatalogService{resp: testDeepSeekCatalog()}
	service := NewService(repo, credential, providerCatalog)

	_, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:            "deepseek",
		Provider:        "deepseek",
		ModelName:       "deepseek-v4-flash",
		APIKey:          "sk-test",
		ProviderOptions: json.RawMessage(`{"thinking":"enabled","reasoning_effort":"extreme"}`),
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("expected ErrInvalidRequest, got %v", err)
	}
	if repo.createCalls != 0 || credential.calls != 0 {
		t.Fatalf("invalid options must fail before credential test and save")
	}
}

func TestCreateUsesCatalogModelTypeWhenRequestOmitsIt(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{Status: "ok"},
	}
	catalog := &stubProviderCatalogService{resp: testCatalogWithModelType("multimodal")}
	service := NewService(repo, credential, catalog)

	resp, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:      "vision",
		Provider:  "openai",
		ModelName: "gpt-4o-mini",
		APIKey:    "sk-test",
	})
	if err != nil {
		t.Fatalf("Create returned error: %v", err)
	}
	if resp.ModelType != string(ModelTypeMultimodal) {
		t.Fatalf("model type = %q, want %q", resp.ModelType, ModelTypeMultimodal)
	}
	if repo.lastCreated.ModelType != ModelTypeMultimodal {
		t.Fatalf("stored model type = %q, want %q", repo.lastCreated.ModelType, ModelTypeMultimodal)
	}
}

func TestCreateRejectsModelTypeThatConflictsWithCatalog(t *testing.T) {
	repo := &stubRepository{}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{Status: "ok"},
	}
	catalog := &stubProviderCatalogService{resp: testCatalog()}
	service := NewService(repo, credential, catalog)

	_, err := service.Create(context.Background(), testUser(), CreateRequest{
		Name:      "wrong-type",
		Provider:  "openai",
		ModelName: "gpt-4o-mini",
		ModelType: "image",
		APIKey:    "sk-test",
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("expected ErrInvalidRequest, got %v", err)
	}
	if repo.createCalls != 0 {
		t.Fatalf("expected create not to be called, got %d", repo.createCalls)
	}
	if credential.calls != 0 {
		t.Fatalf("expected credential test not to run, got %d", credential.calls)
	}
}

func TestUpdateNameOnlySyncsCatalogModelTypeWithoutCredentialRetest(t *testing.T) {
	repo := &stubRepository{
		getResp: &ModelConfig{
			ID:          1,
			UserID:      1,
			Name:        "main",
			Provider:    "openai",
			ModelName:   "gpt-4o-mini",
			ModelType:   ModelTypeText,
			BaseURL:     "https://api.openai.com/v1",
			APIKey:      "sk-test",
			Temperature: 1,
			MaxTokens:   4096,
			IsDefault:   false,
			Status:      StatusActive,
		},
	}
	credential := &stubCredentialService{
		resp: &credential.TestCredentialResponse{
			Status:  "ok",
			Message: "probe ok",
		},
	}
	catalog := &stubProviderCatalogService{resp: testCatalogWithModelType("multimodal")}
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
	if resp.ModelType != string(ModelTypeMultimodal) {
		t.Fatalf("expected model type to sync to %q, got %q", ModelTypeMultimodal, resp.ModelType)
	}
	if credential.calls != 0 {
		t.Fatalf("expected credential test not to run, got %d", credential.calls)
	}
	if repo.updateCalls != 1 {
		t.Fatalf("expected update to be called once, got %d", repo.updateCalls)
	}
}

func TestNormalizeInputModelType(t *testing.T) {
	tests := []struct {
		name      string
		modelType string
		want      ModelType
		wantErr   bool
	}{
		{name: "defaults to text", want: ModelTypeText},
		{name: "normalizes text", modelType: " TEXT ", want: ModelTypeText},
		{name: "accepts multimodal", modelType: "multimodal", want: ModelTypeMultimodal},
		{name: "accepts image", modelType: "image", want: ModelTypeImage},
		{name: "rejects unsupported type", modelType: "audio", wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			config, err := normalizeInput(modelConfigInput{
				Name:      "main",
				Provider:  "openai",
				ModelName: "gpt-4o-mini",
				ModelType: tt.modelType,
				APIKey:    "sk-test",
			})
			if tt.wantErr {
				if !errors.Is(err, ErrInvalidRequest) {
					t.Fatalf("expected ErrInvalidRequest, got %v", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("normalizeInput returned error: %v", err)
			}
			if config.ModelType != tt.want {
				t.Fatalf("model type = %q, want %q", config.ModelType, tt.want)
			}
		})
	}
}

func TestModelTypeFromCatalog(t *testing.T) {
	tests := []struct {
		name        string
		catalogType string
		modelName   string
		want        ModelType
		wantOK      bool
	}{
		{name: "legacy text chat", catalogType: "chat", modelName: "deepseek-chat", want: ModelTypeText, wantOK: true},
		{name: "legacy qwen vl", catalogType: "chat", modelName: "qwen3-vl-plus", want: ModelTypeMultimodal, wantOK: true},
		{name: "legacy openai vision", catalogType: "chat", modelName: "gpt-4o-mini", want: ModelTypeMultimodal, wantOK: true},
		{name: "native image", catalogType: "image", modelName: "gpt-image-1", want: ModelTypeImage, wantOK: true},
		{name: "unknown type", catalogType: "embedding", modelName: "text-embedding-3-small"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := modelTypeFromCatalog(tt.catalogType, tt.modelName)
			if ok != tt.wantOK || got != tt.want {
				t.Fatalf("modelTypeFromCatalog(%q, %q) = (%q, %v), want (%q, %v)", tt.catalogType, tt.modelName, got, ok, tt.want, tt.wantOK)
			}
		})
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
		resp: &credential.TestCredentialResponse{
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
