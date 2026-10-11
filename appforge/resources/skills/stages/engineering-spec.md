# Engineering Spec Stage

Collapse the small-app planning work into one implementation-ready engineering spec. Prefer concrete defaults over follow-up questions unless a safety or feasibility blocker exists.

Produce `engineering_spec` with:

- Traceable requirements with acceptance criteria.
- A small screen/surface inventory including loading, empty, error, and success states where relevant.
- State/data ownership and lifecycle decisions.
- A pragmatic implementation plan sized for a lightweight web app.
- Verification steps that can be run by the implementation and verification stages.

Keep the spec short enough to guide code immediately. Do not invent enterprise architecture for a trivial or small request.

For `web-app-lite`, declare `verification_profile` as `static-web` (local HTML/CSS/JS without a build), `javascript-web` (production bundle), or `typescript-web` (bundle plus typecheck). Specify `browser_checks` before implementation: one case for every acceptance criterion, with IDs such as `FR-001/AC-01`, explicit local CSS selectors, actions, and assertions. Use the artifact schema for supported actions. A counter criterion should click its button and assert the changed number; page visibility alone does not prove the interaction. Include relevant failure/empty states as criteria.

These checks are frozen when this stage completes. Later stages must fix the app, not weaken assertions, delete requirements, or downgrade the profile. The current host harness serves local static files/bundles without external network or a backend. Do not claim that it verifies API, SSR, authentication-server or external-service behavior; preserve such requirements and state the unsupported validation boundary explicitly.
