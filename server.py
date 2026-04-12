from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import List, Optional, Literal, Dict
from datetime import datetime, timezone, timedelta
import os
import logging
import bcrypt
import jwt
import json
import asyncio

# ============================================================
# MongoDB Atlas Connection (Optimize edildi)
# ============================================================
# MongoDB Atlas Connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(
    mongo_url,
    maxPoolSize=50,
    minPoolSize=10,
    maxIdleTimeMS=45000,
    serverSelectionTimeoutMS=5000
)
db = client[os.environ['DB_NAME']]

# Create the main app
app = FastAPI(
    title="Getir-Heri API",
    version="1.0.0",
    docs_url="/api/docs" if os.environ.get("ENV") != "production" else None
)

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

JWT_ALGORITHM = "HS256"

# ============================================================
# WebSocket Connection Manager (Push Bildirimleri)
# ============================================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str, role: str):
        await websocket.accept()
        key = f"{role}:{user_id}"
        if key not in self.active_connections:
            self.active_connections[key] = []
        self.active_connections[key].append(websocket)
        logger.info(f"WebSocket connected: {role}:{user_id}")

    def disconnect(self, websocket: WebSocket, user_id: str, role: str):
        key = f"{role}:{user_id}"
        if key in self.active_connections:
            self.active_connections[key] = [
                ws for ws in self.active_connections[key] if ws != websocket
            ]
            if not self.active_connections[key]:
                del self.active_connections[key]
        logger.info(f"WebSocket disconnected: {role}:{user_id}")

    async def send_to_user(self, user_id: str, role: str, message: dict):
        key = f"{role}:{user_id}"
        if key in self.active_connections:
            dead = []
            for ws in self.active_connections[key]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.active_connections[key].remove(ws)

    async def send_to_role(self, role: str, message: dict):
        keys_to_send = [k for k in self.active_connections if k.startswith(f"{role}:")]
        for key in keys_to_send:
            dead = []
            for ws in self.active_connections[key]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.active_connections[key].remove(ws)

    async def broadcast(self, message: dict):
        for key in list(self.active_connections.keys()):
            dead = []
            for ws in self.active_connections[key]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.active_connections[key].remove(ws)

manager = ConnectionManager()

# ============================================================
# Helper Functions
# ============================================================
def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        "type": "access"
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "refresh"
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user["_id"] = str(user["_id"])
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============================================================
# Pydantic Models
# ============================================================
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: Literal["courier", "restaurant", "admin"]
    phone: Optional[str] = None
    restaurant_name: Optional[str] = None
    
    @field_validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('Password must be at least 6 characters')
        return v

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class OrderCreate(BaseModel):
    restaurant_id: str
    customer_name: str
    customer_phone: str
    customer_address: str
    customer_lat: float
    customer_lng: float
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    items: List[dict]
    total_amount: float
    delivery_fee: float
    notes: Optional[str] = None

class OrderUpdate(BaseModel):
    status: Optional[Literal["pending", "assigned", "picked_up", "in_transit", "delivered", "cancelled"]] = None
    courier_lat: Optional[float] = None
    courier_lng: Optional[float] = None

class LocationUpdate(BaseModel):
    lat: float
    lng: float

class RatingCreate(BaseModel):
    order_id: str
    rated_user_id: str
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = None

class PromoCodeCreate(BaseModel):
    code: str
    discount_type: Literal["percentage", "fixed"]
    discount_value: float
    min_order_amount: float = 0
    max_uses: int = 100
    expires_at: Optional[str] = None

class PromoCodeApply(BaseModel):
    code: str
    order_amount: float

