package pythonruntime

import (
	"agent-task-platform/apps/api/internal/shared/actor"
	"context"
	"encoding/json"
	"fmt"
	"time"

	"agent-task-platform/apps/api/internal/modules/pptagent"
	"agent-task-platform/apps/api/internal/modules/provider/catalog"
	"agent-task-platform/apps/api/internal/modules/provider/credential"
	"agent-task-platform/apps/api/internal/modules/textgeneration"
	providerv1 "agent-task-platform/contracts/gen/go/v1/provider"
	"google.golang.org/grpc"
)

const maxDeckGRPCMessageBytes = 300 << 20

type Client struct {
	grpcClient providerv1.ProviderRuntimeServiceClient
	timeout    time.Duration
}

func NewClient(conn grpc.ClientConnInterface) *Client {
	return &Client{
		grpcClient: providerv1.NewProviderRuntimeServiceClient(conn),
		timeout:    5 * time.Second,
	}
}

func (c *Client) TestCredential(
	ctx context.Context,
	user actor.User,
	req credential.TestCredentialRequest,
) (*credential.TestCredentialResponse, error) {
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	resp, err := c.grpcClient.TestCredential(callCtx, &providerv1.TestCredentialRequest{
		User: &providerv1.UserContext{
			UserId:    user.UserID,
			Username:  user.Username,
			SessionId: user.SessionID,
			Device:    user.Device,
		},
		Provider: req.Provider,
		ApiKey:   req.APIKey,
		BaseUrl:  req.BaseURL,
	})
	if err != nil {
		return nil, fmt.Errorf("call python runtime test credential failed: %w", err)
	}

	return &credential.TestCredentialResponse{
		RequestID: resp.GetRequestId(),
		Provider:  resp.GetProvider(),
		Status:    resp.GetStatus(),
		Message:   resp.GetMessage(),
	}, nil
}

func (c *Client) ListProviders(ctx context.Context) ([]catalog.ProviderResponse, error) {
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	resp, err := c.grpcClient.ListProviders(callCtx, &providerv1.ListProvidersRequest{})
	if err != nil {
		return nil, fmt.Errorf("call python runtime list providers failed: %w", err)
	}

	providers := make([]catalog.ProviderResponse, 0, len(resp.GetProviders()))
	for _, provider := range resp.GetProviders() {
		models := make([]catalog.ModelResponse, 0, len(provider.GetModels()))
		for _, model := range provider.GetModels() {
			parameters := make([]catalog.ModelParameterResponse, 0, len(model.GetParameters()))
			for _, parameter := range model.GetParameters() {
				parameters = append(parameters, catalog.ModelParameterResponse{
					Name:         parameter.GetName(),
					DisplayName:  parameter.GetDisplayName(),
					Type:         parameter.GetType(),
					Description:  parameter.GetDescription(),
					DefaultValue: parameter.GetDefaultValue(),
					Options:      parameter.GetOptions(),
					Required:     parameter.GetRequired(),
					Min:          parameter.Min,
					Max:          parameter.Max,
				})
			}

			models = append(models, catalog.ModelResponse{
				ID:                 model.GetId(),
				ModelName:          model.GetModelName(),
				DisplayName:        model.GetDisplayName(),
				Type:               model.GetType(),
				ReasoningSupported: model.GetReasoningSupported(),
				Parameters:         parameters,
			})
		}

		providers = append(providers, catalog.ProviderResponse{
			Name:                   provider.GetName(),
			DisplayName:            provider.GetDisplayName(),
			Description:            provider.GetDescription(),
			Manager:                provider.GetManager(),
			SDK:                    provider.GetSdk(),
			DefaultBaseURL:         provider.GetDefaultBaseUrl(),
			SupportsCredentialTest: provider.GetSupportsCredentialTest(),
			SupportsCustomBaseURL:  provider.GetSupportsCustomBaseUrl(),
			Models:                 models,
		})
	}

	return providers, nil
}

