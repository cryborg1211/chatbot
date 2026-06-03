import os
import time
import requests
import urllib3
from urllib.parse import quote, urlparse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def crawl_lamdong_rest_api():
    print("🚜 Quay xe! Dùng REST API gốc cào 500 văn bản mới nhất...\n")
    
    session = requests.Session()
    session.verify = False
    session.headers.update({
        "Accept": "application/json;odata=verbose",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Connection": "keep-alive"
    })

    folder_pdf  = "data/lamdong_pdf"
    folder_doc  = "data/lamdong_docs"
    os.makedirs(folder_pdf, exist_ok=True)
    os.makedirs(folder_doc, exist_ok=True)
    downloaded_files = set(os.listdir(folder_pdf)) | set(os.listdir(folder_doc))
    
    # Dùng lại API cũ đã từng chạy thành công, mở rộng top=500 lấy cho sướng
    list_api_url = "https://lamdong.gov.vn/sites/skhcn/_api/web/lists/getByTitle('Qu%E1%BA%A3n%20l%C3%BD%20v%C4%83n%20b%E1%BA%A3n')/items?$select=Id,Title&$top=500&$filter=Attachments eq 1&$orderby=Created desc"
    try:
        print("📡 Đang lấy danh sách ID từ server...")
        # Lấy một phát 500 ID luôn, thao tác này cực nhẹ nên server trả về ngay
        response = session.get(list_api_url, timeout=(10, 30))
        if response.status_code != 200:
            print(f"🛑 Lỗi API danh sách: {response.status_code} - {response.text}")
            return

        items = response.json().get('d', {}).get('results', [])
        if not items:
            print("⚠️ Server không trả về ID nào!")
            return
            
        print(f"🎯 Server đã nhả {len(items)} ID. Bắt đầu tỉa file...\n")
        tong_so_file = 0

        for index, item in enumerate(items):
            item_id = item['Id']
            title = item.get('Title') or 'Khong_Tieu_De'

            print(f"[{index+1}/{len(items)}] 🔍 ID {item_id}: {title[:40]}...")

            # API moi ruột đính kèm
            attachment_api = f"https://lamdong.gov.vn/sites/skhcn/_api/web/lists/getByTitle('Qu%E1%BA%A3n%20l%C3%BD%20v%C4%83n%20b%E1%BA%A3n')/items({item_id})?$expand=AttachmentFiles"            
            try:
                att_resp = session.get(attachment_api, timeout=(10, 20))
                if att_resp.status_code == 200:
                    attachments = att_resp.json().get('d', {}).get('AttachmentFiles', {}).get('results', [])
                    
                    if not attachments:
                        print("    🈳 Bài này không có file đính kèm.")
                        continue

                    for file in attachments:
                        file_name = file['FileName']
                        
                        # Lấy cả PDF và Word
                        if not (file_name.lower().endswith('.pdf') or file_name.lower().endswith('.doc') or file_name.lower().endswith('.docx')):
                            continue
                            
                        if file_name in downloaded_files:
                            print(f"    ⏩ Đã có sẵn: {file_name}")
                            continue
                            
                        # Encode URL và kéo file
                        dl_url = f"https://lamdong.gov.vn{quote(file['ServerRelativeUrl'], safe='/')}"
                        pdf_resp = session.get(dl_url, timeout=(10, 60))
                        
                        if pdf_resp.status_code == 200:
                            dest = folder_pdf if file_name.lower().endswith('.pdf') else folder_doc
                            save_path = os.path.join(dest, file_name)
                            with open(save_path, 'wb') as f:
                                f.write(pdf_resp.content)
                            print(f"    ✅ Đã tải: {file_name}")
                            tong_so_file += 1
                            downloaded_files.add(file_name)
                            
                            # Ngủ 1 giây để vuốt ve con server
                            time.sleep(1) 
                        else:
                            print(f"    ⚠️ Lỗi {pdf_resp.status_code} tải file")
                else:
                     print(f"    ⚠️ Lỗi API {att_resp.status_code} khi soi ID {item_id}")
            except Exception as e:
                print(f"    ⚠️ Bỏ qua ID {item_id} do timeout/lỗi: {e}")
                time.sleep(3) # Nếu nghẽn mạng thì tự ngủ 3s cho thông tĩnh mạch

        print(f"\n🎉 HOÀN TẤT CHIẾN DỊCH! Đã cào thành công {tong_so_file} file.")

    except Exception as e:
        print(f"💥 Code gãy: {e}")

if __name__ == "__main__":
    crawl_lamdong_rest_api()