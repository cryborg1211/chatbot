# 📋 LD3 AI Chatbot - CURENT STAGE

Dự án phát triển hệ thống RAG Chatbot nội bộ cho Trung tâm đổi mới sáng tạo và chuyển đổi số CS III tỉnh Lâm Đồng (LD3). Kế hoạch này được thiết kế nhằm tối ưu hóa giao diện Admin, nâng cấp luồng xử lý tài liệu phức tạp, quản lý chi phí vận hành và đảm bảo hệ thống chịu tải tốt khi đưa vào vận hành thực tế.

---

## 🟥 GIAI ĐOẠN 1: Chuẩn hóa UI/UX & Data Binding (Dashboard Admin)
**Mục tiêu:** Loại bỏ hoàn toàn dữ liệu giả (mock data), hoàn thiện các tương tác cốt lõi trên giao diện Quản trị viên.

- [ ] **Dọn dẹp Mock Data:** Xóa toàn bộ dữ liệu mẫu tĩnh trên trang quản lý người dùng (`http://localhost:5101/admin`).
- [ ] **Kích hoạt các thành phần UI bị liệt:**
  - [ ] Nút danh sách bot chat "Gần đây" (Recent bots) - bổ sung liên kết động hoặc khóa tương tác nếu chưa khả dụng.
  - [ ] Nút Cài đặt hệ thống (Settings).
  - [ ] Nút Thông báo (Notifications).
  - [ ] Nút Hồ sơ cá nhân (Profile).
  - [ ] Nút Tạo cuộc trò chuyện mới (New Chat).
- [ ] **Tối ưu hóa Phân trang:** Thực hiện gắn thẻ (Semantic HTML Tags) và chuẩn hóa luồng render danh sách tại `http://localhost:5101/admin?PageNumber=1` để giao diện hiển thị gọn gàng, đúng cấu trúc kỹ thuật.

---

## 📄 GIAI ĐOẠN 2: Nâng cấp Pipeline Xử lý Tài liệu (Ingestion)
**Mục tiêu:** Xử lý triệt để định dạng PDF và chuẩn bị hạ tầng cho các tài liệu cũ dạng bản in quét mờ.

- [ ] **Hoàn thiện Module PDF Ingestion:** Tích hợp luồng upload và trích xuất cấu trúc văn bản/bảng biểu cho file PDF tiêu chuẩn thông qua Docling.
- [ ] **Kiến trúc mở rộng OCR (Future-proof):** Thiết kế sẵn interface để tích hợp các bộ thư viện OCR (Tesseract / PaddleOCR / Cloud API) nhằm bóc tách dữ liệu từ các file PDF dạng scan hoặc hình ảnh chụp văn bản hành chính trong tương lai.

---

## 🧠 GIAI ĐOẠN 3: Quản lý Model & Inference linh hoạt
**Mục tiêu:** Giảm sự phụ thuộc vào hạ tầng phần cứng cục bộ, cho phép cấu hình linh hoạt.

- [ ] **Xây dựng Module API Key Management:** - Thêm tùy chọn trong Settings cho phép Admin/User cấu hình nhập API Key từ các nhà cung cấp bên thứ ba (OpenAI, Anthropic, Gemini API).
  - Thiết kế cơ chế switch luồng linh hoạt giữa Local Inference (Ollama) và Cloud API dựa trên cấu hình Key khả dụng.

---

## 🛡️ GIAI ĐOẠN 4: Tối ưu Chi phí & Guardrails (AI Routing)
**Mục tiêu:** Tiết kiệm tài nguyên tính toán của server nội bộ và giảm chi phí token khi gọi API ngoài.

- [ ] **Cấu hình Chatbot Stages (Query Router):** Phân mảnh luồng xử lý câu hỏi ngay từ cổng vào của AI Worker.
- [ ] **Triển khai Guardrails chặn câu hỏi ngoài lề:**
  - Viết module phân loại ý định (Intent Classification) từ câu hỏi của người dùng.
  - Đối với các câu hỏi chitchat vô tri hoặc hoàn toàn không liên quan đến cơ sở dữ liệu tài liệu LD3, hệ thống tự động ngắt luồng gọi LLM và trả về các câu trả lời mẫu cấu hình sẵn (Template Responses).

---

## 🌐 GIAI ĐOẠN 5: Landing Page & Cổng vào Người dùng
**Mục tiêu:** Tạo điểm chạm đầu tiên thân thiện và chuyên nghiệp cho hệ thống.

- [ ] **Thiết kế Landing Page tối giản:** Dựng một trang giới thiệu tổng quan hoặc tích hợp thẳng giao diện Landing Page làm cổng vào của trang Chat chính, tối ưu hóa trải nghiệm đăng nhập/truy cập của cán bộ.

---

## ⚡ GIAI ĐOẠN 6: Tối ưu hiệu năng & Khả năng chịu tải (Scale >100 CCU)
**Mục tiêu:** Đảm bảo hệ thống không bị nghẽn mạng, sập queue khi có nhiều người truy cập cùng lúc.

- [ ] **Optimize Async & Concurrency:** Rà soát lại luồng xử lý bất đồng bộ trong FastAPI Worker và ASP.NET Core Controllers.
- [ ] **Tối ưu SignalR & Queue Worker:** - Tối ưu hóa bộ nhớ và băng thông của SignalR (`DocumentHub`) khi stream kết quả chat thời gian thực.
  - Cấu hình lại bộ nhớ đệm và cơ chế phân phối job của `arq queue` để xử lý mượt mà kịch bản có trên 100 người dùng hoạt động đồng thời (>100 Concurrent Users).

---
