from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.services.bookings import DomainError

app = FastAPI(title="Clinic booking API", version="1.0")
app.include_router(router)


@app.exception_handler(DomainError)
async def domain_error_handler(_, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})
