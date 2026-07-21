package mysql

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFindDirectoryUpward(t *testing.T) {
	root := t.TempDir()
	migrationDir := filepath.Join(root, "migrations", "mysql")
	start := filepath.Join(root, "apps", "api", "config")
	if err := os.MkdirAll(migrationDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(start, 0o755); err != nil {
		t.Fatal(err)
	}

	got, ok := findDirectoryUpward(start, filepath.Join("migrations", "mysql"))
	if !ok {
		t.Fatal("migration directory was not found")
	}
	if got != migrationDir {
		t.Fatalf("directory = %q, want %q", got, migrationDir)
	}
}

func TestSplitSQLStatementsKeepsQuotedSemicolons(t *testing.T) {
	script := "INSERT INTO sample(value) VALUES ('a;b'); UPDATE sample SET value = \"c;d\";"
	statements := splitSQLStatements(script)
	if len(statements) != 2 {
		t.Fatalf("statements = %#v", statements)
	}
}

func TestPptRunStoreMigrationDefinesDurableSnapshotAndCompleteEventLog(t *testing.T) {
	dir, err := resolveMySQLMigrationDir("")
	if err != nil {
		t.Fatal(err)
	}
	payload, err := os.ReadFile(filepath.Join(dir, "011_create_ppt_run_store.sql"))
	if err != nil {
		t.Fatal(err)
	}
	script := string(payload)
	statements := splitSQLStatements(script)
	if len(statements) != 2 {
		t.Fatalf("migration statement count = %d, want 2", len(statements))
	}
	for _, required := range []string{
		"CREATE TABLE IF NOT EXISTS ppt_runs",
		"client_request_id VARCHAR(128)",
		"COLLATE utf8mb4_bin",
		"request_fingerprint CHAR(64)",
		"options_json JSON NOT NULL",
		"result_json JSON NULL",
		"error_json JSON NULL",
		"worker_active TINYINT(1)",
		"GENERATED ALWAYS AS",
		"UNIQUE KEY uk_ppt_runs_owner_client_request (owner_id, client_request_id)",
		"UNIQUE KEY uk_ppt_runs_owner_active_thread (owner_id, active_thread_id)",
		"CREATE TABLE IF NOT EXISTS ppt_run_events",
		"payload_json JSON NOT NULL",
		"PRIMARY KEY (run_id, sequence)",
		"UNIQUE KEY uk_ppt_run_events_event_id (event_id)",
		"FOREIGN KEY (owner_id) REFERENCES users (id)",
		"FOREIGN KEY (run_id) REFERENCES ppt_runs (run_id)",
	} {
		if !strings.Contains(script, required) {
			t.Errorf("migration is missing %q", required)
		}
	}
	if strings.Contains(script, "FOREIGN KEY (agent_config_id)") {
		t.Fatal("agent_config_id must remain a historical reference without a foreign key")
	}
}

func TestPptEventIDMigrationSupportsStableProducerIDs(t *testing.T) {
	dir, err := resolveMySQLMigrationDir("")
	if err != nil {
		t.Fatal(err)
	}
	payload, err := os.ReadFile(filepath.Join(dir, "012_widen_ppt_event_ids.sql"))
	if err != nil {
		t.Fatal(err)
	}
	script := string(payload)
	statements := splitSQLStatements(script)
	if len(statements) != 2 {
		t.Fatalf("migration statement count = %d, want 2", len(statements))
	}
	for _, required := range []string{
		"ALTER TABLE ppt_runs",
		"event_id VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NULL",
		"ALTER TABLE ppt_run_events",
		"event_id VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL",
	} {
		if !strings.Contains(script, required) {
			t.Errorf("migration is missing %q", required)
		}
	}
	if strings.Contains(strings.ToUpper(script), "DROP INDEX") {
		t.Fatal("event_id widening must preserve the existing unique index")
	}
}
