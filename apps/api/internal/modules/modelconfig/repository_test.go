package modelconfig

import (
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/DATA-DOG/go-sqlmock"
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

func TestUpdateReturnsExistingConfigWhenMySQLReportsZeroAffectedRows(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("create sql mock: %v", err)
	}
	defer db.Close()

	repo := NewRepository(db)
	now := time.Now().UTC().Truncate(time.Second)
	config := ModelConfig{
		ID:              4,
		UserID:          1,
		Name:            "deepseekpro",
		Provider:        "deepseek",
		ModelName:       "deepseek-v4-flash",
		ModelType:       ModelTypeText,
		BaseURL:         "https://api.deepseek.com",
		APIKey:          "sk-test",
		ProviderOptions: `{"thinking":"disabled","reasoning_effort":"high"}`,
		Temperature:     1,
		MaxTokens:       4096,
		IsDefault:       false,
		Status:          StatusActive,
	}

	mock.ExpectBegin()
	mock.ExpectExec(regexp.QuoteMeta(updateModelConfigQuery)).
		WithArgs(
			config.Name,
			config.Provider,
			config.ModelName,
			config.ModelType,
			config.BaseURL,
			config.APIKey,
			config.ProviderOptions,
			config.Temperature,
			config.MaxTokens,
			config.IsDefault,
			config.Status,
			config.UserID,
			config.ID,
		).
		WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectQuery(regexp.QuoteMeta(selectModelConfigColumns+`
			WHERE user_id = ? AND id = ?
			LIMIT 1
		`)).
		WithArgs(config.UserID, config.ID).
		WillReturnRows(sqlmock.NewRows([]string{
			"id", "user_id", "name", "provider", "model_name", "model_type", "base_url", "api_key", "provider_options",
			"temperature", "max_tokens", "is_default", "status", "created_at", "updated_at",
		}).AddRow(
			config.ID,
			config.UserID,
			config.Name,
			config.Provider,
			config.ModelName,
			config.ModelType,
			config.BaseURL,
			config.APIKey,
			[]byte(config.ProviderOptions),
			config.Temperature,
			config.MaxTokens,
			config.IsDefault,
			config.Status,
			now,
			now,
		))
	mock.ExpectCommit()

	updated, err := repo.Update(t.Context(), config)
	if err != nil {
		t.Fatalf("Update returned error for an unchanged existing row: %v", err)
	}
	if updated.ID != config.ID || updated.ModelName != config.ModelName {
		t.Fatalf("updated config = %#v, want id=%d model=%q", updated, config.ID, config.ModelName)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatalf("unmet SQL expectations: %v", err)
	}
}
