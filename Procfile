# Single worker, threaded — required by the 512MB guardrails (§3.1):
# the Discord bot runs its own asyncio loop in a daemon thread inside this
# process, so exactly one worker must serve the app.
web: gunicorn -w 1 --threads 4 -k gthread app:app
