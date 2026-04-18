from fastapi import FastAPI, APIRouter, Request, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from typing import Literal, Optional, List
from datetime import datetime, timedelta
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
import jwt
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ====================== KONFİGÜRASYON ======================
MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME", "getir-db")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://getir-heri.web.app")
ALLOWED_ORIGINS = [
    FRONTEND_URL,
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

# ====================== BAĞLANTI ======================
client = AsyncIOMotorClient(
    MONGO_URL,
    maxPoolSize=50,
    minPoolSize=10,
    maxIdleTimeMS=45000,
    serverSelectionTimeoutMS=5000,
    socketTimeoutMS=45000,
    connectTimeoutMS=10000,
    retryWrites=True,
    w="majority"
)

db = client[DB_NAME]
users_collection = db["users"]
orders_collection = db["orders"]
restaurants_collection = db["restaurants"]
courier_locations_collection = db["courier_locations"]
notifications_collection = db["notifications"]

# ====================== GÜVENLİK ======================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# ====================== MODELLER ======================
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str = Field(..., min_length=2)
    role: Literal["courier", "restaurant", "admin"]
    phone: Optional[str] = None
    restaurant_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class StatusUpdate(BaseModel):
    status: Literal["available", "busy", "offline"]

class LocationUpdate(BaseModel):
    latitude: float
    longitude: float

class OrderCreate(BaseModel):
    customer_name: str
    customer_phone: str
    customer_address: str
    customer_latitude: float
    customer_longitude: float
    items: List[dict]
    total_amount: float
    notes: Optional[str] = None

class OrderStatusUpdate(BaseModel):
    status: Literal["pending", "assigned", "picked_up", "in_transit", "delivered", "cancelled"]

class CourierAssign(BaseModel):
    courier_id: str

# ====================== YARDIMCI FONKSİYONLAR ======================
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    try:
        user = await users_collection.find_one({"_id": ObjectId(user_id)})
    except:
        raise HTTPException(status_code=401, detail="Invalid user ID")
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return {
        "_id": str(user["_id"]),
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "phone": user.get("phone"),
        "restaurant_name": user.get("restaurant_name"),
        "status": user.get("status", "offline")
    }

# ====================== FASTAPI UYGULAMA ======================
app = FastAPI(title="Getir-Heri API", version="1.0.0")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
    expose_headers=["Content-Length", "Content-Type"],
    max_age=3600,
)

