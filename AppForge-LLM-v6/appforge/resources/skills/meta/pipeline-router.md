# Pipeline router

Classify the request by the deliverable and repository state, not by a favorite stack.

- `web-app`: browser-first greenfield product.
- `fullstack-saas`: persistent multi-user product with authentication, tenancy, billing, or operational concerns.
- `api-service`: backend HTTP, GraphQL, event, or microservice surface.
- `cli-tool`: command-line program or developer utility.
- `desktop-app`: Electron, Tauri, native desktop, or cross-platform desktop product.
- `mobile-app`: Android, iOS, Flutter, or React Native product.
- `data-app`: dashboard, ETL, analytics, or data transformation product.
- `automation`: scheduled workflow, integration, bot, or event-driven automation.
- `library-sdk`: reusable package, library, or SDK.
- `prototype`: deliberately time-boxed proof of concept or MVP slice.
- `feature`: new behavior in an existing repository.
- `bugfix`: a reported defect that must be reproduced and regression-tested.

When two pipelines match, choose the one with the more specific irreversible concerns. For example, a subscription dashboard is `fullstack-saas`, not merely `web-app`; a feature request in an existing SaaS repository is `feature` because preserving the repository is the dominant constraint.
