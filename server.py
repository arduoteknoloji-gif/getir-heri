from fastapi import FastAPI, APIRouter, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Literal
import logging

app = FastAPI(title="Getir-Heri API")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StatusUpdate(BaseModel):
    status: Literal["available", "busy", "offline"]

async def get_current_user(request: Request):
    return {
        "_id": "69e0d576d761769e31705134",
        "role": "courier",
        "name": "Test Kurye"
    }

# ====================== AUTH ======================
@api_router.post("/auth/login")
async def login():
    return {
        "_id": "69e0d576d761769e31705134",
        "role": "courier",
        "name": "Test Kurye"
    }

@api_router.get("/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    return user

# ====================== STATUS (DÜZELTİLDİ) ======================
@api_router.patch("/couriers/{courier_id}/status")
async def update_courier_status(
    courier_id: str,
    status_update: StatusUpdate,
    user: dict = Depends(get_current_user)
):
    logger.info(f"Status update: {courier_id} -> {status_update.status}")
    return {"message": f"Status updated to {status_update.status}", "status": status_update.status}

# ====================== EARNINGS ======================
@api_router.get("/couriers/{courier_id}/earnings")
async def get_courier_earnings(courier_id: str, user: dict = Depends(get_current_user)):
    return {
        "total_earnings": 1250,
        "total_deliveries": 12,
        "average_per_delivery": 104.17
    }

# ====================== ORDERS ======================
@api_router.get("/orders")
async def get_orders(user: dict = Depends(get_current_user)):
    return []

app.include_router(api_router)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)