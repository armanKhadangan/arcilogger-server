"""``/api/v1/activity`` — Canlı Durum (Wave 5 contract §6.2 M2 and §6 Addendum B N6), mounted
here: who sends and receives now (``GET /activity/devices``, :mod:`arcilogger_api.activity.routes`,
W5-M; module ``usage_analytics`` on that route only) and the internet line's speed
(``GET /activity/wan``, :mod:`arcilogger_api.activity.wan`, W5-N; no module gate).
"""

from arcilogger_api.activity import wan
from arcilogger_api.activity.routes import router as router

router.add_api_route("/wan", wan.get_wan, methods=["GET"])
