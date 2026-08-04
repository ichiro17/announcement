# 部署到 Zeabur

這份指南把系統從「本機原型」部署成可以讓全校教職員連線使用的正式版本。
以下步驟中，凡是需要登入你自己帳號（GitHub、Zeabur）的部分，都需要你自己在瀏覽器操作。

## 事前準備

- 一個 GitHub 帳號（沒有的話到 https://github.com/signup 免費註冊）
- 一個 Zeabur 帳號（到 https://zeabur.com 用 GitHub 登入即可，不用另外註冊密碼）

## 步驟 1：把程式碼推上 GitHub

1. 到 https://github.com/new 建立一個新的 repository
   - 名稱建議：`bulletin-signoff`
   - 建議設為 **Private**（校內系統，程式碼不需要公開）
   - 不要勾選「Add a README」等初始化選項（本機已經有完整專案了）
2. 建立完成後，GitHub 會顯示一個 repo 網址，例如
   `https://github.com/<你的帳號>/bulletin-signoff.git`
3. 回到終端機，在 `bulletin-signoff` 資料夾內執行（把網址換成你自己的）：

   ```bash
   git remote add origin https://github.com/<你的帳號>/bulletin-signoff.git
   git branch -M main
   git push -u origin main
   ```

## 步驟 2：在 Zeabur 建立專案並部署

1. 登入 https://zeabur.com
2. 建立新 Project → 新增 Service → 選擇「Deploy from GitHub」→ 選你剛剛推上去的 `bulletin-signoff` repo
3. Zeabur 會自動偵測到 `Dockerfile` 並用它建置，不用額外設定 build 指令

## 步驟 3：加上永久儲存空間（Volume）

SQLite 資料庫檔案要存在容器裡的 `/data` 資料夾，但容器本身重新部署時內容會重置，
所以一定要掛一個永久磁碟，不然每次重新部署資料就會消失：

1. 在該 Service 的設定頁面找到「Volume」
2. 新增一個 Volume，掛載路徑設為 `/data`

## 步驟 4：設定環境變數

在 Service 的「Environment Variables」設定：

| 變數名稱 | 值 | 說明 |
|---|---|---|
| `SECRET_KEY` | 一串隨機長字串 | 用來加密登入 session，絕對不能用預設值。可在本機執行下面指令產生一組：`python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATA_DIR` | `/data` | 對應步驟 3 掛的 Volume 路徑 |
| `PRODUCTION` | `1` | 開啟正式環境設定（安全 cookie、信任反向代理的 HTTPS 標頭） |

設定完儲存後，Zeabur 會自動重新部署。

## 步驟 5：取得網址、第一次登入

1. 在 Service 的「Domains」分頁，Zeabur 會提供一個免費網址（例如 `bulletin-signoff.zeabur.app`），
   也可以在這裡綁定學校自己的網域（如果之後想要 `xxx.tn.edu.tw` 之類的子網域，需要另外跟校內
   資訊組或教育局申請網域，並在這裡設定 CNAME）。
2. 開啟該網址，用預設帳號登入：
   - 帳號：`admin`
   - 密碼：`admin123`
3. **系統會強制要求立刻修改密碼**，請馬上改成只有你知道的密碼。
4. 前往「教職員名冊」→「批次匯入 CSV」匯入全校教職員名冊。

## 之後要更新程式碼時

本機修改完程式碼後：

```bash
git add -A
git commit -m "說明這次改了什麼"
git push
```

Zeabur 偵測到 GitHub 有新的 commit 會自動重新建置部署，資料庫內容因為存在 Volume 裡，
不會因為重新部署而消失。

## 備份提醒

Volume 裡的 SQLite 檔案是全校簽收紀錄的唯一副本。建議定期（例如每學期）從 Zeabur 的
Volume 管理介面下載備份，或請資訊組協助排程備份，避免單一儲存空間損壞造成資料全部遺失。
