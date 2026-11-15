# 🎬 2-Minute Video Walkthrough Script (Loom Demo)
> **Project:** Resilient E-Commerce & 3PL Logistics Automation Pipeline  
> **Target Duration:** 2 minutes – 2 minutes 30 seconds  
> **Tool:** Loom.com (Free) or OBS screen recorder  

---

### ⏱️ Timeline & Action Breakdown

---

#### 🟢 [0:00 – 0:35] The Hook & The Real-World Problem
* **On Screen (Màn hình chiếu):** 
  - Mở trang `README.md` của Repo trên GitHub, cuộn đến phần **"The Problem"** hoặc mở một file Google Sheet mẫu.
* **Speaking Script (Lời bạn nói):**
  > *"Hi there! If you’ve ever built e-commerce or logistics automations using Zapier or native n8n nodes, you’ve probably hit two painful roadblocks: First, API rate limits (HTTP 429) when high-volume webhooks burst concurrently. And second, the 'blank sheet' disaster—where a workflow clears the spreadsheet range, encounters a network timeout, and leaves business owners with an empty sheet."*
* **Ý nghĩa:** Đọc vị chính xác 2 nỗi sợ lớn nhất của khách hàng ngay 30 giây đầu tiên.

---

#### 🔵 [0:35 – 1:15] The Visual Workflow in n8n
* **On Screen (Màn hình chiếu):** 
  - Chuyển sang giao diện **n8n** (hoặc mở file `n8n-workflows/ecommerce_sync.json` / sơ đồ Mermaid trong README).
  - Trỏ chuột vào các node: `Webhook Inbound` ➔ `Transform Payload` ➔ `Safe Batch Write` ➔ `Telegram Alert`.
* **Speaking Script (Lời bạn nói):**
  > *"To solve this, I designed a hybrid resilience architecture. In n8n, we handle incoming webhooks from marketplaces like TikTok Shop or logistics carriers like GHTK. n8n sanitizes the payload and routes events. But instead of relying on fragile native append nodes, n8n delegates the critical I/O to a custom Python resilience engine."*
* **Ý nghĩa:** Cho khách thấy bạn dùng n8n để tối ưu tính trực quan, nhưng có backend xử lý phần khó.

---

#### 🟣 [1:15 – 2:00] The Engineering Secret Sauce (Code Walkthrough)
* **On Screen (Màn hình chiếu):** 
  - Mở VS Code vào file `src/resilience/sheet_write_safety.py` và `gspread_resilience.py`.
  - Cuộn đến hàm kiểm tra dung lượng `SHEETS_MAX_CELLS` và đoạn cơ chế Mutex Lock.
* **Speaking Script (Lời bạn nói):**
  > *"Here in the backend, two critical protections take place: First, in `sheet_write_safety.py`, we validate workbook cell limits and chunk payloads into safe 1.8MB byte blocks. We write the new batch first, verify integrity, and only then truncate stale rows—guaranteeing zero data loss. Second, in `gspread_resilience.py`, we implement cross-process mutex file locks and jittered backoff, ensuring multiple concurrent workers never hit Google's rate-limit ceilings."*
* **Ý nghĩa:** Khẳng định đẳng cấp kỹ sư thật sự, đè bẹp 95% freelancer kéo thả thông thường.

---

#### 🔴 [2:00 – 2:30] Real Results & Call to Action
* **On Screen (Màn hình chiếu):** 
  - Quay lại phần **"Measured Results"** trên README GitHub.
* **Speaking Script (Lời bạn nói):**
  > *"In production, this architecture achieved 99.98% sync uptime across 50,000 monthly events with zero blank sheet incidents. If you're looking to build an automation pipeline that stays reliable under high volume, feel free to reach out. Thank you for watching!"*
* **Ý nghĩa:** Chốt lại bằng số liệu uy tín và kêu gọi khách hàng nhắn tin phỏng vấn.
