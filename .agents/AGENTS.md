# SaaS_repo_demo — Autonomous Harness Execution Rules

This repository is permanently configured to execute engineering work through **`personal-ai-harness` (`mode: orchestrated`)** rather than inline edits by the primary agent.

## Mandatory Instructions for Every New Conversation

1. **Activate the `personal-ai-harness` Skill Immediately:**
   - Before planning or modifying any code in this repository, call `lookup_skill` with `"personal-ai-harness"` (and read `../personal-ai-harness/definitions/agents/core/orchestrator.md` if needed).
2. **Sync Harness Workspace State on Startup:**
   - Run:
     ```bash
     ../personal-ai-harness/.venv/bin/ai-harness sync --project-root . --platform antigravity || ../personal-ai-harness/bin/ai-harness.sh sync --project-root . --platform antigravity
     ```
   - Ensure `.harness/project.yaml` reflects this repo's real source/test paths and validation commands (see `.agents/harness-project.yaml`).
3. **Hard Gate (`U-01`) — Never Edit Application or Test Code Inline:**
   - The primary agent acts **strictly as the Orchestrator**.
   - **Do NOT** edit `backend/`, `frontend/`, `docker-compose.yml`, or `render.yaml` directly from the primary agent.
   - Delegate all changes through `.harness/artifacts/tasks/<TASK-ID>.md` task contracts using `define_subagent` + `invoke_subagent` with distinct specialist roles (`harness-python-backend`, `harness-react-frontend`, `harness-database-specialist`, `harness-qa`, `harness-security`, `harness-reviewer`).
4. **Independent Verification (`U-04`):**
   - Enforce `implementer != qa != reviewer` across all iterations (`max_rework_iterations: 2`).
   - Run real validation commands before reporting completion:
     - Backend: `backend/.venv/bin/python -m pytest -q` and `backend/.venv/bin/ruff check app/ tests/`
     - Frontend: `npm run typecheck`, `npm test`, `npm run build` (in `frontend/`)
5. **Human Approval Gate (`U-05`):**
   - Leave working tree changes uncommitted unless explicitly authorized to commit/push.
