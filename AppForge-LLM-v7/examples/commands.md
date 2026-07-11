# Example input commands

## Web application

```bash
appforge forge "Build a Korean/English habit tracker PWA with offline support, recurring reminders, export, accessibility, tests, and Docker" --allow-network
```

## SaaS

```bash
appforge forge "Create a multi-tenant appointment SaaS with organization roles, Stripe-ready billing boundaries, audit logs, PostgreSQL, API tests, and deployment manifests" --pipeline fullstack-saas --allow-network
```

## API

```bash
appforge forge "Build a FastAPI service that ingests webhook events idempotently, validates signatures, retries jobs, exposes health metrics, and includes integration tests" --pipeline api-service --allow-network
```

## Existing feature

```bash
appforge forge "Add searchable, paginated audit history with CSV export and permission checks" --target . --pipeline feature --allow-network
```

## Bugfix

```bash
appforge forge "Fix the intermittent duplicate-order bug under concurrent checkout and add a regression test" --target . --pipeline bugfix
```

## Guided approvals

```bash
appforge forge "Build a desktop research notebook" --mode guided --pause-for-approval
appforge status projects/build-a-desktop-research-notebook
appforge approve projects/build-a-desktop-research-notebook
appforge run projects/build-a-desktop-research-notebook
```
