package catalog

type ModelResponse struct {
	ID                 string                   `json:"id"`
	ModelName          string                   `json:"model_name"`
	DisplayName        string                   `json:"display_name"`
	Type               string                   `json:"type"`
	ReasoningSupported bool                     `json:"reasoning_supported"`
	Parameters         []ModelParameterResponse `json:"parameters"`
}

type ModelParameterResponse struct {
	Name         string   `json:"name"`
	DisplayName  string   `json:"display_name"`
	Type         string   `json:"type"`
	Description  string   `json:"description"`
	DefaultValue string   `json:"default_value"`
	Options      []string `json:"options"`
	Required     bool     `json:"required"`
	Min          *float64 `json:"min,omitempty"`
	Max          *float64 `json:"max,omitempty"`
}

type ProviderResponse struct {
	Name                   string          `json:"name"`
	DisplayName            string          `json:"display_name"`
	Description            string          `json:"description"`
	Manager                string          `json:"manager"`
	SDK                    string          `json:"sdk"`
	DefaultBaseURL         string          `json:"default_base_url"`
	SupportsCredentialTest bool            `json:"supports_credential_test"`
	SupportsCustomBaseURL  bool            `json:"supports_custom_base_url"`
	Models                 []ModelResponse `json:"models"`
}

type ListResponse struct {
	Providers []ProviderResponse `json:"providers"`
}
