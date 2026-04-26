# Change Log

Changes are appended by date. Each entry covers one session or logical chunk of work.

---

## 2026-04-25

- Created this `CHANGES.md` file as a session change log.
- Updated `NGROK_SETUP.md` to reference `start.sh` as the preferred way to start the stack; manual steps kept as fallback.
- Updated `CLAUDE.md` Commands section to document `start.sh`.

## Previous session (date unknown)

- Created `start.sh` — single script to start backend + frontend + ngrok together. Accepts `prod` (default, ports 8000/5173) or `staging` (ports 8001/5174, sets `APP_ENV=staging` and Vite `--mode staging`). Replaces the need to start each process separately.
- Established staging environment convention: separate ports for prod vs. staging so both can run simultaneously on the same machine.