func (c *Client) StreamText(
	ctx context.Context,
	user actor.User,
	req textgeneration.RuntimeStreamTextRequest,
) (textgeneration.TextStream, error) {
	callCtx, cancel := context.WithCancel(ctx)

	stream, err := c.grpcClient.StreamText(callCtx, &providerv1.StreamTextRequest{
		User: &providerv1.UserContext{
			UserId:    user.UserID,
			Username:  user.Username,
			SessionId: user.SessionID,
			Device:    user.Device,
		},
		Provider:            req.Provider,
		Model:               req.Model,
		ApiKey:              req.APIKey,
		BaseUrl:             req.BaseURL,
		Prompt:              req.Prompt,
		SystemPrompt:        req.SystemPrompt,
		Temperature:         req.Temperature,
		MaxTokens:           int32(req.MaxTokens),
		ProviderOptionsJson: req.ProviderOptionsJSON,
	})
	if err != nil {
		cancel()
		return nil, fmt.Errorf("call python runtime stream text failed: %w", err)
	}

	return &textStream{
		stream: stream,
		cancel: cancel,
	}, nil
}

func (c *Client) GenerateDeck(
	ctx context.Context,
	user actor.User,
	req pptagent.RuntimeGenerateDeckRequest,
) (pptagent.DeckStream, error) {
	callCtx, cancel := context.WithCancel(ctx)

	nodeModelConfigs := make([]*providerv1.DeckNodeModelConfig, 0, len(req.NodeModelConfigs))
	for _, config := range req.NodeModelConfigs {
		nodeModelConfigs = append(nodeModelConfigs, &providerv1.DeckNodeModelConfig{
			NodeKey:             config.NodeKey,
			Provider:            config.Provider,
			Model:               config.Model,
			ApiKey:              config.APIKey,
			BaseUrl:             config.BaseURL,
			ProviderOptionsJson: config.ProviderOptionsJSON,
			Temperature:         config.Temperature,
			MaxTokens:           int32(config.MaxTokens),
			SupportsImageInput:  config.SupportsImageInput,
		})
	}

	sourceFiles := make([]*providerv1.DeckSourceFile, 0, len(req.SourceFiles))
	for _, file := range req.SourceFiles {
		sourceFiles = append(sourceFiles, &providerv1.DeckSourceFile{
			Filename:   file.FileName,
			Content:    file.Content,
			ResourceId: file.ResourceID,
			LocalPath:  file.LocalPath,
			Sha256:     file.SHA256,
			SizeBytes:  file.SizeBytes,
		})
	}

	stream, err := c.grpcClient.GenerateDeck(callCtx, &providerv1.GenerateDeckRequest{
		JobId:          req.JobID,
		GenerationMode: req.Mode,
		User: &providerv1.UserContext{
			UserId:    user.UserID,
			Username:  user.Username,
			SessionId: user.SessionID,
			Device:    user.Device,
		},
		UserDescription:    req.Description,
		SourceFileFilename: req.SourceFileName,
		SourceFile:         req.SourceFile,
		NodeModelConfigs:   nodeModelConfigs,
		SourceFiles:        sourceFiles,
	}, grpc.MaxCallSendMsgSize(maxDeckGRPCMessageBytes), grpc.MaxCallRecvMsgSize(maxDeckGRPCMessageBytes))
	if err != nil {
		cancel()
		return nil, fmt.Errorf("call python runtime generate deck failed: %w", err)
	}

	return &deckStream{
		stream: stream,
		cancel: cancel,
	}, nil
}