# ============================================================
# Auth Routes
# ============================================================
@api_router.post("/auth/register")
async def register(req: RegisterRequest, response: Response):
    email_lower = req.email.lower()
    
    existing = await db.users.find_one({"email": email_lower})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user_doc = {
        "email": email_lower,
        "password_hash": hash_password(req.password),
        "name": req.name,
        "role": req.role,
        "phone": req.phone,
        "rating_avg": 0,
        "rating_count": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    if req.role == "restaurant" and req.restaurant_name:
        restaurant_doc = {
            "name": req.restaurant_name,
            "owner_email": email_lower,
            "address": "",
            "phone": req.phone or "",
            "rating_avg": 0,
            "rating_count": 0,
            "created_at": datetime.now(timezone.utc)
        }
        restaurant_result = await db.restaurants.insert_one(restaurant_doc)
        user_doc["restaurant_id"] = str(restaurant_result.inserted_id)
        user_doc["restaurant_name"] = req.restaurant_name
    
    result = await db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)
    
    access_token = create_access_token(user_id, email_lower)
    refresh_token = create_refresh_token(user_id)
    
    response.set_cookie(
        key="access_token", value=access_token, httponly=True,
        secure=False, samesite="lax", max_age=900, path="/"
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token, httponly=True,
        secure=False, samesite="lax", max_age=604800, path="/"
    )
    
    user_doc["_id"] = user_id
    user_doc.pop("password_hash")
    return user_doc

@api_router.post("/auth/login")
async def login(req: LoginRequest, response: Response, request: Request):
    email_lower = req.email.lower()
    
    identifier = f"{request.client.host}:{email_lower}"
    attempt = await db.login_attempts.find_one({"identifier": identifier})
    
    if attempt and attempt.get("count", 0) >= 5:
        lockout_until = attempt.get("lockout_until")
        if lockout_until and lockout_until > datetime.now(timezone.utc):
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    
    user = await db.users.find_one({"email": email_lower})
    if not user or not verify_password(req.password, user["password_hash"]):
        await db.login_attempts.update_one(
            {"identifier": identifier},
            {
                "$inc": {"count": 1},
                "$set": {
                    "lockout_until": datetime.now(timezone.utc) + timedelta(minutes=15)
                }
            },
            upsert=True
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    await db.login_attempts.delete_one({"identifier": identifier})
    
    user_id = str(user["_id"])
    access_token = create_access_token(user_id, email_lower)
    refresh_token = create_refresh_token(user_id)
    
    response.set_cookie(
        key="access_token", value=access_token, httponly=True,
        secure=False, samesite="lax", max_age=900, path="/"
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token, httponly=True,
        secure=False, samesite="lax", max_age=604800, path="/"
    )
    
    user["_id"] = user_id
    user.pop("password_hash")
    return user

@api_router.post("/auth/logout")
async def logout(response: Response, user: dict = Depends(get_current_user)):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"message": "Logged out successfully"}

@api_router.get("/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    return user

@api_router.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="Refresh token not found")
    
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        
        user_id = str(user["_id"])
        access_token = create_access_token(user_id, user["email"])
        
        response.set_cookie(
            key="access_token", value=access_token, httponly=True,
            secure=False, samesite="lax", max_age=900, path="/"
        )
        
        return {"message": "Token refreshed"}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

# ============================================================
# Order Routes (Zaman Damgalari Eklendi)
# ============================================================
@api_router.post("/orders")
async def create_order(order: OrderCreate, user: dict = Depends(get_current_user)):
    if user["role"] not in ["restaurant", "admin"]:
        raise HTTPException(status_code=403, detail="Only restaurants can create orders")
    
    restaurant = await db.restaurants.find_one({"_id": ObjectId(order.restaurant_id)})
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    
    now = datetime.now(timezone.utc)
    
    order_doc = {
        **order.model_dump(),
        "restaurant_name": restaurant.get("name", ""),
        "status": "pending",
        "courier_id": None,
        "courier_name": None,
        "courier_lat": None,
        "courier_lng": None,
        # Zaman Damgalari
        "timestamps": {
            "created_at": now.isoformat(),
            "assigned_at": None,
            "picked_up_at": None,
            "in_transit_at": None,
            "delivered_at": None,
            "cancelled_at": None
        },
        "delivery_duration_minutes": None,
        "rating": None,
        "delivery_photo_url": None,
        "promo_code": None,
        "discount_amount": 0,
        "created_at": now,
        "updated_at": now
    }
    
    result = await db.orders.insert_one(order_doc)
    order_doc["id"] = str(result.inserted_id)
    order_doc.pop("_id", None)
    
    # Push Bildirimi: Tum kuryelere yeni siparis bildirimi
    await manager.send_to_role("courier", {
        "type": "new_order",
        "title": "Yeni Siparis!",
        "message": f"{restaurant.get('name', '')} - {order.customer_address}",
        "order_id": order_doc["id"],
        "delivery_fee": order.delivery_fee
    })
    
    # Admin'e bildirim
    await manager.send_to_role("admin", {
        "type": "new_order",
        "title": "Yeni Siparis Olusturuldu",
        "message": f"{restaurant.get('name', '')} -> {order.customer_name}",
        "order_id": order_doc["id"]
    })
    
    return order_doc

