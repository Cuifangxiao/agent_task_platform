package textgeneration

import (
	"agent-task-platform/apps/api/internal/shared/actor"
	"context"
	"errors"
	"testing"

	"agent-task-platform/apps/api/internal/modules/modelconfig"
)

func TestStreamTextUsesDefaultActiveModelConfig(t *testing.T) {
	ctx := context.Background()
	expectedStream := &fakeTextStream{}
	runtimeClient := &fakeRuntimeClient{returnStream: expectedStream}
	repo := &fakeModelConfigRepo{
		configs: []*modelconfig.ModelConfig{
			{
				ID:       1,
				UserID:   10,
				Provider: "openai",
				Status:   modelconfig.StatusInactive,
			},
			{
				ID:              2,
				UserID:          10,
				Provider:        "deepseek",
				ModelName:       "deepseek-chat",
				APIKey:          "sk-test",
				BaseURL:         "https://api.deepseek.com",
				ProviderOptions: `{"top_p":0.8}`,
				Temperature:     0.7,
				MaxTokens:       1024,
				IsDefault:       true,
				Status:          modelconfig.StatusActive,
			},
		},
	}
	service := NewService(repo, runtimeClient)

	stream, err := service.StreamText(ctx, actor.User{
		UserID:   10,
		Username: "alice",
	}, StreamTextRequest{
		Message:      " hello ",
		SystemPrompt: " be brief ",
	})
	if err != nil {
		t.Fatalf("StreamText returned error: %v", err)
	}
	if stream != expectedStream {
		t.Fatalf("unexpected stream: %#v", stream)
	}

	got := runtimeClient.lastReq
	if got.Provider != "deepseek" || got.Model != "deepseek-chat" {
		t.Fatalf("unexpected provider/model: %#v", got)
	}
	if got.Prompt != "hello" || got.SystemPrompt != "be brief" {
		t.Fatalf("unexpected prompt fields: %#v", got)
	}
	if got.APIKey != "sk-test" || got.BaseURL != "https://api.deepseek.com" {
		t.Fatalf("unexpected credential fields: %#v", got)
	}
	if got.ProviderOptionsJSON != `{"top_p":0.8}` {
		t.Fatalf("unexpected provider options: %s", got.ProviderOptionsJSON)
	}
	if got.Temperature != 0.7 || got.MaxTokens != 1024 {
		t.Fatalf("unexpected generation params: %#v", got)
	}
}

func TestStreamTextRejectsInactiveModelConfig(t *testing.T) {
	ctx := context.Background()
	repo := &fakeModelConfigRepo{
		byID: map[int64]*modelconfig.ModelConfig{
			2: {
				ID:       2,
				UserID:   10,
				Provider: "openai",
				Status:   modelconfig.StatusInactive,
			},
		},
	}
	service := NewService(repo, &fakeRuntimeClient{returnStream: &fakeTextStream{}})

	_, err := service.StreamText(ctx, actor.User{
		UserID: 10,
	}, StreamTextRequest{
		ModelConfigID: 2,
		Message:       "hello",
	})
	if !errors.Is(err, ErrModelConfigInactive) {
		t.Fatalf("expected ErrModelConfigInactive, got %v", err)
	}
}

type fakeRuntimeClient struct {
	lastReq      RuntimeStreamTextRequest
	returnStream TextStream
}

func (c *fakeRuntimeClient) StreamText(
	_ context.Context,
	_ actor.User,
	req RuntimeStreamTextRequest,
) (TextStream, error) {
	c.lastReq = req
	return c.returnStream, nil
}

type fakeTextStream struct{}

func (s *fakeTextStream) Recv() (*TextStreamChunk, error) {
	return &TextStreamChunk{Done: true}, nil
}

func (s *fakeTextStream) Close() error {
	return nil
}

type fakeModelConfigRepo struct {
	configs []*modelconfig.ModelConfig
	byID    map[int64]*modelconfig.ModelConfig
}

func (r *fakeModelConfigRepo) Create(
	context.Context,
	modelconfig.ModelConfig,
) (*modelconfig.ModelConfig, error) {
	panic("not implemented")
}

func (r *fakeModelConfigRepo) ListByUserID(context.Context, int64) ([]*modelconfig.ModelConfig, error) {
	return r.configs, nil
}

func (r *fakeModelConfigRepo) GetByID(
	_ context.Context,
	_ int64,
	id int64,
) (*modelconfig.ModelConfig, error) {
	config, ok := r.byID[id]
	if !ok {
		return nil, modelconfig.ErrNotFound
	}
	return config, nil
}

func (r *fakeModelConfigRepo) Update(
	context.Context,
	modelconfig.ModelConfig,
) (*modelconfig.ModelConfig, error) {
	panic("not implemented")
}

func (r *fakeModelConfigRepo) Delete(context.Context, int64, int64) error {
	panic("not implemented")
}
