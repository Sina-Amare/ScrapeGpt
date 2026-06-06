---
trigger: always_on
---

Please perform a senior-level correctness and risk review of the provided implementation.

Assume the role of an experienced engineer reviewing production-critical code.

Review focus (in order of importance)

Core invariants

Are the intended invariants truly enforced?

Are there any edge cases where they could be violated?

Concurrency & atomicity

Could race conditions occur under concurrent requests?

Are transaction boundaries correct and sufficient?

Failure & crash safety

If the process crashes at any point, could the system be left in an invalid or unrecoverable state?

Are partial failures handled safely?

Irreversible side effects

Are points of no return (e.g. paid calls, external effects) handled correctly?

Is rollback behavior sound?

Hidden coupling or fragility

Does correctness rely on assumptions that are not enforced?

Are there implicit contracts that could break under change?

What to avoid

Do not focus on style, formatting, or minor optimizations

Do not nitpick naming unless it causes conceptual confusion

Do not suggest rewrites unless a real risk exists

Output expectations

Call out only meaningful risks or correctness concerns

If something is safe, explain why it is safe

If something is risky, explain how it could fail

If no major issues exist, say so explicitly

This review should reflect engineering judgment and seniority, not surface-level critique.