@api_router.get("/orders")
async def get_orders(
    status: Optional[str] = None,
    user: dict = Depends(get_current_user)
):
    query = {}
    
    if user["role"] == "courier":
        query["$or"] = [
            {"courier_id": user["_id"]},
            {"status": "pending"}
        ]
    elif user["role"] == "restaurant":
        query["restaurant_id"] = user.get("restaurant_id", "")
    
    if status:
        query["status"] = status
    
    orders = await db.orders.find(query).sort("created_at", -1).to_list(1000)
    
    for order in orders:
        if "_id" in order:
            order["id"] = str(order["_id"])
            del order["_id"]
    
    return orders

@api_router.get("/orders/{order_id}")
async def get_order(order_id: str, user: dict = Depends(get_current_user)):
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    order["id"] = str(order["_id"])
    order.pop("_id", None)
    return order

@api_router.patch("/orders/{order_id}")
async def update_order(
    order_id: str,
    update: OrderUpdate,
    user: dict = Depends(get_current_user)
):
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    now = datetime.now(timezone.utc)
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    update_data["updated_at"] = now
    
    # Zaman Damgasi Guncelleme
    new_status = update_data.get("status")
    if new_status:
        timestamp_field = f"timestamps.{new_status}_at"
        update_data[timestamp_field] = now.isoformat()
        
        # Teslimat suresi hesapla
        if new_status == "delivered":
            timestamps = order.get("timestamps", {})
            created_str = timestamps.get("created_at")
            if created_str:
                try:
                    created_time = datetime.fromisoformat(created_str)
                    duration = (now - created_time).total_seconds() / 60
                    update_data["delivery_duration_minutes"] = round(duration, 1)
                except Exception:
                    pass
    
    await db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": update_data})
    
    updated_order = await db.orders.find_one({"_id": ObjectId(order_id)})
    updated_order["id"] = str(updated_order["_id"])
    updated_order.pop("_id", None)
    
    # Push Bildirimleri
    if new_status:
        status_labels = {
            "assigned": "Kurye Atandi",
            "picked_up": "Siparis Alindi",
            "in_transit": "Kurye Yolda",
            "delivered": "Teslim Edildi",
            "cancelled": "Siparis Iptal Edildi"
        }
        label = status_labels.get(new_status, new_status)
        
        # Restorana bildirim
        restaurant_id = order.get("restaurant_id")
        if restaurant_id:
            restaurant_user = await db.users.find_one({"restaurant_id": restaurant_id})
            if restaurant_user:
                await manager.send_to_user(str(restaurant_user["_id"]), "restaurant", {
                    "type": "order_status_update",
                    "title": f"Siparis Durumu: {label}",
                    "message": f"#{order_id[:8]} - {order.get('customer_name', '')}",
                    "order_id": order_id,
                    "status": new_status
                })
        
        # Kurye'ye bildirim
        courier_id = order.get("courier_id")
        if courier_id:
            await manager.send_to_user(courier_id, "courier", {
                "type": "order_status_update",
                "title": f"Siparis Durumu: {label}",
                "message": f"#{order_id[:8]}",
                "order_id": order_id,
                "status": new_status
            })
        
        # Admin'e bildirim
        await manager.send_to_role("admin", {
            "type": "order_status_update",
            "title": f"Siparis Durumu: {label}",
            "message": f"#{order_id[:8]} - {order.get('customer_name', '')}",
            "order_id": order_id,
            "status": new_status
        })
    
    return updated_order

