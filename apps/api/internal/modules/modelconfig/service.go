package modelconfig

import (
	"agent-task-platform/apps/api/internal/shared/actor"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"agent-task-platform/apps/api/internal/modules/provider/catalog"
	"agent-task-platform/apps/api/internal/modules/provider/credential"
	"agent-task-platform/apps/api/internal/shared/validation"
)

var ErrInvalidRequest = errors.New("invalid model config request")
var ErrNotFound = errors.New("model config not found")

type CredentialService interface {
	TestCredential(ctx context.Context, user actor.User, req credential.TestCredentialRequest) (*credential.TestCredentialResponse, error)
}

type ProviderCatalogService interface {
	List(ctx context.Context) (*catalog.ListResponse, error)
}

type Service struct {
	repo                   Repository
	credentialService      CredentialService
	providerCatalogService ProviderCatalogService
}

func NewService(repo Repository, credentialService CredentialService, providerCatalogService ProviderCatalogService) *Service {
	return &Service{
		repo:                   repo,
		credentialService:      credentialService,
		providerCatalogService: providerCatalogService,
	}
}

func (s *Service) Create(ctx context.Context, user actor.User, req CreateRequest) (*Response, error) {
	if err := validateUserID(user.UserID); err != nil {
		return nil, err
	}

	config, err := normalizeInput(modelConfigInput{
		Name:            req.Name,
		Provider:        req.Provider,
		ModelName:       req.ModelName,
		ModelType:       req.ModelType,
		BaseURL:         req.BaseURL,
		APIKey:          req.APIKey,
		ProviderOptions: req.ProviderOptions,
		Temperature:     req.Temperature,
		MaxTokens:       req.MaxTokens,
		IsDefault:       req.IsDefault,
		Status:          req.Status,
	})
	if err != nil {
		return nil, err
	}
	if err := s.validateProviderModel(ctx, &config, strings.TrimSpace(req.ModelType) != ""); err != nil {
		return nil, err
	}
	if err := s.runCredentialTest(ctx, user, config); err != nil {
		return nil, err
	}

	config.UserID = user.UserID
	created, err := s.repo.Create(ctx, config)
	if err != nil {
		return nil, err
	}

	return toResponse(created), nil
}

func (s *Service) List(ctx context.Context, userID int64) ([]Response, error) {
	if err := validateUserID(userID); err != nil {
		return nil, err
	}

	configs, err := s.repo.ListByUserID(ctx, userID)
	if err != nil {
		return nil, err
	}

	return toResponses(configs), nil
}

func (s *Service) Get(ctx context.Context, userID int64, id int64) (*Response, error) {
	if err := validateUserID(userID); err != nil {
		return nil, err
	}
	if err := validateModelConfigID(id); err != nil {
		return nil, err
	}

	config, err := s.repo.GetByID(ctx, userID, id)
	if err != nil {
		return nil, err
	}

	return toResponse(config), nil
}

func (s *Service) Update(ctx context.Context, user actor.User, id int64, req UpdateRequest) (*Response, error) {
	if err := validateUserID(user.UserID); err != nil {
		return nil, err
	}
	if err := validateModelConfigID(id); err != nil {
		return nil, err
	}

	existing, err := s.repo.GetByID(ctx, user.UserID, id)
	if err != nil {
		return nil, err
	}

	input := modelConfigInput{
		Name:            existing.Name,
		Provider:        existing.Provider,
		ModelName:       existing.ModelName,
		ModelType:       string(existing.ModelType),
		BaseURL:         existing.BaseURL,
		APIKey:          existing.APIKey,
		ProviderOptions: json.RawMessage(existing.ProviderOptions),
		Temperature:     &existing.Temperature,
		MaxTokens:       &existing.MaxTokens,
		IsDefault:       existing.IsDefault,
		Status:          string(existing.Status),
	}

	if req.Name != nil {
		input.Name = *req.Name
	}
	if req.Provider != nil {
		input.Provider = *req.Provider
	}
	if req.ModelName != nil {
		input.ModelName = *req.ModelName
	}
	if req.ModelType != nil {
		input.ModelType = *req.ModelType
	}
	if req.BaseURL != nil {
		input.BaseURL = *req.BaseURL
	}
	if req.APIKey != nil {
		input.APIKey = *req.APIKey
	}
	if req.ProviderOptions != nil {
		input.ProviderOptions = *req.ProviderOptions
	}
	if req.Temperature != nil {
		input.Temperature = req.Temperature
	}
	if req.MaxTokens != nil {
		input.MaxTokens = req.MaxTokens
	}
	if req.IsDefault != nil {
		input.IsDefault = *req.IsDefault
	}
	if req.Status != nil {
		input.Status = *req.Status
	}

	config, err := normalizeInput(input)
	if err != nil {
		return nil, err
	}
	modelTypeExplicit := req.ModelType != nil && strings.TrimSpace(*req.ModelType) != ""
	if err := s.validateProviderModel(ctx, &config, modelTypeExplicit); err != nil {
		return nil, err
	}
	if shouldRetestCredential(existing, config) {
		if err := s.runCredentialTest(ctx, user, config); err != nil {
			return nil, err
		}
	}

	config.ID = id
	config.UserID = user.UserID
	updated, err := s.repo.Update(ctx, config)
	if err != nil {
		return nil, err
	}

	return toResponse(updated), nil
}

