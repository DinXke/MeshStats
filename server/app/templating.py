import time
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Cache busting: changes on every (re)start, and therefore on every deploy
templates.env.globals["asset_v"] = str(int(time.time()))
