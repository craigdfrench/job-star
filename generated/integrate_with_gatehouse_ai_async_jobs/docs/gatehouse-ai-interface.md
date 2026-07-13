# Gatehouse-AI Async Job Interface

> **Status**: Initial documentation — requires verification against actual gatehouse-ai codebase.
> Sections marked with ⚠️ contain assumptions that need confirmation.
> **Last Updated**: 2025-01-24

## Overview

Gatehouse-AI provides an asynchronous job execution system that Job-Star will integrate with as its execution backend. Job-Star acts as the intelligent client layer that decides **what** to execute, **when**, and **how**, while gatehouse-ai handles the actual job scheduling, execution, and result delivery.

---

## 1. Integration Type

⚠️ **Assumption**: Gatehouse-AI exposes an HTTP REST API for job submission and status polling, with optional webhook callbacks for completion notifications. This is the most common pattern for async job systems and allows for language-agnostic integration.

**To verify**:
- [ ] Confirm whether gatehouse-ai is HTTP REST, gRPC, queue-based (e.g., Celery, Bull, Sidekiq), or library-level
- [ ] If queue-based, identify the message broker (Redis, RabbitMQ, SQS, etc.)
- [ ] If library-level, identify the language/runtime and whether a Python SDK exists
- [ ] Check for OpenAPI/Swagger spec or protobuf definitions in the repo

**Integration approach for Job-Star**: HTTP REST client (preferred for decoupling) unless gatehouse-ai is library-level only, in which case we wrap it in a thin service adapter.

---

## 2. Authentication

⚠️ **Assumption**: API key-based authentication via `Authorization` header.

### Request Authentication