func (s *Service) Delete(ctx context.Context, userID int64, id int64) error {
	if err := validateUserID(userID); err != nil {
		return err
	}
	if err := validateModelConfigID(id); err != nil {
		return err
	}

	return s.repo.Delete(ctx, userID, id)
}

func (s *Service) validateProviderModel(ctx context.Context, config *ModelConfig, modelTypeExplicit bool) error {
	if s.providerCatalogService == nil {
		return fmt.Errorf("provider catalog service is not configured")
	}

	resp, err := s.providerCatalogService.List(ctx)
	if err != nil {
		return fmt.Errorf("list providers failed: %w", err)
	}

	for _, provider := range resp.Providers {
		if !strings.EqualFold(provider.Name, config.Provider) {
			continue
		}

		for _, model := range provider.Models {
			if strings.EqualFold(model.ModelName, config.ModelName) || strings.EqualFold(model.ID, config.ModelName) {
				if catalogType, ok := modelTypeFromCatalog(model.Type, model.ModelName); ok {
					if modelTypeExplicit && config.ModelType != catalogType {
						return fmt.Errorf(
							"%w: model_type '%s' does not match catalog type '%s' for model '%s'",
							ErrInvalidRequest,
							config.ModelType,
							catalogType,
							model.ModelName,
						)
					}
					config.ModelType = catalogType
				}
				if err := reconcileProviderOptionsWithCatalog(config, model); err != nil {
					return err
				}
				config.ModelName = model.ModelName
				return nil
			}
		}

		return fmt.Errorf("%w: model_name '%s' is not supported for provider '%s'", ErrInvalidRequest, config.ModelName, config.Provider)
	}

	return fmt.Errorf("%w: provider '%s' is not supported", ErrInvalidRequest, config.Provider)
}

func reconcileProviderOptionsWithCatalog(config *ModelConfig, model catalog.ModelResponse) error {
	options := decodeProviderOptions(config.ProviderOptions)
	parameters := make(map[string]catalog.ModelParameterResponse, len(model.Parameters))
	for _, parameter := range model.Parameters {
		parameters[parameter.Name] = parameter
	}

	if enabled, hasThinkingToggle := catalogThinkingEnabled(options, parameters); hasThinkingToggle && !enabled {
		// Provider-specific thinking controls have no meaning when thinking is
		// disabled. Removing them also repairs stale records created before the UI
		// exposed this dependency.
		delete(options, "reasoning_effort")
		delete(options, "thinking_budget")
	}

	for name, value := range options {
		parameter, known := parameters[name]
		if !known {
			// provider_options remains extensible for SDK-specific settings that
			// are not intended to be rendered by the catalog-driven form.
			continue
		}
		if err := validateCatalogParameterValue(parameter, value); err != nil {
			return fmt.Errorf("%w: provider_options.%s %v", ErrInvalidRequest, name, err)
		}
	}

	if parameter, ok := parameters["temperature"]; ok {
		if err := validateCatalogParameterValue(parameter, config.Temperature); err != nil {
			return fmt.Errorf("%w: temperature %v", ErrInvalidRequest, err)
		}
	}
	if parameter, ok := parameters["max_tokens"]; ok {
		if err := validateCatalogParameterValue(parameter, config.MaxTokens); err != nil {
			return fmt.Errorf("%w: max_tokens %v", ErrInvalidRequest, err)
		}
	}

	normalized, err := json.Marshal(options)
	if err != nil {
		return fmt.Errorf("%w: normalize provider_options failed", ErrInvalidRequest)
	}
	config.ProviderOptions = string(normalized)
	return nil
}

func catalogThinkingEnabled(
	options map[string]any,
	parameters map[string]catalog.ModelParameterResponse,
) (bool, bool) {
	for _, name := range []string{"thinking", "enable_thinking"} {
		parameter, ok := parameters[name]
		if !ok {
			continue
		}
		value, exists := options[name]
		if !exists {
			value = parameter.DefaultValue
		}
		if boolean, ok := value.(bool); ok {
			return boolean, true
		}
		normalized := strings.ToLower(strings.TrimSpace(fmt.Sprint(value)))
		return normalized == "enabled" || normalized == "true", true
	}
	return false, false
}

