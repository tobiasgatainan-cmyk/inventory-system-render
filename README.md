[README.md](https://github.com/user-attachments/files/29027535/README.md)
# 美善基金會庫存管理系統 — 部署說明

## 檔案結構
```
inventory-system-render/
├── main.py              ← Flask 主程式
├── gsheet.py            ← Google Sheet 同步模組
├── requirements.txt
├── render.yaml
└── templates/
    ├── base.html
    ├── login.html
    ├── index.html
    └── admin/
        ├── users.html
        ├── user_form.html
        ├── items.html
        ├── item_form.html
        └── logs.html
```

---

## 步驟 1：Render 建立 PostgreSQL 資料庫

1. 進入 Render Dashboard → **New → PostgreSQL**
2. 名稱填 `inventory-db`，Plan 選 **Free**
3. 建立後，複製 **Internal Database URL**

---

## 步驟 2：設定 Render Web Service 環境變數

在你的 Web Service → **Environment** 頁面新增：

| 變數名稱 | 值 |
|---|---|
| `DATABASE_URL` | 上面複製的 Internal Database URL |
| `SECRET_KEY` | 任意長字串（如 `openssl rand -hex 32` 產生） |
| `GOOGLE_SHEET_ID` | （之後 Google Sheet 設定完再填） |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | （之後 Google Sheet 設定完再填） |

---

## 步驟 3：Google Sheet 連結設定

### 3a. 建立 Google Service Account
1. 進入 [Google Cloud Console](https://console.cloud.google.com)
2. 建立新專案（或使用現有）
3. 啟用 **Google Sheets API** 與 **Google Drive API**
4. IAM → **Service Accounts → 建立 Service Account**
5. 建立完成後，點進去 → **Keys → Add Key → JSON**
6. 下載 JSON 檔案

### 3b. 設定試算表
1. 在 Google Sheets 建立新試算表
2. 把試算表分享給 Service Account 的 email（編輯者）
   - email 格式：`your-sa@your-project.iam.gserviceaccount.com`
3. 複製試算表網址中的 ID（`/d/` 後面那段）

### 3c. 填入 Render 環境變數
- `GOOGLE_SHEET_ID`：貼上試算表 ID
- `GOOGLE_SERVICE_ACCOUNT_JSON`：把整個 JSON 檔案內容貼進去（一整行）

---

## 步驟 4：推送到 GitHub

```bash
git add .
git commit -m "feat: add full admin system with DB and Google Sheet sync"
git push origin main
```

Render 會自動重新部署。

---

## 預設帳號

| 帳號 | 密碼 | 角色 |
|---|---|---|
| `admin` | `admin1234` | 管理員 |

> ⚠️ 上線後請立即到「使用者管理」更改管理員密碼！

---

## 角色權限

| 功能 | 管理員 | 編輯者 | 檢視者 |
|---|:---:|:---:|:---:|
| 查看庫存 | ✅ | ✅ | ✅ |
| 調整庫存 | ✅ | ✅ | ❌ |
| 新增/編輯/刪除品項 | ✅ | ✅ | ❌ |
| 查看異動紀錄 | ✅ | ✅ | ❌ |
| 管理使用者 | ✅ | ❌ | ❌ |
| Google Sheet 同步 | ✅ | ❌ | ❌ |
