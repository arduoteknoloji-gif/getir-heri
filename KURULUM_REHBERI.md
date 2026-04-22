# Getir-Heri — Canlıya Alma & Mobil Uygulama Rehberi

## 📋 İÇİNDEKİLER
1. Backend (Render.com) Deploy
2. Frontend (Web) Deploy
3. React Native Mobil Kurulum
4. Android APK / AAB Derleme
5. iOS Build (App Store)
6. Güvenlik Notları

---

## 1. BACKEND — RENDER.COM DEPLOY

### Adım 1: GitHub'a yükle
```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/KULLANICI/getir-heri-backend.git
git push -u origin main
```

### Adım 2: Render.com'da servis oluştur
1. https://render.com → **New → Web Service**
2. GitHub reposunu bağla
3. Şu ayarları gir:

| Alan | Değer |
|------|-------|
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn server:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT` |
| Region | Frankfurt (EU) |

### Adım 3: Environment Variables ekle
Render dashboard → **Environment** sekmesine git:

```
MONGO_URL   = mongodb+srv://getir_admin:Okan160505@cluster0.9xnxskz.mongodb.net/getir-db?retryWrites=true&w=majority
DB_NAME     = getir-db
JWT_SECRET  = [güçlü rastgele bir değer üret — aşağıya bak]
ENV         = production
```

**Güçlü JWT_SECRET üretmek için:**
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Adım 4: Deploy & Test
Deploy tamamlandıktan sonra health check:
```bash
curl https://getir-heri.onrender.com/api/health
# {"status":"ok","version":"1.0.0"} gelmeli
```

### Adım 5: Admin kullanıcısı oluştur
```bash
curl -X POST https://getir-heri.onrender.com/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@getir-heri.com","password":"GüçlüŞifre123!","name":"Admin","role":"admin"}'
```

---

## 2. FRONTEND — WEB DEPLOY (Firebase Hosting)

```bash
# Firebase CLI kur
npm install -g firebase-tools

# React uygulamasını derle
cd getir-heri-web
npm run build

# Firebase'e login
firebase login

# Init (sadece ilk seferde)
firebase init hosting
# Public directory: build
# Single-page app: Yes

# Deploy
firebase deploy
```

**Alternatif: Vercel ile 1 komutla deploy**
```bash
npm install -g vercel
cd getir-heri-web
vercel --prod
```

---

## 3. REACT NATIVE MOBİL KURULUM

### Ön gereksinimler
- Node.js 18+
- npm veya yarn
- Expo CLI
- EAS CLI (build için)

```bash
npm install -g expo-cli eas-cli
```

### Projeyi kur
```bash
cd getir-heri-mobile
npm install
```

### Geliştirme sunucusunu başlat
```bash
npx expo start
```

Telefonuna **Expo Go** uygulamasını indir (App Store / Play Store), 
ekrandaki QR kodu tara → uygulama açılır.

### API URL güncelle
`src/api.js` ve `src/AuthContext.js` içindeki:
```
https://getir-heri.onrender.com/api
```
URL'ini kendi Render servisinin URL'i ile değiştir.

---

## 4. ANDROID BUILD (APK / Google Play)

### EAS hesabı oluştur
```bash
# Expo hesabına giriş
eas login

# Proje bağla
eas init
```

### Test APK (iç dağıtım)
```bash
eas build --platform android --profile preview
```
Build tamamlandığında APK indirme linki gelir. 
WhatsApp/Telegram ile arkadaşlara gönderebilirsin.

### Production AAB (Google Play)
```bash
eas build --platform android --profile production
```

### Google Play'e yükleme
1. https://play.google.com/console → **Yeni Uygulama Oluştur**
2. **Uygulama adı:** Getir-Heri
3. **Production → Sürümler → Yeni Sürüm Oluştur**
4. AAB dosyasını yükle
5. Açıklama, ekran görüntüleri ekle
4. **İncelemeye Gönder**

Google inceleme süresi: **3-7 iş günü**

---

## 5. iOS BUILD (App Store)

### Gereksinimler
- Apple Developer Program üyeliği ($99/yıl)
- Mac bilgisayar VEYA EAS Cloud Build (Mac gerekmez)

### EAS ile iOS build (Mac gerekmez!)
```bash
eas build --platform ios --profile production
```

EAS otomatik olarak:
- Provisioning Profile oluşturur
- Sertifikaları yönetir
- Cloud'da derler

### App Store'a yükleme
```bash
# Otomatik submit
eas submit --platform ios
```

Veya manuel: **Transporter** uygulaması ile .ipa yükle.

App Store inceleme süresi: **1-3 iş günü**

---

## 6. GÜVENLİK KONTROL LİSTESİ

### ⚠️ YAPILMASI GEREKEN
- [ ] JWT_SECRET'i değiştir (varsayılan değeri kullanma!)
- [ ] MongoDB'de IP whitelist ayarla (Render.com IP'si)
- [ ] CORS'u daralt: `allow_origins=["https://getir-heri.web.app"]`
- [ ] Admin şifresini güçlü yap

### SSH Anahtarı Uyarısı
Önceki dosyada **özel SSH anahtarı** tespit edildi.
Eğer bu anahtarı herhangi bir yerde kullandıysanız:
```bash
# Yeni anahtar üret
ssh-keygen -t ed25519 -C "yeni-anahtar"
# Eski anahtarı GitHub/sunucudan kaldır
```

### MongoDB Güvenliği
```
Atlas → Network Access → Add IP Address
Render.com IP aralığı: 
  3.64.0.0/13 (Frankfurt)
```

---

## 7. HIZLI KOMUT REHBERİ

```bash
# Backend'i local test et
cd backend
pip install -r requirements.txt
uvicorn server:app --reload

# Mobil geliştirme
cd getir-heri-mobile
npx expo start

# Android APK üret
eas build -p android --profile preview

# iOS build
eas build -p ios --profile production

# Her iki platform aynı anda
eas build --platform all
```

---

## 8. PROJE DOSYA YAPISI

```
getir-heri-backend/
├── server.py          ← Ana API
├── requirements.txt   ← Python bağımlılıkları
├── render.yaml        ← Render deploy config
└── .env               ← Ortam değişkenleri (GİT'E EKLEME!)

getir-heri-mobile/
├── App.js             ← Navigation root
├── app.json           ← Expo config
├── eas.json           ← Build config
├── package.json
└── src/
    ├── api.js         ← HTTP client
    ├── AuthContext.js ← Auth state
    └── screens/
        ├── auth/
        │   ├── LoginScreen.js
        │   └── RegisterScreen.js
        ├── courier/
        │   ├── CourierDashboard.js
        │   ├── CourierOrderDetail.js
        │   ├── CourierHistory.js
        │   └── CourierEarnings.js
        └── restaurant/
            ├── RestaurantDashboard.js
            ├── RestaurantOrders.js
            ├── RestaurantNewOrder.js
            └── RestaurantAnalytics.js
```
