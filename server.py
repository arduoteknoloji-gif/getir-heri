from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timezone
import os
import logging

from fastapi import FastAPI, APIRouter, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel
from typing import Literal

# ====================== CONFIG ======================
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get('MONGO_URL')
db_name = os.environ.get('DB_NAME')

client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

app = FastAPI(title="Getir-Heri API")
api_router = APIRouter(prefix="/api")

# ====================== CORS (EN ÖNEMLİ) ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Test için tüm origin'lere izin ver
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================== MODELS ======================
class StatusUpdate(BaseModel):
    status: Literal["available", "busy", "offline"]

# ====================== DEPENDENCIES ======================
async def get_current_user(request: Request):
    # Basit token kontrolü (gerçek projede jwt decode yap)
    token = request.cookies.get("access_token") or request.headers.get("Authorization")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Basit user döndür (gerçekte jwt'den al)
    return {"_id": "69e0d576d761769e31705134", "role": "courier"}  # Test için sabit

# ====================== COURIER STATUS ======================
@api_router.patch("/couriers/{courier_id}/status")
async def update_courier_status(courier_id: str, status_update: StatusUpdate, user: dict = Depends(get_current_user)):
    print(f"Status update request: {courier_id} -> {status_update.status}")
    return {"message": f"Status updated to {status_update.status}"}

# ====================== EARNINGS ======================
@api_router.get("/couriers/{courier_id}/earnings")
async def get_courier_earnings(courier_id: str, user: dict = Depends(get_current_user)):
    return {
        "total_earnings": 1250.0,
        "total_deliveries": 8,
        "average_per_delivery": 156.25,
    }

# ====================== ORDERS ======================
@api_router.get("/orders")
async def get_orders(user: dict = Depends(get_current_user)):
    return [
        {
            "id": "order1",
            "restaurant_name": "Test Restoran",
            "customer_name": "Ahmet Yılmaz",
            "customer_address": "İstanbul",
            "status": "pending",
            "delivery_fee": 35.0
        }
    ]

# ====================== APP ======================
app.include_router(api_router)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)