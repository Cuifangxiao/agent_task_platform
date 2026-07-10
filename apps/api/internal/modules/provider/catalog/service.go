package catalog

import "context"

type RuntimeClient interface {
	ListProviders(ctx context.Context) ([]ProviderResponse, error)
}

type Service struct {
	runtimeClient RuntimeClient
}

func NewService(runtimeClient RuntimeClient) *Service {
	return &Service{runtimeClient: runtimeClient}
}

func (s *Service) List(ctx context.Context) (*ListResponse, error) {
	providers, err := s.runtimeClient.ListProviders(ctx)
	if err != nil {
		return nil, err
	}

	return &ListResponse{
		Providers: providers,
	}, nil
}
