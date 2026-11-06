"""``/api/v1/activity`` — Canlı Durum (Wave 5 contract §6, Addendum B), mounted here: the
internet line's speed (``GET /activity/wan``, :mod:`arcilogger_api.activity.wan`, W5-N). W5-M
adds the devices' route to this router.
"""

from fastapi import APIRouter

from arcilogger_api.activity import wan

router = APIRouter(prefix="/activity", tags=["activity"])
router.add_api_route("/wan", wan.get_wan, methods=["GET"])
