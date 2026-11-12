from coaudit.api.app import create_app
from coaudit.bootstrap import build_default_service

app = create_app(build_default_service())