@api_router.post("/orders/{order_id}/assign")
async def assign_courier(
    order_id: str,
    courier_id: str,
    user: dict = Depends(get_current_user)
):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can assign couriers")
    
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    courier = await db.users.find_one({"_id": ObjectId(courier_id), "role": "courier"})
    if not courier:
        raise HTTPException(status_code=404, detail="Courier not found")
    
    now = datetime.now(timezone.utc)
    
    await db.orders.update_one(
        {"_id": ObjectId(order_id)},
        {
            "$set": {
                "courier_id": courier_id,
                "courier_name": courier.get("name", ""),
                "status": "assigned",
                "timestamps.assigned_at": now.isoformat(),
                "updated_at": now
            }
        }
    )
    
    updated_order = await db.orders.find_one({"_id": ObjectId(order_id)})
    updated_order["id"] = str(updated_order["_id"])
    updated_order.pop("_id", None)
    
    # Kurye'ye bildirim
    await manager.send_to_user(courier_id, "courier", {
        "type": "order_assigned",
        "title": "Yeni Siparis Atandi!",
        "message": f"{order.get('restaurant_name', '')} -> {order.get('customer_address', '')}",
        "order_id": order_id
    })
    
    return updated_order

@api_router.post("/orders/{order_id}/accept")
async def accept_order(order_id: str, user: dict = Depends(get_current_user)):
    if user["role"] != "courier":
        raise HTTPException(status_code=403, detail="Only couriers can accept orders")
    
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if order["status"] != "pending":
        raise HTTPException(status_code=400, detail="Order already assigned")
    
    now = datetime.now(timezone.utc)
    
    await db.orders.update_one(
        {"_id": ObjectId(order_id)},
        {
            "$set": {
                "courier_id": user["_id"],
                "courier_name": user.get("name", ""),
                "status": "assigned",
                "timestamps.assigned_at": now.isoformat(),
                "updated_at": now
            }
        }
    )
    
    updated_order = await db.orders.find_one({"_id": ObjectId(order_id)})
    updated_order["id"] = str(updated_order["_id"])
    updated_order.pop("_id", None)
    
    # Restorana bildirim
    restaurant_id = order.get("restaurant_id")
    if restaurant_id:
        restaurant_user = await db.users.find_one({"restaurant_id": restaurant_id})
        if restaurant_user:
            await manager.send_to_user(str(restaurant_user["_id"]), "restaurant", {
                "type": "order_accepted",
                "title": "Siparis Kabul Edildi!",
                "message": f"Kurye: {user.get('name', '')} - #{order_id[:8]}",
                "order_id": order_id,
                "courier_name": user.get("name", "")
            })
    
    # Admin'e bildirim
    await manager.send_to_role("admin", {
        "type": "order_accepted",
        "title": "Siparis Kabul Edildi",
        "message": f"Kurye: {user.get('name', '')} - #{order_id[:8]}",
        "order_id": order_id
    })
    
    return updated_order

