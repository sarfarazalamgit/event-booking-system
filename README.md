
# Event Booking System

A role-based event booking REST API built with Python 3.14, FastAPI,
uv, PostgreSQL, Redis, ARQ, Docker, and Resend.

## 1. Features

- Separate organizer and customer roles.
- JWT access-token authentication.
- Argon2 password hashing.
- Public event listing and event detail endpoints.
- Organizer-owned event creation, updates, and deletion.
- Customer bookings and booking history.
- PostgreSQL inventory constraints and row locking.
- Asynchronous booking confirmation emails.
- Asynchronous event update notification emails.
- Automated API integration tests.
- Locust-based concurrency and performance testing.
- Docker Compose local development.

## 2. Architecture

The FastAPI service handles validation, authentication, authorization,
and business logic. PostgreSQL stores users, events, and bookings.
Redis transports background jobs to ARQ workers. The worker sends
transactional emails through the Resend HTTPS API.

The API and worker are separate processes so email latency does not
need to be part of the API response time.

## 3. Design decisions

### Language and dependencies

Python 3.14 is the target runtime. uv manages dependencies and creates
a lockfile so package versions can be reproduced. FastAPI provides
request validation, dependency injection, OpenAPI documentation, and
asynchronous endpoints.

### Database

PostgreSQL is the authoritative store for bookings and inventory.
SQLAlchemy's asynchronous engine uses asyncpg.

The first version creates tables at application startup to make local
setup straightforward. Production deployments should use versioned
Alembic migrations rather than relying on create_all.

### Authentication and authorization

Passwords are hashed with Argon2. The login endpoint issues signed JWT
access tokens with an expiration time.

Only organizers can manage events, and an organizer can modify or
delete only their own events. Only customers can book tickets and view
their booking history.

Registration accepts a role to simplify this assignment's demo. A
public production service should not let arbitrary users grant
themselves organizer privileges. Organizer accounts should be approved
or created through a trusted administrative process.

### Booking correctness

Each booking has a positive quantity. A customer may have only one
booking row per event. Each request may book up to ten tickets.

The booking transaction locks the relevant event row with
SELECT ... FOR UPDATE. It checks inventory, inserts the booking, and
increments tickets_sold within the same transaction.

The database also enforces that sold tickets cannot exceed capacity.
A duplicate booking or insufficient inventory returns HTTP 409.

The row lock serializes booking attempts for the same event. It is a
correctness-first approach; a very popular event can become a contention
hotspot. Benchmark results should guide any future redesign.

### Background processing

The API enqueues jobs in Redis, and ARQ workers send emails using
Resend. Booking confirmations are sent to the customer who booked.
Event update notifications are sent to customers with bookings for
the updated event.

Email delivery requires a valid Resend API key and authorized sender.
Missing credentials cause the worker job to fail rather than pretending
an email was sent.

ARQ jobs may execute more than once. Production systems should add
durable delivery records, idempotency controls, monitoring, and a
transactional outbox so database commits and job enqueueing cannot
leave notification gaps.

### Event capacity updates

An organizer cannot reduce capacity below the number of tickets
already sold. Confirmed bookings prevent event deletion.

### Performance

Locust generates concurrent HTTP traffic. Baseline and optimized runs
must use the same event conditions, machine, test duration, and
workload profile. The baseline intentionally uses a small connection
pool; the optimized run uses a measured pool size.

The breaking point is defined by a documented latency or error-rate
objective, not by the number of virtual users alone. Inventory conflicts
are business outcomes and must be separated from infrastructure errors.

No benchmark results are claimed until the tests have actually run.
Results depend on hardware, hosting limits, database configuration,
network latency, and workload.

### Deployment

Docker packages the application. Docker Compose runs PostgreSQL, Redis,
the API, and the worker locally. For a public deployment, use a managed
PostgreSQL database and Redis-compatible service, separate API and
worker processes, HTTPS, and environment-based secrets.

## 4. Requirements

- Python 3.14
- uv
- Docker Engine and Docker Compose
- A Resend account and API key for real email delivery

## 5. Local setup

1. Copy .env.example to .env.
2. Generate a secure SECRET_KEY.
3. Set RESEND_API_KEY and EMAIL_FROM.
4. Run uv python install 3.14.
5. Run uv python pin 3.14.
6. Run uv lock and uv sync --dev.
7. Start services with docker compose up --build -d.
8. Open http://localhost:8000/docs.
9. Check http://localhost:8000/health.

Do not commit .env or production credentials.

## 6. API endpoints

| Method | Path | Access |
|---|---|---|
| POST | /auth/register | Public |
| POST | /auth/login | Public |
| GET | /users/me | Authenticated |
| GET | /events | Public |
| GET | /events/{event_id} | Public |
| POST | /events | Organizer |
| PATCH | /events/{event_id} | Event owner |
| DELETE | /events/{event_id} | Event owner |
| POST | /events/{event_id}/bookings | Customer |
| GET | /bookings/me | Customer |

Interactive API documentation is available at /docs.

## 7. Tests

Start PostgreSQL and Redis with Docker Compose, then run:

    uv run pytest -v
    uv run ruff check .
    uv run ruff format --check .

The automated tests use a fake queue for predictable assertions.
Perform a separate live email test to verify actual delivery.

## 8. Load testing

Create a future-dated event with enough capacity for the intended
workload. Record its ID and run:

    EVENT_ID=YOUR_EVENT_ID uv run locust -f loadtest/locustfile.py --host http://localhost:8000

Open http://localhost:8089.

Run controlled user levels and export the results. Record throughput,
p95 latency, failed requests, successful bookings, and final inventory.

Repeat with the optimized connection pool. Do not compare runs where
the event sells out, or where the hardware or workload differs.

Report the first level that violates the chosen service-level objective
and the highest level that satisfies it. Confirm that inventory never
exceeds capacity.

## 9. Production hardening

Before using this as a real ticketing service:

- Use Alembic migrations.
- Remove public self-service organizer role assignment.
- Use a transactional outbox for notification jobs.
- Add booking cancellation and inventory restoration transactions.
- Add rate limiting, structured logs, monitoring, and alerts.
- Add email delivery status tracking and idempotency.
- Configure CORS explicitly if a browser frontend is added.
- Restrict database network access and use managed secrets.
- Use a verified sending domain.
- Add pagination limits and query-performance monitoring.
- Test concurrent bookings against PostgreSQL, not SQLite.
- Configure backups and disaster recovery.
- Load test against staging before increasing production capacity.

## 10. Known limitations

This assignment implementation prioritizes clarity and correct
inventory handling. It is not a payment-processing system. It does
not implement refunds, ticket QR codes, distributed tracing, or a
durable transactional outbox.

The API commits a booking before enqueueing its confirmation job.
If Redis is unavailable at precisely that point, a confirmed booking
can exist without a queued email. The same general gap exists between
an event update and notification enqueueing. A transactional outbox
should replace this approach for production-grade delivery guarantees.

The first version also uses create_all during startup, public role
selection during registration, and a basic JWT scheme. These choices
are documented trade-offs, not claims of complete production security.

