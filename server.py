from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import jwt
import bcrypt
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "getir-db")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-12345")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7

app = FastAPI(title="Getir-Heri API", version="1.0.0")
security = HTTPBearer()

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== HELPERS =====

def get_now():
    return datetime.now(timezone.utc)

def to_obj_id(id_str: str):
    try:
        return ObjectId(id_str)
    except:
        raise HTTPException(status_code=400, detail="Geçersiz ID formatı")

def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def serialize_user(user: dict) -> dict:
    user["_id"] = str(user["_id"])
    user.pop("password", None)
    return user

async def get_current_user(auth: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(auth.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Geçersiz token")
        user = await db.users.find_one({"_id": to_obj_id(user_id)})
        if not user:
            raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı")
        return serialize_user(user)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token süresi dolmuş")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Oturum süresi dolmuş")

# ===== HEALTH CHECK =====

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

# ===== AUTH =====

@app.post("/api/auth/register")
async def register(data: dict):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    name = data.get("name", "").strip()
    role = data.get("role", "customer")

    if not email or not password or not name:
        raise HTTPException(status_code=400, detail="Email, şifre ve isim zorunludur")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Şifre en az 6 karakter olmalıdır")

    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Bu email zaten kayıtlı")

    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_doc = {
        "email": email,
        "password": hashed_pw,
        "name": name,
        "role": role,
        "phone": data.get("phone"),
        "restaurant_name": data.get("restaurant_name"),
        "status": "offline" if role == "courier" else "active",
        "created_at": get_now(),
        "updated_at": get_now()
    }
    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = str(result.inserted_id)
    user_doc.pop("password", None)
    token = create_token(str(result.inserted_id))
    return {"success": True, "token": token, "user": user_doc}

@app.post("/api/auth/login")
async def login(data: dict):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    user = await db.users.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=401, detail="Email veya şifre hatalı")
    if not bcrypt.checkpw(password.encode(), user.get("password", "").encode()):
        raise HTTPException(status_code=401, detail="Email veya şifre hatalı")
    token = create_token(str(user["_id"]))
    return {"success": True, "token": token, "user": serialize_user(user)}

@app.post("/api/auth/logout")
async def logout(user: dict = Depends(get_current_user)):
    return {"success": True, "message": "Çıkış yapıldı"}

# ===== ORDERS =====

@app.get("/api/orders")
async def get_orders(user: dict = Depends(get_current_user)):
    query = {}
    if user["role"] == "restaurant":
        query = {"restaurant_id": user["_id"]}
    elif user["role"] == "courier":
        query = {"$or": [{"status": "pending"}, {"courier_id": user["_id"]}]}
    cursor = db.orders.find(query).sort("created_at", -1)
    orders = await cursor.to_list(length=100)
    for o in orders:
        o["_id"] = str(o["_id"])
        o["id"] = o["_id"]
    return {"orders": orders}