# ============================================================
# Courier Routes (Canli Takip Iyilestirildi)
# ============================================================
@api_router.get("/couriers")
async def get_couriers(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view all couriers")
    
    couriers = []
    cursor = db.users.find({"role": "courier"}, {"password_hash": 0})
    async for courier in cursor:
        courier["_id"] = str(courier["_id"])
        active_count = await db.orders.count_documents({
            "courier_id": courier["_id"],
            "status": {"$in": ["assigned", "picked_up", "in_transit"]}
        })
        courier["active_orders"] = active_count
        couriers.append(courier)
    
    return couriers

@api_router.patch("/couriers/{courier_id}/location")
async def update_courier_location(
    courier_id: str,
    location: LocationUpdate,
    user: dict = Depends(get_current_user)
):
    if user["role"] != "courier" or user["_id"] != courier_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    now = datetime.now(timezone.utc)
    
    await db.users.update_one(
        {"_id": ObjectId(courier_id)},
        {
            "$set": {
                "current_lat": location.lat,
                "current_lng": location.lng,
                "last_location_update": now
            }
        }
    )
    
    await db.orders.update_many(
        {
            "courier_id": courier_id,
            "status": {"$in": ["assigned", "picked_up", "in_transit"]}
        },
        {
            "$set": {
                "courier_lat": location.lat,
                "courier_lng": location.lng,
                "updated_at": now
            }
        }
    )
    
    # Kurye konumunu ilgili restoranlara ve musteriye gercek zamanli gonder
    active_orders = await db.orders.find({
        "courier_id": courier_id,
        "status": {"$in": ["assigned", "picked_up", "in_transit"]}
    }).to_list(100)
    
    for order in active_orders:
        order_id = str(order["_id"])
        
        # Restorana bildirim
        restaurant_id = order.get("restaurant_id")
        if restaurant_id:
            restaurant_user = await db.users.find_one({"restaurant_id": restaurant_id})
            if restaurant_user:
                await manager.send_to_user(str(restaurant_user["_id"]), "restaurant", {
                    "type": "courier_location_update",
                    "order_id": order_id,
                    "lat": location.lat,
                    "lng": location.lng,
                    "courier_name": user.get("name", ""),
                    "timestamp": now.isoformat()
                })
        
        # Admin'e bildirim
        await manager.send_to_role("admin", {
            "type": "courier_location_update",
            "order_id": order_id,
            "courier_id": courier_id,
            "lat": location.lat,
            "lng": location.lng,
            "timestamp": now.isoformat()
        })
    
    return {
        "message": "Location updated",
        "timestamp": now.isoformat(),
        "active_orders": len(active_orders)
    }

@api_router.get("/couriers/{courier_id}/earnings")
async def get_courier_earnings(courier_id: str, user: dict = Depends(get_current_user)):
    if user["role"] != "courier" or user["_id"] != courier_id:
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Unauthorized")
    
    completed_orders = await db.orders.find({
        "courier_id": courier_id,
        "status": "delivered"
    }).to_list(1000)
    
    total_earnings = sum(order.get("delivery_fee", 0) for order in completed_orders)
    total_deliveries = len(completed_orders)
    
    # Gunluk kazanc
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_orders = [o for o in completed_orders if o.get("created_at") and o["created_at"] >= today]
    today_earnings = sum(o.get("delivery_fee", 0) for o in today_orders)
    
    # Ortalama teslimat suresi
    durations = [o.get("delivery_duration_minutes", 0) for o in completed_orders if o.get("delivery_duration_minutes")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    
    return {
        "total_earnings": total_earnings,
        "total_deliveries": total_deliveries,
        "average_per_delivery": total_earnings / total_deliveries if total_deliveries > 0 else 0,
        "today_earnings": today_earnings,
        "today_deliveries": len(today_orders),
        "average_delivery_duration": round(avg_duration, 1)
    }

# ============================================================
# Restaurant Routes
# ============================================================
@api_router.get("/restaurants")
async def get_restaurants(user: dict = Depends(get_current_user)):
    restaurants = []
    cursor = db.restaurants.find({})
    async for r in cursor:
        r["id"] = str(r["_id"])
        r.pop("_id", None)
        restaurants.append(r)
    return restaurants

@api_router.get("/restaurants/{restaurant_id}/analytics")
async def get_restaurant_analytics(restaurant_id: str, user: dict = Depends(get_current_user)):
    if user["role"] == "restaurant" and user.get("restaurant_id") != restaurant_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    orders = await db.orders.find({"restaurant_id": restaurant_id}).to_list(1000)
    
    total_orders = len(orders)
    completed_orders = [o for o in orders if o["status"] == "delivered"]
    total_revenue = sum(o.get("total_amount", 0) for o in completed_orders)
    
    # Ortalama teslimat suresi
    durations = [o.get("delivery_duration_minutes", 0) for o in completed_orders if o.get("delivery_duration_minutes")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    
    return {
        "total_orders": total_orders,
        "completed_orders": len(completed_orders),
        "total_revenue": total_revenue,
        "average_order_value": total_revenue / len(completed_orders) if completed_orders else 0,
        "average_delivery_duration": round(avg_duration, 1)
    }

# ============================================================
# Message Routes
# ============================================================
class MessageCreate(BaseModel):
    order_id: str
    message: str

@api_router.post("/messages")
async def create_message(msg: MessageCreate, user: dict = Depends(get_current_user)):
    message_doc = {
        "order_id": msg.order_id,
        "sender_id": user["_id"],
        "sender_name": user["name"],
        "sender_role": user["role"],
        "message": msg.message,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.messages.insert_one(message_doc)
    message_doc["id"] = str(result.inserted_id)
    message_doc.pop("_id", None)
    
    # Mesaj bildirimini ilgili kullanicilara gonder
    order = await db.orders.find_one({"_id": ObjectId(msg.order_id)})
    if order:
        if user["role"] != "courier" and order.get("courier_id"):
            await manager.send_to_user(order["courier_id"], "courier", {
                "type": "new_message",
                "title": f"Yeni Mesaj - {user['name']}",
                "message": msg.message[:100],
                "order_id": msg.order_id
            })
        if user["role"] != "restaurant" and order.get("restaurant_id"):
            restaurant_user = await db.users.find_one({"restaurant_id": order["restaurant_id"]})
            if restaurant_user:
                await manager.send_to_user(str(restaurant_user["_id"]), "restaurant", {
                    "type": "new_message",
                    "title": f"Yeni Mesaj - {user['name']}",
                    "message": msg.message[:100],
                    "order_id": msg.order_id
                })
    
    return message_doc

@api_router.get("/messages/{order_id}")
async def get_messages(order_id: str, user: dict = Depends(get_current_user)):
    messages = await db.messages.find(
        {"order_id": order_id},
        {"_id": 0}
    ).sort("created_at", 1).to_list(1000)
    
    return messages

# ============================================================
# Rating / Derecelendirme Routes
# ============================================================
@api_router.post("/ratings")
async def create_rating(rating: RatingCreate, user: dict = Depends(get_current_user)):
    # Siparis kontrolu
    order = await db.orders.find_one({"_id": ObjectId(rating.order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if order["status"] != "delivered":
        raise HTTPException(status_code=400, detail="Can only rate delivered orders")
    
    # Daha once derecelendirilmis mi?
    existing = await db.ratings.find_one({
        "order_id": rating.order_id,
        "rater_id": user["_id"]
    })
    if existing:
        raise HTTPException(status_code=400, detail="Already rated")
    
    rating_doc = {
        "order_id": rating.order_id,
        "rater_id": user["_id"],
        "rater_name": user["name"],
        "rater_role": user["role"],
        "rated_user_id": rating.rated_user_id,
        "rating": rating.rating,
        "comment": rating.comment,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.ratings.insert_one(rating_doc)
    rating_doc["id"] = str(result.inserted_id)
    rating_doc.pop("_id", None)
    
    # Ortalama puani guncelle
    all_ratings = await db.ratings.find({"rated_user_id": rating.rated_user_id}).to_list(1000)
    avg = sum(r["rating"] for r in all_ratings) / len(all_ratings)
    
    await db.users.update_one(
        {"_id": ObjectId(rating.rated_user_id)},
        {"$set": {"rating_avg": round(avg, 1), "rating_count": len(all_ratings)}}
    )
    
    # Siparis rating'ini guncelle
    await db.orders.update_one(
        {"_id": ObjectId(rating.order_id)},
        {"$set": {"rating": rating.rating}}
    )
    
    return rating_doc

@api_router.get("/ratings/{user_id}")
async def get_user_ratings(user_id: str, user: dict = Depends(get_current_user)):
    ratings = await db.ratings.find(
        {"rated_user_id": user_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return ratings

# ============================================================
# Promo Code / Promosyon Kodu Routes
# ============================================================
@api_router.post("/promo-codes")
async def create_promo_code(promo: PromoCodeCreate, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create promo codes")
    
    existing = await db.promo_codes.find_one({"code": promo.code.upper()})
    if existing:
        raise HTTPException(status_code=400, detail="Promo code already exists")
    
    promo_doc = {
        "code": promo.code.upper(),
        "discount_type": promo.discount_type,
        "discount_value": promo.discount_value,
        "min_order_amount": promo.min_order_amount,
        "max_uses": promo.max_uses,
        "current_uses": 0,
        "is_active": True,
        "expires_at": promo.expires_at,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.promo_codes.insert_one(promo_doc)
    promo_doc["id"] = str(result.inserted_id)
    promo_doc.pop("_id", None)
    return promo_doc

@api_router.get("/promo-codes")
async def get_promo_codes(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view promo codes")
    
    promos = await db.promo_codes.find({}, {"_id": 0}).to_list(100)
    return promos

@api_router.post("/promo-codes/apply")
async def apply_promo_code(promo: PromoCodeApply, user: dict = Depends(get_current_user)):
    code = await db.promo_codes.find_one({"code": promo.code.upper(), "is_active": True})
    if not code:
        raise HTTPException(status_code=404, detail="Invalid or expired promo code")
    
    if code["current_uses"] >= code["max_uses"]:
        raise HTTPException(status_code=400, detail="Promo code usage limit reached")
    
    if code.get("expires_at"):
        try:
            expires = datetime.fromisoformat(code["expires_at"])
            if expires < datetime.now(timezone.utc):
                raise HTTPException(status_code=400, detail="Promo code expired")
        except Exception:
            pass
    
    if promo.order_amount < code["min_order_amount"]:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum order amount: {code['min_order_amount']} TL"
        )
    
    if code["discount_type"] == "percentage":
        discount = promo.order_amount * (code["discount_value"] / 100)
    else:
        discount = code["discount_value"]
    
    discount = min(discount, promo.order_amount)
    
    return {
        "code": code["code"],
        "discount_type": code["discount_type"],
        "discount_value": code["discount_value"],
        "discount_amount": round(discount, 2),
        "final_amount": round(promo.order_amount - discount, 2)
    }

# ============================================================
# Delivery Photo / Teslimat Kaniti Routes
# ============================================================
@api_router.post("/orders/{order_id}/delivery-photo")
async def upload_delivery_photo(
    order_id: str,
    request: Request,
    user: dict = Depends(get_current_user)
):
    if user["role"] != "courier":
        raise HTTPException(status_code=403, detail="Only couriers can upload delivery photos")
    
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    body = await request.json()
    photo_url = body.get("photo_url", "")
    
    if not photo_url:
        raise HTTPException(status_code=400, detail="Photo URL required")
    
    await db.orders.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"delivery_photo_url": photo_url, "updated_at": datetime.now(timezone.utc)}}
    )
    
    # Restorana bildirim
    restaurant_id = order.get("restaurant_id")
    if restaurant_id:
        restaurant_user = await db.users.find_one({"restaurant_id": restaurant_id})
        if restaurant_user:
            await manager.send_to_user(str(restaurant_user["_id"]), "restaurant", {
                "type": "delivery_photo",
                "title": "Teslimat Kaniti Yuklendi",
                "message": f"Siparis #{order_id[:8]}",
                "order_id": order_id,
                "photo_url": photo_url
            })
    
    return {"message": "Delivery photo uploaded", "photo_url": photo_url}

@api_router.get("/orders/{order_id}/delivery-photo")
async def get_delivery_photo(order_id: str, user: dict = Depends(get_current_user)):
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return {"photo_url": order.get("delivery_photo_url")}

# ============================================================
# Analytics Routes
# ============================================================
@api_router.get("/analytics/admin")
async def get_admin_analytics(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view analytics")
    
    total_orders = await db.orders.count_documents({})
    total_couriers = await db.users.count_documents({"role": "courier"})
    total_restaurants = await db.restaurants.count_documents({})
    
    completed_orders = await db.orders.find({"status": "delivered"}).to_list(1000)
    total_revenue = sum(o.get("total_amount", 0) for o in completed_orders)
    
    active_orders = await db.orders.count_documents({
        "status": {"$in": ["pending", "assigned", "picked_up", "in_transit"]}
    })
    
    # Ortalama teslimat suresi
    durations = [o.get("delivery_duration_minutes", 0) for o in completed_orders if o.get("delivery_duration_minutes")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    
    # Bugunun istatistikleri
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_orders = await db.orders.count_documents({"created_at": {"$gte": today}})
    today_delivered = await db.orders.count_documents({"status": "delivered", "created_at": {"$gte": today}})
    
    return {
        "total_orders": total_orders,
        "active_orders": active_orders,
        "total_couriers": total_couriers,
        "total_restaurants": total_restaurants,
        "total_revenue": total_revenue,
        "completed_deliveries": len(completed_orders),
        "average_delivery_duration": round(avg_duration, 1),
        "today_orders": today_orders,
        "today_delivered": today_delivered
    }

# ============================================================
# WebSocket Endpoint (Push Bildirimleri)
# ============================================================
@app.websocket("/ws/{user_id}/{role}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, role: str):
    await manager.connect(websocket, user_id, role)
    try:
        while True:
            data = await websocket.receive_text()
            # Heartbeat / ping-pong
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id, role)
    except Exception:
        manager.disconnect(websocket, user_id, role)

# ============================================================
# Notifications Endpoint (Bildirim Gecmisi)
# ============================================================
@api_router.get("/notifications")
async def get_notifications(user: dict = Depends(get_current_user)):
    notifications = await db.notifications.find(
        {"user_id": user["_id"]},
        {"_id": 0}
    ).sort("created_at", -1).to_list(50)
    
    return notifications

@api_router.post("/notifications/read")
async def mark_notifications_read(user: dict = Depends(get_current_user)):
    await db.notifications.update_many(
        {"user_id": user["_id"], "read": False},
        {"$set": {"read": True}}
    )
    return {"message": "All notifications marked as read"}

# ============================================================
# Health Check (Railway icin)
# ============================================================
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "connected" if client else "disconnected"
    }

# ============================================================
# Startup Event
# ============================================================
@app.on_event("startup")
async def startup_event():
    # Create indexes
    await db.users.create_index("email", unique=True)
    await db.login_attempts.create_index("identifier")
    await db.orders.create_index("status")
    await db.orders.create_index("courier_id")
    await db.orders.create_index("restaurant_id")
    await db.orders.create_index("created_at")
    await db.ratings.create_index([("order_id", 1), ("rater_id", 1)])
    await db.promo_codes.create_index("code", unique=True)
    await db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    
    logger.info("Database indexes created")
    
    # Seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@getir-heri.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    
    existing_admin = await db.users.find_one({"email": admin_email})
    if existing_admin is None:
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Admin",
            "role": "admin",
            "rating_avg": 0,
            "rating_count": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        })
        logger.info(f"Admin user created: {admin_email}")
    elif not verify_password(admin_password, existing_admin["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}}
        )
        logger.info(f"Admin password updated")
    
    # Seed test courier
    test_courier_email = "courier@test.com"
    test_courier = await db.users.find_one({"email": test_courier_email})
    if not test_courier:
        await db.users.insert_one({
            "email": test_courier_email,
            "password_hash": hash_password("courier123"),
            "name": "Test Courier",
            "role": "courier",
            "phone": "+905551234567",
            "rating_avg": 0,
            "rating_count": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        })
        logger.info("Test courier created")
    
    # Seed test restaurant
    test_restaurant_email = "restaurant@test.com"
    test_restaurant = await db.users.find_one({"email": test_restaurant_email})
    if not test_restaurant:
        restaurant_doc = {
            "name": "Test Restaurant",
            "owner_email": test_restaurant_email,
            "address": "Istanbul, Turkiye",
            "phone": "+905559876543",
            "rating_avg": 0,
            "rating_count": 0,
            "created_at": datetime.now(timezone.utc)
        }
        restaurant_result = await db.restaurants.insert_one(restaurant_doc)
        restaurant_id = str(restaurant_result.inserted_id)
        
        await db.users.insert_one({
            "email": test_restaurant_email,
            "password_hash": hash_password("restaurant123"),
            "name": "Test Restaurant Owner",
            "role": "restaurant",
            "phone": "+905559876543",
            "restaurant_id": restaurant_id,
            "restaurant_name": "Test Restaurant",
            "rating_avg": 0,
            "rating_count": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        })
        logger.info("Test restaurant created")
    
    # Seed sample promo code
    existing_promo = await db.promo_codes.find_one({"code": "HOSGELDIN"})
    if not existing_promo:
        await db.promo_codes.insert_one({
            "code": "HOSGELDIN",
            "discount_type": "percentage",
            "discount_value": 20,
            "min_order_amount": 50,
            "max_uses": 1000,
            "current_uses": 0,
            "is_active": True,
            "expires_at": None,
            "created_at": datetime.now(timezone.utc)
        })
        logger.info("Sample promo code created: HOSGELDIN")

# Include the router in the main app
app.include_router(api_router)

# CORS - Railway ve Firebase icin guncellendi
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[
        os.environ.get("FRONTEND_URL", "http://localhost:3000"),
        "https://getir-heri.web.app",  # Firebase Hosting
        "https://getir-heri.firebaseapp.com",  # Firebase Hosting alt domain
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
    logger.info("Database connection closed")