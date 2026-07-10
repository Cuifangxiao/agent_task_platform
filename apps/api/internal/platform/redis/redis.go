// Package redisstore owns Redis connectivity.
package redisstore

import (
	"context"
	"fmt"
	"time"

	"agent-task-platform/apps/api/internal/config"

	"github.com/redis/go-redis/v9"
)

func Open(cfg config.RedisConfig) (*redis.Client, error) {
	client := redis.NewClient(&redis.Options{
		Addr:     cfg.Address(),
		Password: cfg.Password,
		DB:       cfg.DB,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := client.Ping(ctx).Err(); err != nil {
		_ = client.Close()
		return nil, fmt.Errorf("ping redis failed: %w", err)
	}

	return client, nil
}
