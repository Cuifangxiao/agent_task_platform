package credential

type TestCredentialRequest struct {
	Provider string `json:"provider" binding:"required,min=2,max=32"`
	APIKey   string `json:"api_key" binding:"required,min=1,max=512"`
	BaseURL  string `json:"base_url" binding:"omitempty,max=255"`
}

type TestCredentialResponse struct {
	RequestID string `json:"request_id"`
	Provider  string `json:"provider"`
	Status    string `json:"status"`
	Message   string `json:"message"`
}
