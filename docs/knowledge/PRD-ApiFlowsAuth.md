# API Flows — Auth

---

## POST /api/auth/register

```
Client
  │
  └─ POST /api/auth/register
      body: { email, password, name? }
      │
      ├─ backend: check duplicate email
      │     SELECT users WHERE email = ?
      │     → 409 Conflict if already registered
      │
      ├─ backend: provision PostgreSQL role
      │     db_role = "user_{uuid[:8]}"
      │     CREATE ROLE {db_role} LOGIN PASSWORD '<random>'
      │     → 500 if role creation fails
      │
      ├─ backend: persist user
      │     INSERT users (id, email, name, hashed_password=bcrypt(password), db_role)
      │
      ├─ backend: sign JWT
      │     payload: { sub: user_id, iat: now, exp: now + JWT_EXPIRY_MINUTES }
      │     algorithm: HS256
      │
      └─ response 201 Created
            { access_token, token_type: "bearer", user_id, email }
```

---

## POST /api/auth/login

```
Client
  │
  └─ POST /api/auth/login
      body: { email, password }
      │
      ├─ backend: look up user
      │     SELECT users WHERE email = ?
      │     → 401 Unauthorized if not found
      │
      ├─ backend: verify password
      │     bcrypt.checkpw(password, hashed_password)
      │     → 401 Unauthorized on mismatch
      │
      ├─ backend: sign JWT
      │     payload: { sub: user_id, iat: now, exp: now + JWT_EXPIRY_MINUTES }
      │
      └─ response 200 OK
            { access_token, token_type: "bearer", user_id, email }
```

---

## JWT validation (all protected endpoints)

Every endpoint except `/health` and `/api/internal/*` runs this check before the handler:

```
Authorization: Bearer <token>
  │
  ├─ decode JWT (HS256, JWT_SECRET)
  │     → 401 if missing, malformed, or expired
  │
  ├─ extract sub → user_id
  │
  ├─ SELECT users WHERE id = user_id
  │     → 401 if user not found
  │
  └─ inject User object into route handler
```