func validateCatalogParameterValue(parameter catalog.ModelParameterResponse, value any) error {
	switch strings.ToLower(strings.TrimSpace(parameter.Type)) {
	case "enum":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("must be one of %s", strings.Join(parameter.Options, ", "))
		}
		for _, option := range parameter.Options {
			if text == option {
				return nil
			}
		}
		return fmt.Errorf("must be one of %s", strings.Join(parameter.Options, ", "))
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("must be a boolean")
		}
		return nil
	case "integer":
		number, ok := catalogNumber(value)
		if !ok || math.Trunc(number) != number {
			return fmt.Errorf("must be an integer")
		}
		return validateCatalogNumberRange(parameter, number)
	case "number", "float":
		number, ok := catalogNumber(value)
		if !ok {
			return fmt.Errorf("must be a number")
		}
		return validateCatalogNumberRange(parameter, number)
	case "string":
		if _, ok := value.(string); !ok {
			return fmt.Errorf("must be a string")
		}
	}
	return nil
}

func catalogNumber(value any) (float64, bool) {
	switch number := value.(type) {
	case float64:
		return number, true
	case float32:
		return float64(number), true
	case int:
		return float64(number), true
	case int32:
		return float64(number), true
	case int64:
		return float64(number), true
	default:
		return 0, false
	}
}

func validateCatalogNumberRange(parameter catalog.ModelParameterResponse, number float64) error {
	if parameter.Min != nil && number < *parameter.Min {
		return fmt.Errorf("must be at least %v", *parameter.Min)
	}
	if parameter.Max != nil && number > *parameter.Max {
		return fmt.Errorf("must be at most %v", *parameter.Max)
	}
	return nil
}

func modelTypeFromCatalog(raw string, modelName string) (ModelType, bool) {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "chat":
		if legacyChatModelSupportsImageInput(modelName) {
			return ModelTypeMultimodal, true
		}
		return ModelTypeText, true
	case string(ModelTypeText):
		return ModelTypeText, true
	case string(ModelTypeMultimodal):
		return ModelTypeMultimodal, true
	case string(ModelTypeImage):
		return ModelTypeImage, true
	default:
		return "", false
	}
}

func legacyChatModelSupportsImageInput(modelName string) bool {
	name := strings.ToLower(strings.TrimSpace(modelName))
	if strings.Contains(name, "qwen") && (strings.Contains(name, "-vl") || strings.Contains(name, "vl-")) {
		return true
	}
	for _, marker := range []string{"gpt-4o", "gpt-4.1", "gpt-5", "vision", "gemini", "claude-3", "claude-4"} {
		if strings.Contains(name, marker) {
			return true
		}
	}
	return false
}

func (s *Service) runCredentialTest(ctx context.Context, user actor.User, config ModelConfig) error {
	if s.credentialService == nil {
		return fmt.Errorf("credential service is not configured")
	}

	resp, err := s.credentialService.TestCredential(ctx, user, credential.TestCredentialRequest{
		Provider: config.Provider,
		APIKey:   config.APIKey,
		BaseURL:  config.BaseURL,
	})
	if err != nil {
		return fmt.Errorf("test provider credential failed: %w", err)
	}

	if !strings.EqualFold(strings.TrimSpace(resp.Status), "ok") {
		message := strings.TrimSpace(resp.Message)
		if message == "" {
			message = "credential test failed"
		}
		return fmt.Errorf("%w: %s", ErrInvalidRequest, message)
	}

	return nil
}

func shouldRetestCredential(existing *ModelConfig, next ModelConfig) bool {
	if existing == nil {
		return true
	}

	return existing.Provider != next.Provider ||
		existing.APIKey != next.APIKey ||
		existing.BaseURL != next.BaseURL
}

func toResponse(config *ModelConfig) *Response {
	return &Response{
		ID:              config.ID,
		Name:            config.Name,
		Provider:        config.Provider,
		ModelName:       config.ModelName,
		ModelType:       string(config.ModelType),
		BaseURL:         config.BaseURL,
		APIKeyMasked:    maskAPIKey(config.APIKey),
		HasAPIKey:       config.APIKey != "",
		ProviderOptions: decodeProviderOptions(config.ProviderOptions),
		Temperature:     config.Temperature,
		MaxTokens:       config.MaxTokens,
		IsDefault:       config.IsDefault,
		Status:          string(config.Status),
		CreatedAt:       config.CreatedAt,
		UpdatedAt:       config.UpdatedAt,
	}
}

func toResponses(configs []*ModelConfig) []Response {
	responses := make([]Response, 0, len(configs))
	for _, config := range configs {
		if config == nil {
			continue
		}
		responses = append(responses, *toResponse(config))
	}

	return responses
}

