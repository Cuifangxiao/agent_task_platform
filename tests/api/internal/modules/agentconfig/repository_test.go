package agentconfig

import (
	"regexp"
	"testing"
	"time"

	"github.com/DATA-DOG/go-sqlmock"
)

func TestUpdateConfigContinuesWhenOnlyBindingsChange(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("create sql mock: %v", err)
	}
	defer db.Close()

	repo := NewRepository(db)
	now := time.Now().UTC().Truncate(time.Second)
	maxTokens := 960000
	config := AgentConfig{
		ID:          1,
		UserID:      1,
		AgentID:     1,
		AgentKey:    AgentKeyPPT,
		AgentName:   "PPTAgent",
		Name:        "ppt",
		Description: "PPT config",
		Status:      StatusActive,
		NodeBindings: []NodeBinding{
			{NodeKey: "designer", ModelConfigID: 1, MaxTokens: &maxTokens},
		},
	}

	mock.ExpectBegin()
	mock.ExpectExec(`UPDATE agent_configs`).
		WithArgs(
			config.Name,
			config.Description,
			config.IsDefault,
			config.Status,
			config.UserID,
			config.ID,
		).
		WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectQuery(regexp.QuoteMeta(`SELECT EXISTS(
			SELECT 1
			FROM agent_configs
			WHERE user_id = ? AND id = ?
		)`)).
		WithArgs(config.UserID, config.ID).
		WillReturnRows(sqlmock.NewRows([]string{"exists"}).AddRow(true))
	mock.ExpectExec(regexp.QuoteMeta(`DELETE FROM agent_config_node_bindings WHERE agent_config_id = ?`)).
		WithArgs(config.ID).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectExec(`INSERT INTO agent_config_node_bindings`).
		WithArgs(config.ID, "designer", int64(1), nil, maxTokens).
		WillReturnResult(sqlmock.NewResult(11, 1))
	mock.ExpectQuery(regexp.QuoteMeta(selectAgentConfigColumns+`
			WHERE c.user_id = ? AND c.id = ?
			LIMIT 1
		`)).
		WithArgs(config.UserID, config.ID).
		WillReturnRows(sqlmock.NewRows([]string{
			"id", "user_id", "agent_id", "agent_key", "agent_name", "name", "description",
			"is_default", "status", "created_at", "updated_at",
		}).AddRow(
			config.ID,
			config.UserID,
			config.AgentID,
			config.AgentKey,
			config.AgentName,
			config.Name,
			config.Description,
			config.IsDefault,
			config.Status,
			now,
			now,
		))
	mock.ExpectQuery(`SELECT id, agent_config_id, node_key, model_config_id, temperature, max_tokens, created_at, updated_at`).
		WithArgs(config.ID).
		WillReturnRows(sqlmock.NewRows([]string{
			"id", "agent_config_id", "node_key", "model_config_id", "temperature", "max_tokens", "created_at", "updated_at",
		}).AddRow(11, config.ID, "designer", 1, nil, maxTokens, now, now))
	mock.ExpectCommit()

	updated, err := repo.UpdateConfig(t.Context(), config)
	if err != nil {
		t.Fatalf("UpdateConfig returned error when only bindings changed: %v", err)
	}
	if len(updated.NodeBindings) != 1 || updated.NodeBindings[0].ModelConfigID != 1 {
		t.Fatalf("updated bindings = %#v", updated.NodeBindings)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatalf("unmet SQL expectations: %v", err)
	}
}
