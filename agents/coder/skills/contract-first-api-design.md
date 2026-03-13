# Contract-First API Design

**Tags:** coding, api, design, architecture, openapi

## Core Principle
Define the API contract (schema + endpoints + error codes) as an OpenAPI spec before writing any implementation — both producer and consumer can build in parallel against a stable interface.

## Process
1. **Write the spec first** — OpenAPI/AsyncAPI YAML. No implementation code until the contract is agreed upon by all consumers.
2. **Name resources as nouns, not verbs** — `/orders` not `/getOrders`. Use HTTP methods to express action (GET, POST, PUT, PATCH, DELETE).
3. **Define all error responses explicitly** — 400, 401, 403, 404, 409, 422, 500 — each with a machine-readable error code in the body (not just HTTP status).
4. **Version from day one** — `/v1/` in the path. Breaking changes require a new version; never mutate existing contracts.
5. **Generate mock servers** — Use the spec to spin up mocks so consumers can build before the backend exists.
6. **Validate at runtime** — Validate request and response payloads against the schema in tests and in production middleware.

## API Design Rules
- Treat the spec as the single source of truth — auto-generate docs, SDKs, and validation from it, never the reverse.
- Never expose internal domain model structure directly — design the API for the consumer's mental model, not the database schema.
- Pagination is not optional — any endpoint returning a list must have pagination from day one.
- Idempotency keys for mutations — POST/PUT endpoints that have side effects should support idempotency keys.
- Use 422 for semantic validation errors (correct format, wrong business logic), not 400 (bad format).

## Application
- Before any new endpoint: write the OpenAPI YAML first, review with the consumer team, then implement.
- For breaking changes: introduce `/v2/` alongside `/v1/`, migrate consumers, then deprecate with a sunset header.

## Source
Postman API Design Principles; Microsoft REST API Guidelines; OpenAPI Specification (openapi.org)
