import time
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Cache-busting: verandert bij elke (her)start, dus ook bij elke deploy
templates.env.globals["asset_v"] = str(int(time.time()))