@app.get("/api/orders/{order_id}")
async def get_order(order_id: str, user: dict = Depends(get_current_user)):
    order = await db.orders.find_one({"_id": to_obj_id(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    order["_id"] = str(order["_id"])
    order["id"] = order["_id"]
    return order

@app.post("/api/orders")
async def create_order(order_data: dict, user: dict = Depends(get_current_user)):
    if user["role"] != "restaurant":
        raise HTTPException(status_code=403, detail="Sadece restoranlar sipariş girebilir")
    order_data["restaurant_id"] = user["_id"]
    order_data["restaurant_name"] = user.get("restaurant_name") or user.get("name")
    order_data["status"] = "pending"
    order_data["created_at"] = get_now()
    order_data["updated_at"] = get_now()
    result = await db.orders.insert_one(order_data)
    return {"id": str(result.inserted_id), "status": "success"}

@app.patch("/api/orders/{order_id}")
async def update_order(order_id: str, data: dict, user: dict = Depends(get_current_user)):
    update_data = {k: v for k, v in data.items() if k not in ["_id", "id"]}
    update_data["updated_at"] = get_now()
    result = await db.orders.update_one({"_id": to_obj_id(order_id)}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return {"message": "Sipariş güncellendi"}

@app.post("/api/orders/{order_id}/accept")
async def accept_order(order_id: str, user: dict = Depends(get_current_user)):
    if user["role"] != "courier":
        raise HTTPException(status_code=403, detail="Sadece kuryeler sipariş kabul edebilir")
    order = await db.orders.find_one({"_id": to_obj_id(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    if order.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Bu sipariş artık müsait değil")
    await db.orders.update_one(
        {"_id": to_obj_id(order_id)},
        {"$set": {
            "courier_id": user["_id"],
            "courier_name": user["name"],
            "status": "assigned",
            "updated_at": get_now()
        }}
    )
    return {"message": "Sipariş kabul edildi"}

# ===== COURIERS =====

@app.get("/api/couriers")
async def get_couriers(user: dict = Depends(get_current_user)):
    cursor = db.users.find({"role": "courier"})
    couriers = await cursor.to_list(length=200)
    for c in couriers:
        c["_id"] = str(c["_id"])
        c.pop("password", None)
    return {"data": couriers}

@app.patch("/api/couriers/{courier_id}/status")
async def update_courier_status(courier_id: str, data: dict, user: dict = Depends(get_current_user)):
    if user["role"] != "admin" and user["_id"] != courier_id:
        raise HTTPException(status_code=403, detail="Yetkisiz işlem")
    await db.users.update_one(
        {"_id": to_obj_id(courier_id)},
        {"$set": {"status": data["status"], "updated_at": get_now()}}
    )
    return {"message": "Durum güncellendi"}

@app.patch("/api/couriers/{courier_id}/location")
async def update_courier_location(courier_id: str, data: dict, user: dict = Depends(get_current_user)):
    if user["_id"] != courier_id:
        raise HTTPException(status_code=403, detail="Yetkisiz işlem")
    await db.users.update_one(
        {"_id": to_obj_id(courier_id)},
        {"$set": {
            "current_lat": data.get("lat"),
            "current_lng": data.get("lng"),
            "updated_at": get_now()
        }}
    )
    return {"message": "Konum güncellendi"}

@app.get("/api/couriers/{courier_id}/earnings")
async def get_courier_earnings(courier_id: str, user: dict = Depends(get_current_user)):
    if user["role"] != "admin" and user["_id"] != courier_id:
        raise HTTPException(status_code=403, detail="Yetkisiz işlem")
    cursor = db.orders.find({"courier_id": courier_id, "status": "delivered"})
    delivered = await cursor.to_list(length=1000)
    total_earnings = sum(o.get("delivery_fee", 0) for o in delivered)
    total_deliveries = len(delivered)
    avg = total_earnings / total_deliveries if total_deliveries > 0 else 0
    return {
        "total_earnings": total_earnings,
        "total_deliveries": total_deliveries,
        "average_per_delivery": avg
    }

# ===== RESTAURANTS =====

@app.get("/api/restaurants/{restaurant_id}/analytics")
async def get_restaurant_analytics(restaurant_id: str, user: dict = Depends(get_current_user)):
    cursor = db.orders.find({"restaurant_id": restaurant_id})
    orders = await cursor.to_list(length=1000)
    total_orders = len(orders)
    completed = [o for o in orders if o.get("status") == "delivered"]
    total_revenue = sum(o.get("total_amount", 0) for o in completed)
    avg_order = total_revenue / len(completed) if completed else 0
    return {
        "total_orders": total_orders,
        "completed_orders": len(completed),
        "total_revenue": total_revenue,
        "average_order_value": avg_order
    }

# ===== ADMIN =====

@app.get("/api/admin/dashboard-stats")
async def admin_dashboard_stats(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Yetkisiz")
    total_orders = await db.orders.count_documents({})
    pending_orders = await db.orders.count_documents({"status": "pending"})
    total_users = await db.users.count_documents({})
    total_restaurants = await db.users.count_documents({"role": "restaurant"})
    active_couriers = await db.users.count_documents({"role": "courier", "status": "available"})
    pipeline = [{"$match": {"status": "delivered"}}, {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}]
    revenue_result = await db.orders.aggregate(pipeline).to_list(1)
    total_revenue = revenue_result[0]["total"] if revenue_result else 0
    return {
        "totalOrders": total_orders,
        "pendingOrders": pending_orders,
        "totalUsers": total_users,
        "totalRestaurants": total_restaurants,
        "activeCouriers": active_couriers,
        "totalRevenue": total_revenue
    }

@app.get("/api/admin/recent-orders")
async def admin_recent_orders(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Yetkisiz")
    cursor = db.orders.find({}).sort("created_at", -1).limit(10)
    orders = await cursor.to_list(length=10)
    for o in orders:
        o["_id"] = str(o["_id"])
    return orders

@app.get("/api/admin/orders")
async def admin_get_orders(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Yetkisiz")
    cursor = db.orders.find({}).sort("created_at", -1)
    orders = await cursor.to_list(length=500)
    for o in orders:
        o["_id"] = str(o["_id"])
        o["id"] = o["_id"]
    return orders

@app.get("/api/admin/couriers")
async def admin_get_couriers(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Yetkisiz")
    cursor = db.users.find({"role": "courier"})
    couriers = await cursor.to_list(length=500)
    for c in couriers:
        c["_id"] = str(c["_id"])
        c.pop("password", None)
    return couriers

@app.get("/api/admin/restaurants")
async def admin_get_restaurants(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Yetkisiz")
    cursor = db.users.find({"role": "restaurant"})
    restaurants = await cursor.to_list(length=500)
    for r in restaurants:
        r["_id"] = str(r["_id"])
        r.pop("password", None)
        r["name"] = r.get("restaurant_name") or r.get("name")
    return restaurants

@app.get("/api/admin/analytics")
async def admin_analytics(period: str = "week", user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Yetkisiz")
    now = get_now()
    days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(period, 7)
    start_date = now - timedelta(days=days)
    total_orders = await db.orders.count_documents({"created_at": {"$gte": start_date}})
    active_couriers = await db.users.count_documents({"role": "courier", "status": "available"})
    total_restaurants = await db.users.count_documents({"role": "restaurant"})
    pipeline = [
        {"$match": {"status": "delivered", "created_at": {"$gte": start_date}}},
        {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
    ]
    revenue_result = await db.orders.aggregate(pipeline).to_list(1)
    total_revenue = revenue_result[0]["total"] if revenue_result else 0
    return {
        "totalOrders": total_orders,
        "ordersToday": 0,
        "totalRevenue": total_revenue,
        "revenueToday": 0,
        "activeCouriers": active_couriers,
        "totalRestaurants": total_restaurants
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