type modelConfigInput struct {
	Name            string
	Provider        string
	ModelName       string
	ModelType       string
	BaseURL         string
	APIKey          string
	ProviderOptions json.RawMessage
	Temperature     *float64
	MaxTokens       *int
	IsDefault       bool
	Status          string
}

func normalizeInput(input modelConfigInput) (ModelConfig, error) {
	name := strings.TrimSpace(input.Name)
	provider := strings.ToLower(strings.TrimSpace(input.Provider))
	modelName := strings.TrimSpace(input.ModelName)
	modelType := ModelType(strings.ToLower(strings.TrimSpace(input.ModelType)))
	if modelType == "" {
		modelType = ModelTypeText
	}
	baseURL := strings.TrimSpace(input.BaseURL)
	apiKey := strings.TrimSpace(input.APIKey)
	providerOptions, err := normalizeProviderOptions(input.ProviderOptions)
	if err != nil {
		return ModelConfig{}, err
	}

	if name == "" {
		return ModelConfig{}, fmt.Errorf("%w: name is required", ErrInvalidRequest)
	}
	if provider == "" {
		return ModelConfig{}, fmt.Errorf("%w: provider is required", ErrInvalidRequest)
	}
	if modelName == "" {
		return ModelConfig{}, fmt.Errorf("%w: model_name is required", ErrInvalidRequest)
	}
	if modelType != ModelTypeText && modelType != ModelTypeMultimodal && modelType != ModelTypeImage {
		return ModelConfig{}, fmt.Errorf("%w: model_type must be text, multimodal, or image", ErrInvalidRequest)
	}
	if apiKey == "" {
		return ModelConfig{}, fmt.Errorf("%w: api_key is required", ErrInvalidRequest)
	}

	if baseURL != "" {
		if err := validation.HTTPURL(baseURL); err != nil {
			return ModelConfig{}, fmt.Errorf("%w: base_url %v", ErrInvalidRequest, err)
		}
	}

	temperature := 1.0
	if input.Temperature != nil {
		temperature = *input.Temperature
	}
	if temperature < 0 || temperature > 2 {
		return ModelConfig{}, fmt.Errorf("%w: temperature must be between 0 and 2", ErrInvalidRequest)
	}

	maxTokens := 4096
	if input.MaxTokens != nil {
		maxTokens = *input.MaxTokens
	}
	if maxTokens <= 0 {
		return ModelConfig{}, fmt.Errorf("%w: max_tokens must be greater than 0", ErrInvalidRequest)
	}

	status := StatusActive
	if input.Status != "" {
		status = Status(strings.ToLower(strings.TrimSpace(input.Status)))
	}
	if status != StatusActive && status != StatusInactive {
		return ModelConfig{}, fmt.Errorf("%w: status must be active or inactive", ErrInvalidRequest)
	}

	return ModelConfig{
		Name:            name,
		Provider:        provider,
		ModelName:       modelName,
		ModelType:       modelType,
		BaseURL:         baseURL,
		APIKey:          apiKey,
		ProviderOptions: providerOptions,
		Temperature:     temperature,
		MaxTokens:       maxTokens,
		IsDefault:       input.IsDefault,
		Status:          status,
	}, nil
}

func validateUserID(userID int64) error {
	if userID <= 0 {
		return fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}

	return nil
}

func validateModelConfigID(id int64) error {
	if id <= 0 {
		return fmt.Errorf("%w: invalid model config id", ErrInvalidRequest)
	}

	return nil
}

func maskAPIKey(apiKey string) string {
	apiKey = strings.TrimSpace(apiKey)
	if apiKey == "" {
		return ""
	}
	if len(apiKey) <= 4 {
		return strings.Repeat("*", len(apiKey))
	}

	return strings.Repeat("*", len(apiKey)-4) + apiKey[len(apiKey)-4:]
}

func normalizeProviderOptions(raw json.RawMessage) (string, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		return "{}", nil
	}

	var providerOptions map[string]any
	if err := json.Unmarshal(trimmed, &providerOptions); err != nil {
		return "", fmt.Errorf("%w: provider_options must be a valid JSON object", ErrInvalidRequest)
	}

	normalized, err := json.Marshal(providerOptions)
	if err != nil {
		return "", fmt.Errorf("%w: normalize provider_options failed", ErrInvalidRequest)
	}

	return string(normalized), nil
}

func decodeProviderOptions(raw string) map[string]any {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return map[string]any{}
	}

	var providerOptions map[string]any
	if err := json.Unmarshal([]byte(trimmed), &providerOptions); err != nil {
		return map[string]any{}
	}
	if providerOptions == nil {
		return map[string]any{}
	}

	return providerOptions
}
