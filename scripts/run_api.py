"""Start the local, single-worker read-only API in the Conda agent environment."""

import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")
sys.path.insert(0, str(PROJECT_DIR / "src"))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "react_agent.api:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        workers=1,
        proxy_headers=False,
        access_log=False,
        http="h11",
        limit_concurrency=8,
        timeout_keep_alive=5,
        h11_max_incomplete_event_size=16384,
    )
