package validatex

import "testing"

func TestHTTPURL(t *testing.T) {
	tests := []struct {
		name    string
		rawURL  string
		wantErr bool
	}{
		{name: "empty", rawURL: "", wantErr: false},
		{name: "http", rawURL: "http://localhost:11434", wantErr: false},
		{name: "https", rawURL: "https://api.example.com/v1", wantErr: false},
		{name: "missing scheme", rawURL: "api.example.com/v1", wantErr: true},
		{name: "unsupported scheme", rawURL: "ftp://api.example.com", wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := HTTPURL(tt.rawURL)
			if (err != nil) != tt.wantErr {
				t.Fatalf("HTTPURL() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}
