"""Live, real-URL end-to-end harnesses for the scraping pipeline.

These modules hit the network on purpose and are NOT part of the pytest suite.
Run them manually:

    venv\\Scripts\\python.exe -m tests.e2e.run_live_pipeline      # Layer A (services)
    venv\\Scripts\\python.exe -m tests.e2e.run_http_api_e2e       # Layer B (HTTP API)
"""
