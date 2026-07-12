package modelconfig

import (
	"strings"
	"testing"
	"time"
)

type scannerFunc func(dest ...any) error

func (f scannerFunc) Scan(dest ...any) error {
	return f(dest...)
}

func TestScanModelConfigIncludesModelType(t *testing.T) {
	if !strings.Contains(selectModelConfigColumns, "model_name, model_type, base_url") {
		t.Fatal("select columns must keep model_type aligned with scanModelConfig")
	}

	now := time.Now().UTC().Truncate(time.Second)
	config, err := scanModelConfig(scannerFunc(func(dest ...any) error {
		if len(dest) != 15 {
			t.Fatalf("scan destination count = %d, want 15", len(dest))
		}
		*dest[0].(*int64) = 9
		*dest[1].(*int64) = 7
		*dest[2].(*string) = "vision"
		*dest[3].(*string) = "qwen"
		*dest[4].(*string) = "qwen3-vl-plus"
		*dest[5].(*ModelType) = ModelTypeMultimodal
		*dest[6].(*string) = "https://example.com/v1"
		*dest[7].(*string) = "sk-test"
		*dest[8].(*[]byte) = []byte(`{"supports_image_input":true}`)
		*dest[9].(*float64) = 0.5
		*dest[10].(*int) = 2048
		*dest[11].(*bool) = true
		*dest[12].(*Status) = StatusActive
		*dest[13].(*time.Time) = now
		*dest[14].(*time.Time) = now
		return nil
	}))
	if err != nil {
		t.Fatalf("scanModelConfig returned error: %v", err)
	}
	if config.ModelType != ModelTypeMultimodal {
		t.Fatalf("model type = %q, want %q", config.ModelType, ModelTypeMultimodal)
	}
	if config.ProviderOptions != `{"supports_image_input":true}` {
		t.Fatalf("provider options = %q", config.ProviderOptions)
	}
}
