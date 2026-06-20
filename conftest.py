import asyncio
import os
import sys
from dotenv import load_dotenv

# asyncpg on Windows fails with ProactorEventLoop (the default in Python 3.8+).
# Switch to SelectorEventLoop so asyncpg can establish connections correctly.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Must be set before any src.* import so that lru_cache on get_settings()
# caches TestingSettings (pointing to clients_test DB) instead of the
# dev/prod settings.
os.environ["API_MODE"] = "test"

# src/config.py loads .dev.env first (override=False), which writes DB_USER="",
# DB_NAME="clients" etc. into os.environ. Since pydantic-settings reads from
# os.environ before env_file, those empty/wrong values would win.
# Loading .tests.env here with override=True ensures the test DB credentials
# are in os.environ before any src.* import runs.
_tests_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", ".tests.env")
load_dotenv(_tests_env, override=True)
