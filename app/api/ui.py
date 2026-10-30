"""Public patient BFF: share contract handlers, never expose the private API key.

Only public availability and booking creation are intentionally exposed. History,
lookup and cancellation require a separate patient-ownership design before any
public routes can safely be added.
"""

from fastapi import APIRouter, Depends

from app.api.routes import book_slot, list_slots, rate_limit_booking_attempt
from app.schemas.bookings import BookingResponse
from app.schemas.slots import SlotsResponse

router = APIRouter(prefix="/ui-api")
router.add_api_route(
    "/slots", list_slots, methods=["GET"], response_model=SlotsResponse
)
router.add_api_route(
    "/bookings",
    book_slot,
    methods=["POST"],
    response_model=BookingResponse,
    status_code=201,
    dependencies=[Depends(rate_limit_booking_attempt)],
)