func (c *Client) ResumeDeck(
	ctx context.Context,
	user actor.User,
	req pptagent.RuntimeResumeDeckRequest,
) (pptagent.DeckStream, error) {
	callCtx, cancel := context.WithCancel(ctx)

	nodeModelConfigs := make([]*providerv1.DeckNodeModelConfig, 0, len(req.NodeModelConfigs))
	for _, config := range req.NodeModelConfigs {
		nodeModelConfigs = append(nodeModelConfigs, &providerv1.DeckNodeModelConfig{
			NodeKey:             config.NodeKey,
			Provider:            config.Provider,
			Model:               config.Model,
			ApiKey:              config.APIKey,
			BaseUrl:             config.BaseURL,
			ProviderOptionsJson: config.ProviderOptionsJSON,
			Temperature:         config.Temperature,
			MaxTokens:           int32(config.MaxTokens),
			SupportsImageInput:  config.SupportsImageInput,
		})
	}

	stream, err := c.grpcClient.ResumeDeck(callCtx, &providerv1.ResumeDeckRequest{
		JobId: req.JobID,
		User: &providerv1.UserContext{
			UserId:    user.UserID,
			Username:  user.Username,
			SessionId: user.SessionID,
			Device:    user.Device,
		},
		RequestId:        req.RequestID,
		OutlineMarkdown:  req.OutlineMarkdown,
		NodeModelConfigs: nodeModelConfigs,
		Action:           req.Action,
		PayloadJson:      req.PayloadJSON,
	}, grpc.MaxCallSendMsgSize(maxDeckGRPCMessageBytes), grpc.MaxCallRecvMsgSize(maxDeckGRPCMessageBytes))
	if err != nil {
		cancel()
		return nil, fmt.Errorf("call python runtime resume deck failed: %w", err)
	}

	return &deckStream{
		stream: stream,
		cancel: cancel,
	}, nil
}

type textStream struct {
	stream providerv1.ProviderRuntimeService_StreamTextClient
	cancel context.CancelFunc
}

func (s *textStream) Recv() (*textgeneration.TextStreamChunk, error) {
	resp, err := s.stream.Recv()
	if err != nil {
		return nil, err
	}

	return &textgeneration.TextStreamChunk{
		RequestID: resp.GetRequestId(),
		Delta:     resp.GetDelta(),
		Done:      resp.GetDone(),
		Error:     resp.GetError(),
		Sequence:  resp.GetSequence(),
	}, nil
}

func (s *textStream) Close() error {
	if s.cancel != nil {
		s.cancel()
	}
	return nil
}

type deckStream struct {
	stream deckRuntimeStream
	cancel context.CancelFunc
}

type deckRuntimeStream interface {
	Recv() (*providerv1.GenerateDeckResponse, error)
}

func (s *deckStream) Recv() (*pptagent.DeckStreamChunk, error) {
	resp, err := s.stream.Recv()
	if err != nil {
		return nil, err
	}

	chunk := &pptagent.DeckStreamChunk{
		EventVersion:    int(resp.GetEventVersion()),
		JobID:           resp.GetJobId(),
		RequestID:       resp.GetRequestId(),
		EventType:       resp.GetEventType(),
		Stage:           resp.GetStage(),
		Message:         resp.GetMessage(),
		Channel:         resp.GetChannel(),
		TextDelta:       resp.GetTextDelta(),
		Content:         resp.GetContent(),
		OutlineMarkdown: resp.GetOutlineMarkdown(),
		Payload:         parsePayload(resp.GetPayloadJson()),
		Done:            resp.GetDone(),
		Error:           resp.GetError(),
		ErrorCode:       resp.GetErrorCode(),
		StageStatus:     resp.GetStageStatus(),
		Severity:        resp.GetSeverity(),
		ActionRequired:  resp.GetActionRequired(),
		Retryable:       resp.GetRetryable(),
		Sequence:        resp.GetSequence(),
	}
	if progress := resp.GetProgress(); progress != nil && (progress.GetCurrent() != 0 || progress.GetTotal() != 0) {
		chunk.Progress = &pptagent.DeckProgress{Current: int(progress.GetCurrent()), Total: int(progress.GetTotal())}
	}
	if slide := resp.GetSlide(); slide != nil && slide.GetIndex() > 0 {
		chunk.Slide = &pptagent.DeckSlideEvent{Index: int(slide.GetIndex()), Version: int(slide.GetVersion())}
	}
	return chunk, nil
}

func (s *deckStream) Close() error {
	if s.cancel != nil {
		s.cancel()
	}
	return nil
}

func parsePayload(raw string) json.RawMessage {
	if raw == "" {
		return nil
	}
	if json.Valid([]byte(raw)) {
		return json.RawMessage(raw)
	}

	encoded, err := json.Marshal(raw)
	if err != nil {
		return nil
	}
	return encoded
}
