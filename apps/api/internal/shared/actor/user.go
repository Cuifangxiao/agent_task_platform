// Package actor contains the authenticated caller information shared by
// application modules. Keeping it here prevents business modules from
// depending on another module's transport DTOs.
package actor

// User identifies the authenticated user and session executing a use case.
type User struct {
	UserID    int64
	Username  string
	SessionID string
	Device    string
}

// Valid reports whether the actor can own user-scoped resources.
func (u User) Valid() bool {
	return u.UserID > 0
}
