# Rehearsal dashboard

Single-file dashboard: `index.html` (plain HTML/CSS/vanilla JS, no build step).
Open it directly in a browser, or serve the folder with any static server.

## Configurable endpoints

At the top of the `<script>` block in `index.html`:

- `STATUS_URL` — default `http://localhost:9000/status.json` (polled every ~1s)
- `ATTACK_URL` — default `http://localhost:8099/attack` (POST `{variant: "a"|"b"}`)
- `APPROVE_URL` — default `http://localhost:8099/approve` (POST, enabled once phase is `rehearsed`)

Once the real orchestrator is up, update these three constants to point at it.
The status JSON shape is unchanged from `shared/mock_status_server.py`.
