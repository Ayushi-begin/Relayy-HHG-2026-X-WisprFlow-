# Relayy delivery documentation

Prepared on 30 October 2026 (IST), before product changes.

## Reading order

1. [Current progress and requirements](current-state-and-gaps.md): source evidence, delivery boundaries, gaps, and requirement traceability.
2. [Architecture and implementation design](architecture-and-plan.md): HLD, LLD class diagram, target design, and implementation acceptance gates.
3. [Happy paths, user journey, and edge cases](flows-and-edge-cases.md): current and planned behavior with diagrams and checks.
4. [Provider setup and recovery](delivery-setup.md): implemented configuration, fixture binding, safety boundaries, and verification commands.

The README and inspected code form the requirements baseline. The user selected a hackathon demonstration with real Resend email and Stripe sandbox/test-mode refunds. Production deployment is not the delivery target.

## Evidence rules

- **Existing** means code implements the path. It does not mean a runtime test passed.
- **Gap** means the inspected code does not meet the stated requirement or selected delivery target.
- **Proposed** means a design awaits approval or implementation.
- No runtime tests or external-provider calls support this initial assessment.
- Do not report a completion percentage: the repository has no complete product specification or acceptance checklist.

## Delivery boundary

Keep the current FastAPI, SQLModel, SQLite, React, and SSE foundation for one demo process. Use real provider APIs only within the selected test boundary. Never send to fictional seed addresses. Never issue live-mode refunds.

Credentials, a permitted sender, approved test recipients, and verified Stripe test-payment mappings are external prerequisites. Missing prerequisites must block the affected provider flow, not produce a false success.

The approved architecture governs implementation. The assessment and diagrams preserve the pre-implementation baseline; provider setup describes the implemented runtime. Offline checks and real-provider checks remain separate evidence categories.