# ====================== AUTH ROUTE'LAR ======================
@api_router.post("/auth/register")
async def register(user_data: UserRegister):
    existing = await users_collection.find_one({"email": user_data.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    if user_data.role == "restaurant" and not user_data.restaurant_name:
        raise HTTPException(status_code=400, detail="Restaurant name required")
    
    user_dict = {
        "email": user_data.email,
        "password": hash_password(user_data.password),
        "name": user_data.name,
        "role": user_data.role,
        "phone": user_data.phone,
        "restaurant_name": user_data.restaurant_name,
        "status": "offline",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    if user_data.role == "restaurant":
        user_dict["latitude"] = user_data.latitude
        user_dict["longitude"] = user_data.longitude
        await restaurants_collection.insert_one({
            "user_id": None,
            "name": user_data.restaurant_name,
            "owner_email": user_data.email,
            "phone": user_data.phone,
            "address": None,
            "latitude": user_data.latitude,
            "longitude": user_data.longitude,
            "created_at": datetime.utcnow()
        })
    
    result = await users_collection.insert_one(user_dict)
    user_id = str(result.inserted_id)
    
    if user_data.role == "restaurant":
        await restaurants_collection.update_one(
            {"owner_email": user_data.email},
            {"$set": {"user_id": user_id}}
        )
    
    token = create_access_token({"sub": user_id, "role": user_data.role})
    logger.info(f"New user registered: {user_data.email} as {user_data.role}")
    
    return {
        "success": True,
        "message": "Registration successful",
        "token": token,
        "user": {
            "_id": user_id,
            "name": user_data.name,
            "email": user_data.email,
            "role": user_data.role,
            "phone": user_data.phone,
            "restaurant_name": user_data.restaurant_name
        }
    }

@api_router.post("/auth/login")
async def login(user_login: UserLogin):
    user = await users_collection.find_one({"email": user_login.email})
    if not user or not verify_password(user_login.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = create_access_token({
        "sub": str(user["_id"]),
        "role": user["role"],
        "email": user["email"]
    })
    
    logger.info(f"User logged in: {user_login.email}")
    
    return {
        "success": True,
        "message": "Login successful",
        "token": token,
        "user": {
            "_id": str(user["_id"]),
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "phone": user.get("phone"),
            "restaurant_name": user.get("restaurant_name"),
            "status": user.get("status", "offline")
        }
    }

@api_router.get("/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {"success": True, "user": user}

@api_router.post("/auth/logout")
async def logout(user: dict = Depends(get_current_user)):
    return {"success": True, "message": "Logged out"}

# ====================== KURYE ROUTE'LAR ======================
@api_router.patch("/couriers/{courier_id}/status")
async def update_courier_status(courier_id: str, status_update: StatusUpdate, user: dict = Depends(get_current_user)):
    if user["_id"] != courier_id and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    
    result = await users_collection.update_one(
        {"_id": ObjectId(courier_id), "role": "courier"},
        {"$set": {"status": status_update.status, "updated_at": datetime.utcnow()}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Courier not found")
    
    return {"success": True, "message": f"Status updated to {status_update.status}", "new_status": status_update.status}

@api_router.patch("/couriers/{courier_id}/location")
async def update_courier_location(courier_id: str, location: LocationUpdate, user: dict = Depends(get_current_user)):
    if user["_id"] != courier_id and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    
    await courier_locations_collection.update_one(
        {"courier_id": courier_id},
        {"$set": {"latitude": location.latitude, "longitude": location.longitude, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    
    return {"success": True, "message": "Location updated", "location": {"lat": location.latitude, "lng": location.longitude}}

@api_router.get("/couriers/{courier_id}/earnings")
async def get_courier_earnings(courier_id: str, user: dict = Depends(get_current_user)):
    if user["_id"] != courier_id and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    
    delivered_orders = await orders_collection.find({"courier_id": courier_id, "status": "delivered"}).to_list(length=None)
    total_earnings = sum(order.get("delivery_fee", 15) for order in delivered_orders)
    
    return {
        "success": True,
        "total_earnings": total_earnings,
        "total_deliveries": len(delivered_orders),
        "orders": [{"order_id": str(order["_id"]), "amount": order.get("delivery_fee", 15), "date": order.get("delivered_at", order.get("updated_at"))} for order in delivered_orders[-10:]]
    }

@api_router.get("/couriers/available")
async def get_available_couriers(user: dict = Depends(get_current_user)):
    if user["role"] not in ["restaurant", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    couriers = await users_collection.find({"role": "courier", "status": "available"}).to_list(length=50)
    result = []
    for courier in couriers:
        location = await courier_locations_collection.find_one({"courier_id": str(courier["_id"])})
        result.append({
            "_id": str(courier["_id"]),
            "name": courier["name"],
            "phone": courier.get("phone"),
            "status": courier.get("status", "offline"),
            "location": {"lat": location["latitude"], "lng": location["longitude"]} if location else None
        })
    return {"success": True, "couriers": result}

@api_router.get("/couriers")
async def get_all_couriers(user: dict = Depends(get_current_user)):
    if user["role"] not in ["admin", "restaurant"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    couriers = await users_collection.find({"role": "courier"}).to_list(length=100)
    result = []
    for courier in couriers:
        active_orders = await orders_collection.count_documents({
            "courier_id": str(courier["_id"]),
            "status": {"$in": ["assigned", "picked_up", "in_transit"]}
        })
        result.append({
            "_id": str(courier["_id"]),
            "name": courier["name"],
            "email": courier["email"],
            "phone": courier.get("phone"),
            "status": courier.get("status", "offline"),
            "created_at": courier.get("created_at"),
            "active_orders": active_orders
        })
    return {"success": True, "data": result}

# ====================== SİPARİŞ ROUTE'LAR ======================
@api_router.post("/orders")
async def create_order(order_data: OrderCreate, user: dict = Depends(get_current_user)):
    if user["role"] != "restaurant":
        raise HTTPException(status_code=403, detail="Only restaurants can create orders")
    
    order_dict = {
        "restaurant_id": user["_id"],
        "restaurant_name": user.get("restaurant_name", "Unknown"),
        "customer_name": order_data.customer_name,
        "customer_phone": order_data.customer_phone,
        "customer_address": order_data.customer_address,
        "customer_latitude": order_data.customer_latitude,
        "customer_longitude": order_data.customer_longitude,
        "items": order_data.items,
        "total_amount": order_data.total_amount,
        "delivery_fee": 25.0,
        "notes": order_data.notes,
        "status": "pending",
        "courier_id": None,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = await orders_collection.insert_one(order_dict)
    order_id = str(result.inserted_id)
    logger.info(f"New order created: {order_id} by restaurant {user['_id']}")
    
    return {"success": True, "message": "Order created successfully", "order_id": order_id, "order": {**order_dict, "_id": order_id}}

@api_router.get("/orders")
async def get_orders(status: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {}
    if user["role"] == "courier":
        query = {"status": "pending", "courier_id": None} if status == "available" else {"courier_id": user["_id"]}
    elif user["role"] == "restaurant":
        query = {"restaurant_id": user["_id"]}
    if status and status != "available":
        query["status"] = status
    
    orders = await orders_collection.find(query).sort("created_at", -1).to_list(length=100)
    result = []
    for order in orders:
        order_dict = {
            "_id": str(order["_id"]), "id": str(order["_id"]),
            "restaurant_id": order["restaurant_id"], "restaurant_name": order.get("restaurant_name"),
            "customer_name": order["customer_name"], "customer_phone": order["customer_phone"],
            "customer_address": order["customer_address"], "customer_latitude": order["customer_latitude"],
            "customer_longitude": order["customer_longitude"], "items": order["items"],
            "total_amount": order["total_amount"], "delivery_fee": order.get("delivery_fee", 25),
            "status": order["status"], "courier_id": order.get("courier_id"), "notes": order.get("notes"),
            "created_at": order["created_at"], "updated_at": order["updated_at"]
        }
        if order.get("courier_id"):
            courier = await users_collection.find_one({"_id": ObjectId(order["courier_id"])})
            if courier:
                order_dict["courier_name"] = courier["name"]
                order_dict["courier"] = {"_id": str(courier["_id"]), "name": courier["name"], "phone": courier.get("phone")}
        result.append(order_dict)
    return {"success": True, "orders": result, "data": result}

@api_router.get("/orders/{order_id}")
async def get_order(order_id: str, user: dict = Depends(get_current_user)):
    try:
        order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    except:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if user["role"] == "restaurant" and order["restaurant_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if user["role"] == "courier" and order.get("courier_id") != user["_id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    order_dict = {
        "_id": str(order["_id"]), "id": str(order["_id"]),
        "restaurant_id": order["restaurant_id"], "restaurant_name": order.get("restaurant_name"),
        "customer_name": order["customer_name"], "customer_phone": order["customer_phone"],
        "customer_address": order["customer_address"], "customer_latitude": order["customer_latitude"],
        "customer_longitude": order["customer_longitude"], "pickup_lat": order.get("restaurant_latitude"),
        "pickup_lng": order.get("restaurant_longitude"), "items": order["items"],
        "total_amount": order["total_amount"], "delivery_fee": order.get("delivery_fee", 25),
        "status": order["status"], "courier_id": order.get("courier_id"), "notes": order.get("notes"),
        "created_at": order["created_at"], "updated_at": order["updated_at"]
    }
    if order.get("courier_id"):
        courier = await users_collection.find_one({"_id": ObjectId(order["courier_id"])})
        if courier:
            order_dict["courier_name"] = courier["name"]
            order_dict["courier"] = {"_id": str(courier["_id"]), "name": courier["name"], "phone": courier.get("phone")}
            location = await courier_locations_collection.find_one({"courier_id": order["courier_id"]})
            if location:
                order_dict["courier_lat"] = location["latitude"]
                order_dict["courier_lng"] = location["longitude"]
                order_dict["courier_location"] = {"lat": location["latitude"], "lng": location["longitude"], "updated_at": location["updated_at"]}
    return {"success": True, "order": order_dict}

@api_router.post("/orders/{order_id}/accept")
async def accept_order(order_id: str, user: dict = Depends(get_current_user)):
    if user["role"] != "courier":
        raise HTTPException(status_code=403, detail="Only couriers can accept orders")
    
    courier = await users_collection.find_one({"_id": ObjectId(user["_id"])})
    if courier.get("status") == "busy":
        raise HTTPException(status_code=400, detail="You already have an active order")
    
    try:
        order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    except:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["status"] != "pending" or order.get("courier_id"):
        raise HTTPException(status_code=400, detail="Order already assigned")
    
    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"courier_id": user["_id"], "status": "assigned", "updated_at": datetime.utcnow()}}
    )
    await users_collection.update_one({"_id": ObjectId(user["_id"])}, {"$set": {"status": "busy"}})
    logger.info(f"Order {order_id} accepted by courier {user['_id']}")
    
    return {"success": True, "message": "Order accepted", "order_id": order_id}

@api_router.patch("/orders/{order_id}/status")
async def update_order_status(order_id: str, status_update: OrderStatusUpdate, user: dict = Depends(get_current_user)):
    try:
        order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    except:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if user["role"] == "courier" and order.get("courier_id") != user["_id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if user["role"] == "restaurant" and order["restaurant_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    update_data = {"status": status_update.status, "updated_at": datetime.utcnow()}
    if status_update.status == "delivered":
        update_data["delivered_at"] = datetime.utcnow()
        if order.get("courier_id"):
            await users_collection.update_one({"_id": ObjectId(order["courier_id"])}, {"$set": {"status": "available"}})
    
    await orders_collection.update_one({"_id": ObjectId(order_id)}, {"$set": update_data})
    logger.info(f"Order {order_id} status updated to {status_update.status}")
    
    return {"success": True, "message": f"Status updated to {status_update.status}", "new_status": status_update.status}

@api_router.post("/orders/{order_id}/assign")
async def assign_courier(order_id: str, assign_data: CourierAssign, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        order = await orders_collection.find_one({"_id": ObjectId(order_id)})
    except:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    courier = await users_collection.find_one({"_id": ObjectId(assign_data.courier_id), "role": "courier"})
    if not courier:
        raise HTTPException(status_code=404, detail="Courier not found")
    
    await orders_collection.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"courier_id": assign_data.courier_id, "status": "assigned", "updated_at": datetime.utcnow()}}
    )
    await users_collection.update_one({"_id": ObjectId(assign_data.courier_id)}, {"$set": {"status": "busy"}})
    return {"success": True, "message": "Courier assigned"}

# ====================== RESTORAN ROUTE'LAR ======================
@api_router.get("/restaurants/{restaurant_id}/analytics")
async def get_restaurant_analytics(restaurant_id: str, user: dict = Depends(get_current_user)):
    if user["_id"] != restaurant_id and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    
    pipeline = [
        {"$match": {"restaurant_id": restaurant_id}},
        {"$group": {"_id": None, "total_orders": {"$sum": 1}, "completed_orders": {"$sum": {"$cond": [{"$eq": ["$status", "delivered"]}, 1, 0]}}, "total_revenue": {"$sum": "$total_amount"}, "average_order_value": {"$avg": "$total_amount"}}}
    ]
    stats = await orders_collection.aggregate(pipeline).to_list(length=1)
    
    if stats:
        data = stats[0]
        return {"success": True, "total_orders": data.get("total_orders", 0), "completed_orders": data.get("completed_orders", 0), "total_revenue": data.get("total_revenue", 0), "average_order_value": data.get("average_order_value", 0) or 0}
    return {"success": True, "total_orders": 0, "completed_orders": 0, "total_revenue": 0, "average_order_value": 0}

@api_router.get("/restaurants")
async def get_restaurants(user: dict = Depends(get_current_user)):
    if user["role"] not in ["admin", "courier"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    restaurants = await users_collection.find({"role": "restaurant"}).to_list(length=100)
    return {
        "success": True,
        "restaurants": [{"_id": str(r["_id"]), "id": str(r["_id"]), "name": r.get("restaurant_name", r["name"]), "owner_email": r["email"], "phone": r.get("phone"), "latitude": r.get("latitude"), "longitude": r.get("longitude"), "created_at": r.get("created_at")} for r in restaurants]
    }

# ====================== ADMIN ROUTE'LAR ======================
@api_router.get("/admin/dashboard")
async def admin_dashboard(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    total_users = await users_collection.count_documents({})
    total_orders = await orders_collection.count_documents({})
    total_couriers = await users_collection.count_documents({"role": "courier"})
    total_restaurants = await users_collection.count_documents({"role": "restaurant"})
    pending_orders = await orders_collection.count_documents({"status": "pending"})
    active_orders = await orders_collection.count_documents({"status": {"$in": ["assigned", "picked_up", "in_transit"]}})
    
    revenue_pipeline = [{"$match": {"status": "delivered"}}, {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}]
    revenue_result = await orders_collection.aggregate(revenue_pipeline).to_list(length=1)
    total_revenue = revenue_result[0]["total"] if revenue_result else 0
    
    return {"success": True, "stats": {"total_users": total_users, "total_orders": total_orders, "total_couriers": total_couriers, "total_restaurants": total_restaurants, "pending_orders": pending_orders, "active_orders": active_orders, "total_revenue": total_revenue}}

@api_router.get("/analytics/admin")
async def admin_analytics(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    total_orders = await orders_collection.count_documents({})
    completed_deliveries = await orders_collection.count_documents({"status": "delivered"})
    total_couriers = await users_collection.count_documents({"role": "courier"})
    total_restaurants = await users_collection.count_documents({"role": "restaurant"})
    
    revenue_pipeline = [{"$match": {"status": "delivered"}}, {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}]
    revenue_result = await orders_collection.aggregate(revenue_pipeline).to_list(length=1)
    total_revenue = revenue_result[0]["total"] if revenue_result else 0
    
    return {"success": True, "total_orders": total_orders, "completed_deliveries": completed_deliveries, "total_couriers": total_couriers, "total_restaurants": total_restaurants, "total_revenue": total_revenue, "average_order_value": (total_revenue / completed_deliveries) if completed_deliveries > 0 else 0}

# ====================== SAĞLIK KONTROLÜ ======================
@app.get("/health")
async def health_check():
    try:
        await db.command("ping")
        db_status = "connected"
    except Exception as e:
        db_status = f"disconnected: {str(e)}"
    return {"status": "healthy", "database": db_status, "timestamp": datetime.utcnow()}

@app.on_event("startup")
async def startup_event():
    logger.info("Application started")

app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)