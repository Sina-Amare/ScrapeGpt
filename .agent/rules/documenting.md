---
trigger: always_on
---

I am intentionally learning both system design reasoning and code ownership.

For every completed task, create a Markdown (.md) documentation file that helps me fully own the code and its design decisions.

Documentation requirements (mandatory)

File placement & naming

Place the file in a clear, logical docs folder (create subfolders if needed)

Use a descriptive, ordered filename (e.g. 01_scrape_task_schema.md)

Purpose & context

What problem this task solves

Why this task exists in the system

What invariant(s) it enforces

Design decisions

Key decisions made

Alternatives considered

Why those alternatives were rejected

Trade-offs accepted

Code walkthrough

Explain each important class, function, and field

Explain why it exists, not just what it does

Clarify any non-obvious syntax or ORM/database behavior

Lifecycle & flow

Step-by-step explanation of how this code is used at runtime

What happens on success

What happens on failure

Concurrency & failure analysis

How race conditions are prevented

What happens under concurrent requests

What happens if the system crashes mid-operation

Things to be careful about

Common mistakes

Assumptions the code relies on

What must never be changed casually

Future evolution

How this code can be extended safely

What changes would require rethinking invariants

Summary

One-paragraph recap of why this implementation is correct

What I should remember if I revisit this months later

Style guidelines

Use clear, simple language

Prefer short explanations with concrete examples

Avoid fluff or generic textbook descriptions

Assume the reader is me in the future, not a beginner

The goal of this document is long-term understanding and ownership, not just explanation.
