package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadConfigAppliesDefaults(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	data := []byte(`
mysql:
  username: root
  database: agent_task_platform
jwt:
  secret: test-secret
`)

	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}

	if cfg.Server.Address() != "0.0.0.0:8080" {
		t.Fatalf("unexpected server address: %s", cfg.Server.Address())
	}
	if cfg.MySQL.Address() != "127.0.0.1:3306" {
		t.Fatalf("unexpected mysql address: %s", cfg.MySQL.Address())
	}
	if cfg.Redis.Address() != "127.0.0.1:6379" {
		t.Fatalf("unexpected redis address: %s", cfg.Redis.Address())
	}
	if cfg.PythonAgent.GRPCAddress() != "127.0.0.1:50051" {
		t.Fatalf("unexpected python agent address: %s", cfg.PythonAgent.GRPCAddress())
	}
	if cfg.PythonAgent.ArtifactsDir != "./apps/runtime/artifacts/decks" {
		t.Fatalf("unexpected artifact directory: %s", cfg.PythonAgent.ArtifactsDir)
	}
	if got := cfg.JWT.AccessExpireDuration().Minutes(); got != 30 {
		t.Fatalf("unexpected access token ttl minutes: %v", got)
	}
}

func TestLoadConfigRequiresSecrets(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	data := []byte(`
mysql:
  username: root
  database: agent_task_platform
`)

	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}

	if _, err := Load(path); err == nil {
		t.Fatal("expected missing jwt secret error")
	}
}
