# SaaS_repo_demo — Agent Execution Entrypoint

This project uses **`personal-ai-harness` (`mode: orchestrated`)** for all implementation, refactoring, debugging, and verification tasks.

- Read and obey [`.agents/AGENTS.md`](.agents/AGENTS.md) on every new conversation.
- Call `lookup_skill("personal-ai-harness")` and run `ai-harness sync --project-root . --platform antigravity` before starting work.
- **No inline application or test edits (`U-01`)**: Delegate implementation to specialist subagents and verify via independent `qa` and `reviewer`/`security` subagents (`U-04`).
