package pythonruntime

import (
	"context"
	"fmt"
	"time"

	"agent-task-platform/apps/api/internal/modules/pptagent"
	"agent-task-platform/apps/api/internal/modules/provider/catalog"
	"agent-task-platform/apps/api/internal/modules/provider/credential"
	"agent-task-platform/apps/api/internal/modules/textgeneration"
	"agent-task-platform/apps/api/internal/shared/actor"
	pptagentpb "agent-task-platform/contracts/gen/go/pptagent"
	providerv1 "agent-task-platform/contracts/gen/go/v1/provider"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/types/known/structpb"
)

const maxPPTGRPCMessageBytes = 300 << 20

type Client struct {
	grpcClient providerv1.ProviderRuntimeServiceClient
	pptClient  pptagentpb.PptAgentServiceClient
	timeout    time.Duration
}

func NewClient(conn grpc.ClientConnInterface) *Client {
	return &Client{
		grpcClient: providerv1.NewProviderRuntimeServiceClient(conn),
		pptClient:  pptagentpb.NewPptAgentServiceClient(conn),
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

func (c *Client) GeneratePpt(ctx context.Context, req pptagent.RuntimeRequest) (pptagent.EventStream, error) {
	callCtx, cancel := context.WithCancel(ctx)
	models := make([]*pptagentpb.ModelConfig, 0, len(req.Models))
	for _, config := range req.Models {
		options, err := structpb.NewStruct(config.ProviderOptions)
		if err != nil {
			cancel()
			return nil, fmt.Errorf("encode provider options for %s: %w", config.NodeKey, err)
		}
		models = append(models, &pptagentpb.ModelConfig{
			NodeKey:            config.NodeKey,
			Provider:           config.Provider,
			Model:              config.Model,
			ApiKey:             config.APIKey,
			BaseUrl:            config.BaseURL,
			ProviderOptions:    options,
			Temperature:        config.Temperature,
			MaxTokens:          int32(config.MaxTokens),
			SupportsImageInput: config.SupportsImageInput,
		})
	}
	attachments := make([]*pptagentpb.InputResource, 0, len(req.Attachments))
	for _, attachment := range req.Attachments {
		attachments = append(attachments, &pptagentpb.InputResource{
			ResourceId: attachment.ResourceID,
			Name:       attachment.Name,
			MimeType:   attachment.MimeType,
			Content:    attachment.Content,
			Sha256:     attachment.SHA256,
		})
	}
	stream, err := c.pptClient.GeneratePpt(callCtx, &pptagentpb.GeneratePptRequest{
		TaskId:      req.TaskID,
		Description: req.Description,
		Attachments: attachments,
		Models:      models,
	}, grpc.MaxCallSendMsgSize(maxPPTGRPCMessageBytes), grpc.MaxCallRecvMsgSize(maxPPTGRPCMessageBytes))
	if err != nil {
		cancel()
		return nil, fmt.Errorf("call python runtime generate ppt: %w", err)
	}
	return &pptStream{
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

type pptStream struct {
	stream pptRuntimeStream
	cancel context.CancelFunc
}

type pptRuntimeStream interface {
	Recv() (*pptagentpb.AgentEvent, error)
}

func (s *pptStream) Recv() (*pptagent.AgentEvent, error) {
	resp, err := s.stream.Recv()
	if err != nil {
		return nil, err
	}
	event := &pptagent.AgentEvent{
		EventID:   resp.GetEventId(),
		Revision:  resp.GetRevision(),
		TaskID:    resp.GetTaskId(),
		Sequence:  resp.GetSequence(),
		Type:      pptagent.EventType(resp.GetType().String()),
		Stage:     pptagent.Stage(resp.GetStage().String()),
		Message:   resp.GetMessage(),
		Resources: mapResources(resp.GetResources()),
	}
	if createdAt := resp.GetCreatedAt(); createdAt != nil {
		if err := createdAt.CheckValid(); err != nil {
			return nil, fmt.Errorf("invalid ppt event timestamp: %w", err)
		}
		event.CreatedAt = createdAt.AsTime()
	}
	if progress := resp.GetProgress(); progress != nil {
		event.Progress = &pptagent.Progress{
			Current: progress.GetCurrent(), Percent: progress.GetPercent(), Total: progress.GetTotal(), Unit: progress.GetUnit(),
		}
	}
	if page := resp.GetPage(); page != nil {
		event.Page = &pptagent.PageDetail{
			PageID: page.GetPageId(), PageIndex: page.GetPageIndex(), PageCount: page.GetPageCount(), Title: page.GetTitle(),
		}
	}
	if detail := resp.GetError(); detail != nil {
		event.Error = &pptagent.ErrorDetail{
			Code: detail.GetCode(), Message: detail.GetMessage(), DebugMessage: detail.GetDebugMessage(), Retryable: detail.GetRetryable(),
		}
	}
	if result := resp.GetFinalResult(); result != nil {
		event.FinalResult = &pptagent.FinalResult{
			PPTFile: mapResource(result.GetPptFile()), SlideCount: result.GetSlideCount(), ElapsedMS: result.GetElapsedMs(),
		}
	}
	return event, nil
}

func (s *pptStream) Close() error {
	if s.cancel != nil {
		s.cancel()
	}
	return nil
}

func mapResources(resources []*pptagentpb.ResourceRef) []pptagent.ResourceRef {
	mapped := make([]pptagent.ResourceRef, 0, len(resources))
	for _, resource := range resources {
		if resource != nil {
			mapped = append(mapped, mapResource(resource))
		}
	}
	return mapped
}

func mapResource(resource *pptagentpb.ResourceRef) pptagent.ResourceRef {
	if resource == nil {
		return pptagent.ResourceRef{}
	}
	return pptagent.ResourceRef{
		ResourceID:   resource.GetResourceId(),
		Type:         pptagent.ResourceType(resource.GetType().String()),
		Name:         resource.GetName(),
		RelativePath: resource.GetRelativePath(),
		MimeType:     resource.GetMimeType(),
		SizeBytes:    resource.GetSizeBytes(),
		SHA256:       resource.GetSha256(),
	}
}
