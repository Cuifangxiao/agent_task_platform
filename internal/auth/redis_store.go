package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

type RedisSessionStore struct {
	client *redis.Client
}

func NewRedisSessionStore(client *redis.Client) *RedisSessionStore {
	return &RedisSessionStore{client: client}
}

func (s *RedisSessionStore) SaveSession(ctx context.Context, session Session, refreshToken string, ttl time.Duration) error {
	payload, err := json.Marshal(session)
	if err != nil {
		return fmt.Errorf("marshal session: %w", err)
	}

	pipe := s.client.TxPipeline()
	pipe.Set(ctx, sessionKey(session.UserID, session.Device), payload, ttl)
	pipe.Set(ctx, refreshTokenKey(session.UserID, session.Device), refreshToken, ttl)

	if _, err := pipe.Exec(ctx); err != nil {
		return fmt.Errorf("save session: %w", err)
	}

	return nil
}

func (s *RedisSessionStore) GetSession(ctx context.Context, userID int64, device string) (*Session, error) {
	value, err := s.client.Get(ctx, sessionKey(userID, device)).Result()
	if err != nil {
		if err == redis.Nil {
			return nil, nil
		}
		return nil, fmt.Errorf("get session: %w", err)
	}

	var session Session
	if err := json.Unmarshal([]byte(value), &session); err != nil {
		return nil, fmt.Errorf("unmarshal session: %w", err)
	}

	return &session, nil
}

func (s *RedisSessionStore) GetRefreshToken(ctx context.Context, userID int64, device string) (string, error) {
	value, err := s.client.Get(ctx, refreshTokenKey(userID, device)).Result()
	if err != nil {
		if err == redis.Nil {
			return "", nil
		}
		return "", fmt.Errorf("get refresh token: %w", err)
	}

	return value, nil
}

func (s *RedisSessionStore) DeleteSession(ctx context.Context, userID int64, device string) error {
	pipe := s.client.TxPipeline()
	pipe.Del(ctx, sessionKey(userID, device))
	pipe.Del(ctx, refreshTokenKey(userID, device))

	if _, err := pipe.Exec(ctx); err != nil {
		return fmt.Errorf("delete session: %w", err)
	}

	return nil
}

func (s *RedisSessionStore) BlacklistAccessToken(ctx context.Context, jti string, ttl time.Duration) error {
	if ttl <= 0 {
		return nil
	}

	if err := s.client.Set(ctx, accessBlacklistKey(jti), "1", ttl).Err(); err != nil {
		return fmt.Errorf("blacklist access token: %w", err)
	}

	return nil
}

func (s *RedisSessionStore) IsAccessTokenBlacklisted(ctx context.Context, jti string) (bool, error) {
	exists, err := s.client.Exists(ctx, accessBlacklistKey(jti)).Result()
	if err != nil {
		return false, fmt.Errorf("check access token blacklist: %w", err)
	}

	return exists > 0, nil
}

func sessionKey(userID int64, device string) string {
	return fmt.Sprintf("auth:session:user:%d:device:%s", userID, device)
}

func refreshTokenKey(userID int64, device string) string {
	return fmt.Sprintf("auth:refresh:user:%d:device:%s", userID, device)
}

func accessBlacklistKey(jti string) string {
	return fmt.Sprintf("auth:blacklist:access:%s", jti)
}
