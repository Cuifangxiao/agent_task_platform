package pptagent

import (
	"strings"
	"testing"
)

func TestValidateGenerateDeckRequest(t *testing.T) {
	tests := []struct {
		name    string
		request GenerateDeckRequest
		wantErr string
	}{
		{
			name:    "valid",
			request: GenerateDeckRequest{Mode: "quick", SourceFiles: []SourceFile{{FileName: "brief.md", Content: []byte("hello")}}},
		},
		{
			name:    "valid raster image",
			request: GenerateDeckRequest{Mode: "quick", SourceFiles: []SourceFile{{FileName: "hero.WeBP", Content: []byte("image")}}},
		},
		{
			name:    "invalid mode",
			request: GenerateDeckRequest{Mode: "fast"},
			wantErr: "mode",
		},
		{
			name:    "unsupported source",
			request: GenerateDeckRequest{SourceFiles: []SourceFile{{FileName: "payload.exe", Content: []byte("x")}}},
			wantErr: "unsupported",
		},
		{
			name:    "empty source",
			request: GenerateDeckRequest{SourceFiles: []SourceFile{{FileName: "brief.md"}}},
			wantErr: "empty",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := validateGenerateDeckRequest(test.request)
			if test.wantErr == "" && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if test.wantErr != "" && (err == nil || !strings.Contains(err.Error(), test.wantErr)) {
				t.Fatalf("error = %v, want it to contain %q", err, test.wantErr)
			}
		})
	}
}
