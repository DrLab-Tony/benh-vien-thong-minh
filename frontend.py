import base64
from contextlib import contextmanager
import datetime
import io
import json
import os
import random
import re
import shutil
import smtplib
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from PIL import Image
import plotly.graph_objects as go
import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
import requests
import streamlit as st

# --- QUY TẮC STREAMLIT: st.set_page_config BẮT BUỘC ĐỨNG ĐẦU TIÊN ---
st.set_page_config(
    page_title="MedAI - Bệnh Viện Thông Minh - DR. Nguyễn Tiến Toàn",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_dotenv(override=True)

BASE_URL = "https://medical-ai-backend-dn29.onrender.com/api/v1"
RENDER_DB_URL = "postgresql://medical_db_ghev_user:RAfswrMNjsah0eQ6ZjMqQyN4HwDKoKpQ@dpg-da6626fqj5pc73e6pqfg-a.singapore-postgres.render.com/medical_db_ghev"


# ==============================================================================
# HẠ TẦNG KẾT NỐI: CONNECTION POOLING VÀ CONTEXT MANAGER
# ==============================================================================
@st.cache_resource
def get_pg_pool():
    """Khởi tạo Connection Pool tái sử dụng giữa các lần rerun."""
    return ThreadedConnectionPool(minconn=1, maxconn=10, dsn=RENDER_DB_URL)


@contextmanager
def db_cursor(cursor_factory=None):
    """Context manager tự động lấy kết nối, commit/rollback và trả về pool."""
    pool = get_pg_pool()
    conn = pool.getconn()
    cur = conn.cursor(cursor_factory=cursor_factory)
    try:
        yield cur
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        pool.putconn(conn)


MEDLATEC_ONLINE_TESTS = [
    ("Tổng phân tích tế bào máu ngoại vi (CBC 24 thông số)", "Huyết học", 99000, "Đánh giá hồng cầu, bạch cầu, tiểu cầu, thiếu máu, nhiễm trùng"),
    ("Men gan (AST, ALT, GGT)", "Sinh hóa", 110000, "Kiểm tra tổn thương tế bào gan, viêm gan"),
    ("Chức năng thận (Ure, Creatinin máu)", "Sinh hóa", 95000, "Đánh giá chức năng lọc của cầu thận"),
    ("Bộ mỡ máu toàn phần (Cholesterol, Triglycerid, HDL, LDL)", "Sinh hóa", 180000, "Đánh giá nguy cơ xơ vữa động mạch, tim mạch"),
    ("Đường huyết đói (Glucose) & HbA1c", "Sinh hóa", 165000, "Chẩn đoán và theo dõi bệnh Đái tháo đường"),
    ("Định lượng Axit Uric (Tầm soát Gout)", "Sinh hóa", 65000, "Đánh giá nồng độ axit uric lắng đọng khớp"),
    ("Dấu ấn Ung thư Tiêu hóa (CEA)", "Miễn dịch", 185000, "Tầm soát ung thư đại tràng, dạ dày, thực quản"),
    ("Dấu ấn Ung thư Gan (AFP)", "Miễn dịch", 185000, "Tầm soát và theo dõi ung thư biểu mô tế bào gan"),
    ("Dấu ấn Ung thư Tụy & Đường mật (CA 19-9)", "Miễn dịch", 220000, "Tầm soát ung thư tụy, ống mật, dạ dày"),
    ("Dấu ấn Ung thư Tuyến tiền liệt (PSA toàn phần)", "Miễn dịch", 195000, "Dành cho nam giới tầm soát ung thư tiền liệt tuyến"),
    ("Dấu ấn Ung thư Vú (CA 15-3)", "Miễn dịch", 210000, "Dành cho nữ giới tầm soát ung thư vú"),
    ("Dấu ấn Ung thư Buồng trứng (CA 125)", "Miễn dịch", 210000, "Dành cho nữ giới tầm soát ung thư buồng trứng"),
    ("Dấu ấn Ung thư Phổi (Cyfra 21-1)", "Miễn dịch", 230000, "Tầm soát ung thư phổi không tế bào nhỏ"),
    ("Dấu ấn Ung thư Tuyến giáp (Calcitonin & TG)", "Miễn dịch", 250000, "Tầm soát ung thư tuyến giáp thể tủy"),
    ("Xét nghiệm Tổng phân tích nước tiểu (10 thông số)", "Sinh hóa", 55000, "Kiểm tra đường niệu, đạm niệu, nhiễm khuẩn tiết niệu"),
    ("Điện giải đồ (Na+, K+, Cl-)", "Sinh hóa", 85000, "Đánh giá cân bằng nước điện giải cơ thể"),
    ("Chụp X-quang tim phổi thẳng KTS", "Chẩn đoán hình ảnh", 140000, "Kiểm tra tổn thương nhu mô phổi, tim to"),
    ("Siêu âm ổ bụng tổng quát", "Chẩn đoán hình ảnh", 180000, "Khảo sát gan, mật, tụy, lách, thận, bàng quang"),
    ("Siêu âm tuyến giáp Doppler màu", "Chẩn đoán hình ảnh", 180000, "Phát hiện nhân giáp, nang giáp TIRADS"),
    ("Nội soi dạ dày không đau (gây mê)", "Thăm dò chức năng", 950000, "Quan sát toàn bộ niêm mạc thực quản, dạ dày, tá tràng")
]


def init_all_render_tables():
    """Khởi tạo toàn bộ lược đồ DB nếu chưa tồn tại."""
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_users (
                user_id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                full_name VARCHAR(255) NOT NULL,
                role VARCHAR(50) DEFAULT 'user',
                phone VARCHAR(50),
                email VARCHAR(255),
                age INTEGER DEFAULT 30,
                gender VARCHAR(20) DEFAULT 'Nam',
                avatar TEXT,
                is_vip INTEGER DEFAULT 0,
                bio TEXT DEFAULT 'Yêu thích thể thao, đọc sách y khoa',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS medlatec_registrations (
                id SERIAL PRIMARY KEY,
                user_id INTEGER DEFAULT 1,
                patient_name VARCHAR(255),
                patient_phone VARCHAR(50),
                package TEXT,
                final_price BIGINT DEFAULT 0,
                sample_date VARCHAR(50) DEFAULT '',
                location_type VARCHAR(50) DEFAULT 'Tận nhà',
                address TEXT DEFAULT 'Theo hồ sơ',
                doctor_indications TEXT,
                indications_extra_price BIGINT DEFAULT 0,
                status VARCHAR(50) DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS doctor_appointments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER DEFAULT 1,
                patient_name VARCHAR(255),
                patient_phone VARCHAR(50),
                date VARCHAR(50),
                time VARCHAR(50),
                type VARCHAR(100),
                notes TEXT,
                status VARCHAR(50) DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS consultation_sessions (
                session_id SERIAL PRIMARY KEY,
                user_id INTEGER DEFAULT 1,
                initial_symptoms TEXT,
                followup_answers TEXT,
                final_assessment TEXT,
                top_icd10 VARCHAR(50),
                status VARCHAR(50) DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_prescriptions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                medicine_name VARCHAR(255) NOT NULL,
                dosage VARCHAR(100) NOT NULL,
                alarm_time VARCHAR(50) NOT NULL,
                total_quantity VARCHAR(100) DEFAULT '10 viên',
                start_date VARCHAR(50) DEFAULT '',
                instructions TEXT,
                source VARCHAR(100) DEFAULT 'Nhập thủ công',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS health_vitals (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                systolic INTEGER DEFAULT 0,
                diastolic INTEGER DEFAULT 0,
                heart_rate INTEGER DEFAULT 0,
                weight REAL DEFAULT 0,
                height REAL DEFAULT 0,
                bmi REAL DEFAULT 0,
                glucose REAL DEFAULT 0,
                recorded_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS supplements_catalog (
                id SERIAL PRIMARY KEY,
                icon VARCHAR(20) DEFAULT '🌿',
                name VARCHAR(255) NOT NULL,
                usage TEXT NOT NULL,
                original_price BIGINT NOT NULL,
                discount_percent INTEGER DEFAULT 10,
                final_price BIGINT NOT NULL,
                image_url TEXT DEFAULT ''
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_orders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                customer_name VARCHAR(255),
                customer_phone VARCHAR(50),
                items TEXT NOT NULL,
                total_amount BIGINT NOT NULL,
                address TEXT,
                status VARCHAR(50) DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key_name VARCHAR(100) PRIMARY KEY,
                key_value TEXT NOT NULL
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS medical_knowledge_base (
                id SERIAL PRIMARY KEY,
                disease_keyword VARCHAR(255) UNIQUE NOT NULL,
                icd_code VARCHAR(50),
                official_guideline TEXT NOT NULL,
                source_url VARCHAR(255) DEFAULT 'kcb.vn - Bo Y Te',
                learned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS medlatec_tests_catalog (
                test_id SERIAL PRIMARY KEY,
                test_name VARCHAR(255) NOT NULL,
                category VARCHAR(100) DEFAULT 'Sinh hóa / Huyết học',
                unit_price BIGINT NOT NULL,
                description TEXT
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_media_assets (
                asset_key VARCHAR(100) PRIMARY KEY,
                asset_name VARCHAR(255) NOT NULL,
                image_base64 TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("SELECT COUNT(*) FROM supplements_catalog")
        if cur.fetchone()[0] == 0:
            default_tpcn = [
                ("🌿", "Curcumin Nano Tinh Khiết 98%", "Chống oxy hóa mạnh mẽ, hỗ trợ ức chế tế bào ung thư đường tiêu hóa, dạ dày và đại tràng.", 550000, 15, 467500, ""),
                ("🛡️", "Fucoidan Mozuku Nhật Bản Siêu Đậm Đặc", "Kích hoạt chu trình tự diệt tế bào bất thường (Apoptosis), tăng cường hệ miễn dịch tự nhiên.", 1200000, 20, 960000, ""),
                ("🫀", "Coenzyme Q10 & Omega-3 Tinh Dầu Nhuyễn Thể", "Bảo vệ tế bào cơ tim, ngăn ngừa tổn thương oxy hóa màng tế bào, hỗ trợ phòng đột quỵ.", 680000, 10, 612000, "")
            ]
            cur.executemany("INSERT INTO supplements_catalog (icon, name, usage, original_price, discount_percent, final_price, image_url) VALUES (%s, %s, %s, %s, %s, %s, %s)", default_tpcn)

        cur.execute("SELECT COUNT(*) FROM medlatec_tests_catalog")
        if cur.fetchone()[0] == 0:
            cur.executemany("INSERT INTO medlatec_tests_catalog (test_name, category, unit_price, description) VALUES (%s, %s, %s, %s)", MEDLATEC_ONLINE_TESTS)


@st.cache_resource
def _run_migrations_once():
    init_all_render_tables()
    return True

_run_migrations_once()


# ==============================================================================
# HÀM TRUY VẤN VÀ BỘ NHỚ ĐỆM (CACHING)
# ==============================================================================
@st.cache_data(ttl=600)
def get_system_email_config():
    """Lưu cache cấu hình email hệ thống trong 10 phút."""
    config = {}
    try:
        with db_cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_settings")
            for k, v in cur.fetchall():
                config[k] = v
    except Exception as e:
        print(f"Lỗi đọc cấu hình email: {e}")
    return config


@st.cache_data(ttl=600)
def get_cached_tests_catalog():
    """Lưu cache danh mục xét nghiệm 10 phút."""
    try:
        with db_cursor() as cur:
            cur.execute("SELECT test_id, test_name, category, unit_price FROM medlatec_tests_catalog ORDER BY test_id ASC")
            return cur.fetchall()
    except Exception:
        return []


@st.cache_data(ttl=300)
def get_cached_supplements():
    """Lưu cache danh mục TPCN 5 phút."""
    try:
        with db_cursor() as cur:
            cur.execute("SELECT id, icon, name, usage, original_price, discount_percent, final_price, image_url FROM supplements_catalog ORDER BY id DESC")
            return [
                {"id": r[0], "icon": r[1], "name": r[2], "usage": r[3], "original_price": r[4], "discount_percent": r[5], "final_price": r[6], "image_url": r[7]}
                for r in cur.fetchall()
            ]
    except Exception:
        return []


def send_otp_email(recipient_email: str, otp_code: str, action_name: str = "Xác thực tài khoản"):
    """Gửi mã OTP bảo mật."""
    email_cfg = get_system_email_config()
    sender_email = email_cfg.get("SMTP_EMAIL") or email_cfg.get("SENDER_EMAIL") or "toanbvtimhn@gmail.com"
    sender_pass = email_cfg.get("SMTP_PASSWORD") or email_cfg.get("SENDER_APP_PASSWORD")

    if not sender_pass:
        return False, "Không tìm thấy cấu hình gửi mail trên cơ sở dữ liệu Render."

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{otp_code}] Ma OTP {action_name} - Smart Hospital AI"
        msg["From"] = f"He Thong MedAI <{sender_email}>"
        msg["To"] = recipient_email

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 500px; margin: auto; padding: 20px; border: 1px solid #E2E8F0; border-radius: 12px; background: #F8FAFC;">
            <h2 style="color: #0F3D64; text-align: center;">&#127973; HỆ THỐNG Y TẾ THÔNG MINH</h2>
            <p style="font-size: 14px; color: #334155;">Xin chào quý khách,</p>
            <p style="font-size: 14px; color: #334155;">Bạn đang thực hiện <b>{action_name}</b>. Dưới đây là mã xác thực OTP của bạn:</p>
            <div style="text-align: center; margin: 24px 0;">
                <span style="font-size: 30px; font-weight: 900; letter-spacing: 6px; color: #1976D2; background: #E0F2FE; padding: 10px 24px; border-radius: 8px; border: 1px dashed #0284C7; display: inline-block;">
                    {otp_code}
                </span>
            </div>
            <p style="font-size: 12.5px; color: #64748B;">Mã này có hiệu lực trong vòng <b>5 phút</b>.</p>
        </div>
        """
        msg.attach(MIMEText(html_content, "html", "utf-8"))
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15)
        server.login(sender_email, sender_pass.replace(" ", ""))
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()
        return True, "Đã gửi mã OTP thành công về Gmail!"
    except Exception as e:
        return False, f"Lỗi gửi email: {e}"


@st.cache_data(ttl=3600, show_spinner=False)
def get_render_media_asset(asset_key: str, local_file_path: str = "") -> str:
    """Lưu cache ảnh 1 tiếng, không truy vấn lại DB mỗi lần rerun."""
    img_b64 = ""
    try:
        with db_cursor() as cur:
            cur.execute("SELECT image_base64 FROM app_media_assets WHERE asset_key = %s", (asset_key,))
            row = cur.fetchone()
            if row and row[0]:
                img_b64 = row[0]
            elif local_file_path and os.path.exists(local_file_path):
                with open(local_file_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode()
                cur.execute("""
                    INSERT INTO app_media_assets (asset_key, asset_name, image_base64)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (asset_key) DO UPDATE SET image_base64 = EXCLUDED.image_base64
                """, (asset_key, os.path.basename(local_file_path), img_b64))
    except Exception as e:
        print(f"Lỗi lấy media asset {asset_key}: {e}")
    return img_b64


@st.cache_data(ttl=60)
def get_cached_users_from_render():
    users_dict = {}
    profiles_dict = {}
    try:
        with db_cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT user_id, username, password, full_name, role, phone, email, age, gender, avatar, is_vip, bio FROM app_users")
            rows = cur.fetchall()

            for r in rows:
                u_id = r["user_id"]
                u_name = r["username"]
                u_data = {
                    "user_id": u_id, "username": u_name, "password": r["password"],
                    "full_name": r["full_name"], "role": r["role"], "phone": r["phone"] or "",
                    "email": r["email"] or "", "is_vip": bool(r["is_vip"]), "bio": r["bio"] or ""
                }
                users_dict[u_name] = u_data
                if r["phone"]:
                    users_dict[r["phone"]] = u_data

                profiles_dict[u_id] = {
                    "full_name": r["full_name"], "age": r["age"] or 30, "gender": r["gender"] or "Nam",
                    "phone": r["phone"] or "", "email": r["email"] or "", "username": u_name,
                    "avatar": r["avatar"], "is_vip": bool(r["is_vip"]), "bio": r["bio"] or "Yêu thích thể thao, đọc sách y khoa"
                }
    except Exception as e:
        print(f"Lỗi đọc DB Render: {e}")
    return users_dict, profiles_dict


def get_all_users_from_db():
    return get_cached_users_from_render()


@st.cache_data(ttl=45)
def get_system_notifications(user_role: str, user_id: int):
    """Truy vấn thông báo có cache TTL ngắn (45s)."""
    notifs = []
    try:
        with db_cursor() as cur:
            if user_role in ["super_admin", "sub_admin"]:
                cur.execute("SELECT full_name, created_at FROM app_users ORDER BY user_id DESC LIMIT 3")
                for name, c_date in cur.fetchall():
                    notifs.append({
                        "icon": "👤", "title": "Bệnh nhân mới đăng ký",
                        "desc": f"Bệnh nhân **{name}** vừa tạo tài khoản.",
                        "time": str(c_date)[11:16] if c_date else "Vừa xong",
                        "target_nav": "⚙️ Quản Trị Hệ Thống & Phân Quyền"
                    })

                cur.execute("SELECT patient_name, date, time FROM doctor_appointments ORDER BY id DESC LIMIT 3")
                for p_name, a_date, a_time in cur.fetchall():
                    notifs.append({
                        "icon": "📅", "title": "Lịch hẹn mới",
                        "desc": f"Bệnh nhân **{p_name}** hẹn lúc {a_time} ngày {a_date}.",
                        "time": a_date,
                        "target_nav": "⚙️ Quản Trị Hệ Thống & Phân Quyền"
                    })

                cur.execute("SELECT customer_name, total_amount FROM product_orders ORDER BY id DESC LIMIT 3")
                for c_name, t_amt in cur.fetchall():
                    notifs.append({
                        "icon": "📦", "title": "Đơn hàng TPCN mới",
                        "desc": f"Khách hàng **{c_name}** đặt đơn {t_amt:,} đ.",
                        "time": "Mới",
                        "target_nav": "⚙️ Quản Trị Hệ Thống & Phân Quyền"
                    })
            else:
                cur.execute("SELECT medicine_name, alarm_time FROM user_prescriptions WHERE user_id = %s ORDER BY id ASC LIMIT 3", (user_id,))
                for m_name, a_time in cur.fetchall():
                    notifs.append({
                        "icon": "💊", "title": "Nhắc nhở uống thuốc",
                        "desc": f"Đến giờ uống **{m_name}** lúc {a_time}.",
                        "time": a_time,
                        "target_nav": "🧑‍⚕️Khám Bệnh Online (Trợ Lý Y Tế)"
                    })
    except Exception:
        pass
    return notifs


CSS_STYLES = r"""
<style>
    /* Giữ header mặc định để nút mở sidebar hoạt động chuẩn xác */
    header[data-testid="stHeader"] {
        background: transparent !important;
        visibility: visible !important;
        display: block !important;
    }

    /* Chỉ ẩn menu 3 chấm, nút Manage app và Footer */
    #MainMenu, 
    footer,
    div[data-testid="stStatusWidget"],
    .stDeployButton {
        visibility: hidden !important;
        display: none !important;
    }

    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0.8rem !important;
        padding-left: 1.2rem !important;
        padding-right: 1.2rem !important;
        max-width: 100% !important;
    }

    /* ==============================================================
       SIDEBAR: TỰ NHIÊN, KHÔNG KHÓA CỨNG WIDTH, ĐÓNG/MỞ TRƠN TRU
       ============================================================== */
    .sidebar-menu-title {
        display: block !important;
        color: #7DD3FC !important;
        font-size: 15px !important;
        font-weight: 800 !important;
        letter-spacing: 1.2px !important;
        text-transform: uppercase !important;
        padding: 10px 8px 6px 8px !important;
        opacity: 0.9 !important;
    }
    
    [data-testid="stSidebar"] { 
        background: linear-gradient(180deg, #07233E 0%, #0A3258 60%, #061B2F 100%) !important; 
        border-right: 0 !important; 
    }

    [data-testid="stSidebar"] > div:first-child {
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] {
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] > button {
        width: 100% !important;
        display: flex !important;
        justify-content: flex-start !important;
        align-items: center !important;
        text-align: left !important;
        border-radius: 10px !important;
        padding: 8px 12px !important;
        margin-bottom: 4px !important;
        height: auto !important;
        border: 1px solid transparent !important;
        box-sizing: border-box !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] > button p,
    [data-testid="stSidebar"] div[data-testid="stButton"] > button div,
    [data-testid="stSidebar"] div[data-testid="stButton"] > button span {
        display: block !important;
        width: 100% !important;
        text-align: left !important;
        white-space: nowrap !important;
        font-size: 12.5px !important;
        font-weight: 600 !important;
        line-height: 1.3 !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"] {
        background: transparent !important;
        color: #93C5FD !important;
    }
    [data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"]:hover {
        background: rgba(255, 255, 255, 0.08) !important;
        color: #FFFFFF !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"] {
        background: #0284C7 !important;
        color: #FFFFFF !important;
        box-shadow: 0 4px 14px rgba(2, 132, 199, 0.35) !important;
        font-weight: 700 !important;
    }
    [data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"] p {
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }

    .sidebar-custom-divider {
        border-top: 1px solid rgba(255, 255, 255, 0.12);
        margin: 18px 4px 14px 4px;
    }
    .sidebar-quote-box {
        padding: 4px 8px 10px 8px;
        color: #94A3B8;
        font-size: 12px;
        font-style: italic;
        line-height: 1.45;
    }
    .ecg-line-svg {
        width: 100%;
        height: 20px;
        margin-top: 6px;
        stroke: #38BDF8;
        opacity: 0.8;
    }
    .user-badge-bottom {
        background: rgba(255, 255, 255, 0.06) !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        padding: 10px 12px !important;
        border-radius: 12px !important;
        font-size: 11.5px !important;
        color: #E2E8F0 !important;
        margin-top: 16px !important;
        margin-bottom: 8px !important;
    }
    .user-badge-bottom b { color: #FFFFFF !important; font-size: 13px !important; display: block; margin-bottom: 2px; }
    .user-badge-bottom small { color: #38BDF8 !important; font-weight: 600; }
    
    .stPlotlyChart {
        background: #FFFFFF !important;
        border: 1px solid #E2ECF5 !important;
        border-radius: 20px !important;
        box-shadow: 0 4px 18px rgba(15, 60, 100, 0.04) !important;
        overflow: hidden !important;
    }
    .stPlotlyChart > div {
        border-radius: 20px !important;
        overflow: hidden !important;
    }

    /* BANNER BÁC SĨ */
    .hero-banner-compact {
        position: relative !important;
        width: 100% !important;
        aspect-ratio: 1000 / 270 !important;
        height: auto !important;
        min-height: 155px !important;
        max-height: 200px !important;
        border-radius: 18px !important;
        background-size: 100% 100% !important;
        background-position: center !important;
        background-repeat: no-repeat !important;
        box-shadow: 0 4px 18px rgba(2, 132, 199, 0.07) !important;
        padding: 22px 28px !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
        margin-bottom: 12px !important;
    }

    .hero-banner-compact-title {
        font-size: 22px !important;
        font-weight: 900 !important;
        color: #0C3861 !important;
        letter-spacing: -0.4px !important;
        line-height: 1.2 !important;
    }

    .hero-banner-compact-sub {
        font-size: 13px !important;
        color: #334155 !important;
        margin-top: 5px !important;
        font-weight: 600 !important;
    }

    /* CƠ BẢN TOÀN TRANG */
    .stApp { background:#F4F8FC; font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif; }
    [data-testid="stAppViewContainer"] { background:#F4F8FC; }
    .ticker-wrap {
        width: 100%; 
        overflow: hidden; 
        background: linear-gradient(90deg, #0F3D64 0%, #1D5B8C 50%, #0F3D64 100%);
        border-radius: 10px; 
        padding: 9px 12px; 
        margin-bottom: 16px; 
        box-shadow: 0 3px 10px rgba(15, 61, 100, 0.15); 
        border-left: 4px solid #F59E0B;
        position: relative;
        z-index: 1;
    }
    .ticker-text { font-size: 13.5px; font-weight: 600; color: #FFFFFF; letter-spacing: 0.3px; }
    .ticker-highlight { color: #FDE047; font-weight: 700; }
    
    .dashboard-card { background: #FFFFFF; border: 1px solid #E2ECF5; border-radius: 16px; padding: 18px 20px; box-shadow: 0 4px 16px rgba(20, 70, 110, 0.03); margin-bottom: 16px; }
    .card-title { font-size: 15px; font-weight: 800; color: #12385C; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
    
    .summary-card { 
        background: #FFFFFF; 
        border: 1px solid #E2ECF5; 
        border-radius: 14px; 
        padding: 9px 12px; 
        display: flex; 
        align-items: center; 
        gap: 10px; 
        box-shadow: 0 2px 8px rgba(20, 70, 110, 0.02); 
        margin-bottom: 14px;
    }
    .summary-icon { 
        width: 36px; 
        height: 36px; 
        border-radius: 10px; 
        display: flex; 
        align-items: center; 
        justify-content: center; 
        font-size: 17px; 
        flex-shrink: 0;
    }
    .summary-label { font-size: 11px; color: #71889D; font-weight: 600; line-height: 1.1; }
    .summary-val { font-size: 15px; font-weight: 800; color: #0E355B; margin: 2px 0 1px 0; line-height: 1.2; }
    .summary-sub { font-size: 10px; color: #8CA0B2; line-height: 1.1; }
    .status-badge { background: #E8F8F0; color: #109655; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 20px; }
    .slogan-footer { background: linear-gradient(135deg, #1E3A8A 0%, #0D9488 100%); color: #FFFFFF; text-align: center; padding: 20px; border-radius: 12px; font-size: 20px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; margin-top: 35px; box-shadow: 0 4px 12px rgba(30, 58, 138, 0.15); }

    /* ==============================================================
       KHỐI DỊCH VỤ Y TẾ NHANH
       ============================================================== */
    .tight-service-box div[data-testid="stButton"] > button {
        background: #FFFFFF !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 10px !important;
        height: 46px !important;
        width: 100% !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 4px 8px !important;
        box-shadow: 0 1px 3px rgba(15, 60, 100, 0.04) !important;
    }
    .tight-service-box div[data-testid="stButton"] > button:hover {
        background: #F0F9FF !important;
        border-color: #0284C7 !important;
    }
    .tight-service-box div[data-testid="stButton"] > button p {
        font-size: 12.5px !important;
        font-weight: 700 !important;
        color: #0F172A !important;
        margin: 0 !important;
        white-space: nowrap !important;
    }

    /* ==============================================================
       DANH SÁCH TIN TỨC GỌN GÀNG (ẢNH 42PX, CHỮ BÁM SÁT)
       ============================================================== */
    .news-list-flex-container {
        display: flex;
        flex-direction: column;
        width: 100%;
        margin-top: 2px;
    }

    .news-item-row {
        display: flex !important;
        align-items: center !important;
        gap: 10px !important;
        padding: 5px 6px !important;
        text-decoration: none !important;
        border-radius: 8px !important;
        transition: background 0.15s ease !important;
        width: 100% !important;
        box-sizing: border-box !important;
    }

    .news-item-row:hover {
        background-color: #F1F5F9 !important;
    }

    .news-item-thumb {
        width: 42px !important;
        height: 42px !important;
        min-width: 42px !important;
        min-height: 42px !important;
        border-radius: 8px !important;
        object-fit: cover !important;
        flex-shrink: 0 !important;
        display: block !important;
    }

    .news-item-content {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        flex: 1 1 auto !important;
        min-width: 0 !important;
    }

    .news-item-title {
        font-size: 12.5px !important;
        font-weight: 700 !important;
        color: #0F172A !important;
        line-height: 1.35 !important;
        margin: 0 0 2px 0 !important;
        display: -webkit-box !important;
        -webkit-line-clamp: 2 !important;
        -webkit-box-orient: vertical !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        text-align: left !important;
    }

    .news-item-row:hover .news-item-title {
        color: #0284C7 !important;
    }

    .news-item-date {
        font-size: 10.5px !important;
        color: #94A3B8 !important;
        font-weight: 500 !important;
        text-align: left !important;
    }

    .news-item-divider {
        border-bottom: 1px solid #F1F5F9;
        margin: 2px 4px 4px 4px;
    }
    /* ==============================================================
       LÀM SÁNG NỔI BẬT NÚT THU NHỎ SIDEBAR (<<) TRÊN NỀN TỐI
       ============================================================== */
    /* 1. Đổi màu nền nút và tạo khung bo góc nhẹ */
    [data-testid="stSidebarCollapseButton"] button,
    [data-testid="stSidebar"] button[kind="header"],
    button[aria-label="Close sidebar"] {
        background: rgba(255, 255, 255, 0.15) !important;
        border-radius: 8px !important;
        color: #38BDF8 !important;
    }

    [data-testid="stSidebarCollapseButton"] button:hover,
    [data-testid="stSidebar"] button[kind="header"]:hover,
    button[aria-label="Close sidebar"]:hover {
        background: #0284C7 !important;
        color: #FFFFFF !important;
    }

    /* 2. Ép trực tiếp SVG mũi tên chuyển sang màu xanh sáng / trắng */
    [data-testid="stSidebarCollapseButton"] svg,
    [data-testid="stSidebar"] button[kind="header"] svg,
    button[aria-label="Close sidebar"] svg {
        fill: #38BDF8 !important;
        stroke: #38BDF8 !important;
        color: #38BDF8 !important;
        filter: drop-shadow(0 0 2px rgba(56, 189, 248, 0.6)) brightness(1.8) !important;
    }

    [data-testid="stSidebarCollapseButton"] button:hover svg,
    [data-testid="stSidebar"] button[kind="header"]:hover svg,
    button[aria-label="Close sidebar"]:hover svg {
        fill: #FFFFFF !important;
        stroke: #FFFFFF !important;
        color: #FFFFFF !important;
        filter: none !important;
    }
</style>
"""
st.markdown(CSS_STYLES, unsafe_allow_html=True)

# Tự động gỡ bỏ cờ ghi nhớ trạng thái đóng Sidebar trong trình duyệt
st.components.v1.html("""
<script>
    try {
        window.parent.localStorage.removeItem('stSidebar.isCollapsed');
        const btn = window.parent.document.querySelector('[data-testid="stSidebarCollapsedControl"] button') 
                 || window.parent.document.querySelector('[data-testid="collapsedControl"] button')
                 || window.parent.document.querySelector('header button');
        if (btn) {
            btn.click();
        }
    } catch (e) {}
</script>
""", height=0, width=0)

# Ép Sidebar tự động mở ra và tạo nút dự phòng nếu bị ẩn
st.html("""
<script>
    function forceExpandSidebar() {
        // Tìm và tự động kích hoạt nút mở sidebar nếu đang bị ẩn
        const collapsedBtn = window.parent.document.querySelector('[data-testid="stSidebarCollapsedControl"] button') 
                          || window.parent.document.querySelector('[data-testid="collapsedControl"] button');
        if (collapsedBtn) {
            collapsedBtn.click();
        }
        // Xóa cờ trạng thái thu gọn bị lưu cứng trong trình duyệt
        window.parent.localStorage.removeItem('stSidebar.isCollapsed');
    }
    setTimeout(forceExpandSidebar, 200);
</script>
""")

# ==============================================================================
# 1. KHỞI TẠO ĐẦY ĐỦ BỘ NHỚ TRẠNG THÁI (BẮT BUỘC KHỞI TẠO AUTH_USER ĐẦU TIÊN)
# ==============================================================================
if "users_db" not in st.session_state or "profiles_dict" not in st.session_state:
    st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()

# Dòng quan trọng nhất để sửa lỗi AttributeError:
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if "active_user_id" not in st.session_state:
    st.session_state.active_user_id = 1

if "main_navigation" not in st.session_state:
    st.session_state.main_navigation = "🏠 Tổng quan sức khỏe"

if "users_db" not in st.session_state or "profiles_dict" not in st.session_state:
    st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()

if "current_reading_article_id" not in st.session_state:
    st.session_state.current_reading_article_id = None

if "reg_otp_step" not in st.session_state:
    st.session_state.reg_otp_step = False

if "reg_temp_data" not in st.session_state:
    st.session_state.reg_temp_data = {}

if "reg_otp_code" not in st.session_state:
    st.session_state.reg_otp_code = None

if "step1_data" not in st.session_state:
    st.session_state.step1_data = None

if "final_conclusion" not in st.session_state:
    st.session_state.final_conclusion = None

if "cart_items" not in st.session_state:
    st.session_state.cart_items = {}

# ==============================================================================
# MÀN HÌNH ĐĂNG NHẬP / ĐĂNG KÝ
# ==============================================================================
st.markdown(
    """
    <div class="ticker-wrap">
        <marquee direction="left" scrollamount="7" onmouseover="this.stop();" onmouseout="this.start();">
            <span class="ticker-text">
                ⚠️ <span class="ticker-highlight">LƯU Ý Y KHOA:</span> TẤT CẢ CÁC GỢI Ý ĐỀU MANG TÍNH CHẤT THAM KHẢO, KHÔNG THAY THẾ VAI TRÒ CỦA BÁC SĨ LÂM SÀNG. 
                &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;✦&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 
                🏛️ <span class="ticker-highlight">CHUẨN HÓA:</span> TẤT CẢ CÁC GỢI Ý, PHÁC ĐỒ ĐIỀU TRỊ ĐỀU ĐƯỢC SỬ DỤNG TỪ TÀI LIỆU CỦA BỘ Y TẾ VIỆT NAM, ĐÃ ĐƯỢC THAM KHẢO BỞI CÁC BÁC SỸ ĐẦU NGÀNH.
                &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;✦&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 
                ⚙️<span class="ticker-highlight">:</span> SỨC KHỎE LÀ TÀI SẢN QUÝ GIÁ NHẤT CỦA CON NGƯỜI, HÃY TRÂN TRỌNG TỪNG PHÚT KHI CÓ THỂ.
            </span>
        </marquee>
    </div>
    """,
    unsafe_allow_html=True
)

if not st.session_state.auth_user:
    st.markdown("<div style='font-size:24px; font-weight:800; color:#0C3861; text-align:center; margin-bottom:6px;'>🩺 HỆ THỐNG TRỢ LÝ Y TẾ CÁ NHÂN & CHĂM SÓC SỨC KHỎE</div>", unsafe_allow_html=True)
    st.markdown("<div style='font-size:13px; font-weight:600; color:#475569; text-align:center; margin-bottom:20px;'>👨‍⚕️ Tác giả phần mềm: DR. Nguyễn Tiến Toàn | ✉️ Email: toanbvtimhn@gmail.com</div>", unsafe_allow_html=True)

    col_l1, col_l2 = st.columns([1, 1])

    with col_l1:
        st.subheader("🔑 Đăng Nhập Hệ Thống")
        with st.form("login_form_direct"):
            l_user = st.text_input("Tên đăng nhập / Số điện thoại:", placeholder="Nhập tên tài khoản hoặc SĐT...")
            l_pass = st.text_input("Mật khẩu:", type="password", placeholder="Nhập mật khẩu...")
            btn_login = st.form_submit_button("🚀 Đăng Nhập", width="stretch")

        if btn_login:
            input_account = l_user.strip()
            input_password = l_pass.strip()
            
            if not input_account or not input_password:
                st.warning("Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.")
            else:
                st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()
                user_info = st.session_state.users_db.get(input_account)
                
                if user_info and user_info["password"] == input_password:
                    st.session_state.auth_user = user_info
                    st.session_state.active_user_id = user_info["user_id"]
                    st.toast(f"🎉 Đăng nhập thành công! Quyền: {user_info.get('role')}")
                    st.rerun()
                else:
                    st.error("❌ Tên đăng nhập hoặc mật khẩu không chính xác.")

    with col_l2:
        st.subheader("📝 Đăng Ký Tài Khoản Mới")
        if not st.session_state.reg_otp_step:
            with st.form("reg_form_step1"):
                r_name = st.text_input("Họ và tên bệnh nhân:")
                r_user = st.text_input("Tên đăng nhập mong muốn:")
                r_pass = st.text_input("Mật khẩu (từ 6 ký tự trở lên):", type="password")
                c_p, c_e = st.columns(2)
                with c_p:
                    r_phone = st.text_input("Số điện thoại:")
                with c_e:
                    r_email = st.text_input("Gmail nhận OTP:")
                c_a, c_g = st.columns(2)
                with c_a:
                    r_age = st.number_input("Tuổi:", min_value=1, max_value=120, value=30)
                with c_g:
                    r_gender = st.selectbox("Giới tính:", ["Nam", "Nữ", "Khác"])
                btn_send_reg_otp = st.form_submit_button("📩 Gửi Mã Xác Thực OTP Về Gmail", width="stretch")

            if btn_send_reg_otp:
                st.session_state.users_db, _ = get_all_users_from_db()
                if not (r_name.strip() and r_user.strip() and r_pass.strip() and r_phone.strip() and r_email.strip()):
                    st.warning("Vui lòng điền đầy đủ thông tin.")
                elif len(r_pass.strip()) < 6:
                    st.error("Mật khẩu bắt buộc từ 6 ký tự trở lên.")
                elif "@" not in r_email.strip():
                    st.error("Email không hợp lệ.")
                elif r_user.strip() in st.session_state.users_db:
                    st.error("Tên đăng nhập này đã được sử dụng.")
                else:
                    otp_reg = f"{random.randint(100000, 999999)}"
                    with st.spinner(f"Đang gửi mã xác thực tới {r_email.strip()}..."):
                        ok, msg = send_otp_email(r_email.strip(), otp_reg, "Đăng ký tài khoản mới")
                        if ok:
                            st.session_state.reg_otp_code = otp_reg
                            st.session_state.reg_temp_data = {
                                "name": r_name.strip(), "user": r_user.strip(), "pass": r_pass.strip(),
                                "phone": r_phone.strip(), "email": r_email.strip(), "age": r_age, "gender": r_gender
                            }
                            st.session_state.reg_otp_step = True
                            st.rerun()
                        else:
                            st.error(f"❌ {msg}")
        else:
            reg_d = st.session_state.reg_temp_data
            st.info(f"📩 Mã xác nhận bảo mật gửi tới: **{reg_d['email']}**")
            with st.form("reg_form_step2"):
                inp_reg_otp = st.text_input("Nhập mã OTP 6 chữ số:", max_chars=6)
                c_reg1, c_reg2 = st.columns([1.5, 1])
                with c_reg1:
                    btn_finish_reg = st.form_submit_button("🎉 Xác Nhận Tạo Tài Khoản")
                with c_reg2:
                    btn_cancel_reg = st.form_submit_button("❌ Hủy bỏ")

            if btn_cancel_reg:
                st.session_state.reg_otp_step = False
                st.session_state.reg_temp_data = {}
                st.session_state.reg_otp_code = None
                st.rerun()

            if btn_finish_reg:
                if inp_reg_otp.strip() == st.session_state.reg_otp_code:
                    try:
                        with db_cursor() as cur_reg:
                            cur_reg.execute("""
                                INSERT INTO app_users (username, password, full_name, phone, email, age, gender)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """, (reg_d['user'], reg_d['pass'], reg_d['name'], reg_d['phone'], reg_d['email'], reg_d['age'], reg_d['gender']))

                        get_cached_users_from_render.clear()
                        st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()
                        st.session_state.reg_otp_step = False
                        st.session_state.reg_temp_data = {}
                        st.session_state.reg_otp_code = None
                        st.success("🎉 Đăng ký thành công và đã lưu an toàn trên Render! Vui lòng đăng nhập.")
                    except Exception as ex:
                        st.error(f"❌ Lỗi ghi nhận tài khoản lên Render: {ex}")
                else:
                    st.error("❌ Mã OTP không chính xác.")
    st.stop()


# ==============================================================================
# SIDEBAR
# ==============================================================================
ROLE = st.session_state.auth_user["role"]
IS_SUPER_ADMIN = ROLE == "super_admin"
IS_SUB_ADMIN = ROLE == "sub_admin"
IS_ANY_ADMIN = IS_SUPER_ADMIN or IS_SUB_ADMIN
CURRENT_USERNAME = st.session_state.auth_user.get("username", "")

available_ids = (
    list(st.session_state.profiles_dict.keys())
    if IS_ANY_ADMIN
    else [uid for uid, prof in st.session_state.profiles_dict.items() if prof.get("username") == CURRENT_USERNAME]
)
if not available_ids:
    available_ids = [st.session_state.auth_user["user_id"]]

if st.session_state.active_user_id not in available_ids:
    st.session_state.active_user_id = available_ids[0]

USER_ID = st.session_state.active_user_id
current_prof = st.session_state.profiles_dict.get(USER_ID, {
    "full_name": st.session_state.auth_user.get("full_name", "Chưa có tên"),
    "age": 30, "gender": "Nam", "phone": "", "email": "", "avatar": None, "is_vip": False
})

BLANK_AVATAR = "https://cdn-icons-png.flaticon.com/512/847/847969.png"
avatar_display = current_prof.get("avatar") or BLANK_AVATAR

with st.sidebar:
    logo_b64 = get_render_media_asset("app_logo", "app_logo.png")
    if logo_b64:
        img_logo_tag = f'<img src="data:image/png;base64,{logo_b64}" style="width:36px; height:auto; object-fit:contain;">'
    else:
        img_logo_tag = '<span style="font-size:26px;">🛡️</span>'

    st.markdown(f"""
        <div style="display: flex; align-items: center; gap: 10px; padding: 4px 6px 12px 6px;">
            {img_logo_tag}
            <div>
                <div style="font-size: 15px; font-weight: 900; color: #FFFFFF; letter-spacing: 0.4px; line-height: 1.15; text-transform: uppercase;">
                    BỆNH VIỆN THÔNG MINH
                </div>
                <div style="font-size: 9.5px; font-weight: 700; color: #38BDF8; letter-spacing: 1px; margin-top: 2px; text-transform: uppercase;">
                    SMART HOSPITAL AI SYSTEM
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("<span class='sidebar-menu-title'>DANH MỤC</span>", unsafe_allow_html=True)

    nav_options = [
        "🏠 Tổng quan sức khỏe",
        "📰 Tin tức & Khuyến cáo y tế",
        "🧑‍⚕️Khám Bệnh Online (Trợ Lý Y Tế)",
        "🩺 Khám Sức Khỏe & Đặc Quyền VIP",
        "🚨 Cấp Cứu 115 & Sơ Cứu Tại Chỗ",
        "🌿 TPCN phòng ngừa ung thư",
    ]
    if IS_ANY_ADMIN:
        nav_options.append("⚙️ Quản Trị Hệ Thống & Phân Quyền")

    # Hiển thị từng mục menu dưới dạng nút bấm phẳng (loại bỏ hoàn toàn widget radio và nút tròn)
    for nav_item in nav_options:
        is_active = (st.session_state.main_navigation == nav_item)
        btn_type = "primary" if is_active else "secondary"
        if st.button(nav_item, key=f"nav_btn_{nav_item}", type=btn_type, width="stretch"):
            st.session_state.main_navigation = nav_item
            
            # Reset toàn bộ các trạng thái xem chi tiết / màn hình con về mặc định
            st.session_state.current_reading_article_id = None
            st.session_state.step1_data = None
            st.session_state.final_conclusion = None
            if "uploaded_medical_images" in st.session_state:
                st.session_state.uploaded_medical_images = []
            if "inquiry_chat_history" in st.session_state:
                st.session_state.inquiry_chat_history = []
                
            st.rerun()

    st.markdown("<div class='sidebar-custom-divider'></div>", unsafe_allow_html=True)

    st.markdown("""
        <div class="sidebar-quote-box">
            „ Sức khỏe là tài sản<br>quý giá nhất của con người. ”
            <svg class="ecg-line-svg" viewBox="0 0 200 30" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M0 15 H50 L58 4 L66 26 L74 8 L82 22 L90 15 H200" stroke="#38BDF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
        </div>
    """, unsafe_allow_html=True)

    role_label = (
        "👑 Tổng Quản Trị Viên (DR. Nguyễn Tiến Toàn)"
        if IS_SUPER_ADMIN
        else ("🛡️ Quản Trị Viên Cấp Dưới (Sub-Admin)" if IS_SUB_ADMIN else "👤 Bệnh Nhân / Người Dùng")
    )

    st.markdown(
        f"""
        <div class="user-badge-bottom">
            <b>{st.session_state.auth_user['full_name']}</b>
            <small>Quyền: {role_label}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("🚪 Đăng Xuất", width="stretch"):
        st.session_state.auth_user = None
        st.session_state.cart_items = {}
        st.rerun()

    saved_key = st.session_state.get("GEMINI_API_KEY", "")
    if not saved_key:
        try:
            with db_cursor() as cur_k:
                cur_k.execute("SELECT key_value FROM system_settings WHERE key_name = 'GEMINI_API_KEY'")
                row_k = cur_k.fetchone()
                if row_k and row_k[0]:
                    saved_key = row_k[0].strip()
                    st.session_state["GEMINI_API_KEY"] = saved_key
        except Exception:
            pass

    if not st.session_state.get("GEMINI_API_KEY"):
        env_key = os.getenv("GEMINI_API_KEY", "").strip()
        if env_key:
            st.session_state["GEMINI_API_KEY"] = env_key


# ==============================================================================
# DASHBOARD: TỔNG QUAN SỨC KHỎE
# ==============================================================================
if st.session_state.main_navigation == "🏠 Tổng quan sức khỏe":
    app_notifs = get_system_notifications(ROLE, USER_ID)
    notif_count = len(app_notifs)

    col_search, col_bell, col_profile = st.columns([2.4, 0.4, 0.8])
    with col_search:
        st.text_input("Search", placeholder="🔍 Tìm kiếm dịch vụ, bệnh lý, thuốc, bác sĩ...", label_visibility="collapsed", key="dashboard_unique_search")

    with col_bell:
        with st.popover(f"🔔 {notif_count}", width="stretch"):
            st.markdown(f"#### 🔔 Trung Tâm Thông Báo ({notif_count})")
            st.markdown("<hr style='margin: 6px 0 10px 0;'>", unsafe_allow_html=True)
            if not app_notifs:
                st.caption("🎉 Hệ thống chưa có thông báo mới.")
            else:
                for idx, n in enumerate(app_notifs):
                    st.markdown(f"""
                        <div style="background: #F8FBFE; border: 1px solid #E2ECF5; border-radius: 10px; padding: 8px 10px; margin-bottom: 6px;">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <b style="font-size: 12.5px; color: #10385D;">{n['icon']} {n['title']}</b>
                                <small style="font-size: 10px; color: #8F9EAB;">{n['time']}</small>
                            </div>
                            <div style="font-size: 11.5px; color: #4B6E8C; margin: 3px 0 6px 0;">{n['desc']}</div>
                        </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"👉 Chi tiết #{idx+1}", key=f"btn_bell_detail_{idx}", width="stretch"):
                        st.session_state.main_navigation = n["target_nav"]
                        st.rerun()

    with col_profile:
        st.markdown(f"""
            <div style="display: flex; align-items: center; gap: 8px; margin-top: 4px;">
                <img src="{avatar_display}" width="34" height="34" style="border-radius: 50%; object-fit: cover;">
                <div>
                    <b style="font-size: 12.5px; color: #113454; display: block; line-height: 1;">{current_prof['full_name']}</b>
                    <small style="font-size: 10.5px; color: #7B8F9F;">{role_label[:18]} ▾</small>
                </div>
            </div>
        """, unsafe_allow_html=True)

    try:
        with db_cursor() as cur_dash:
            cur_dash.execute("""
                SELECT 
                    (SELECT COUNT(*) FROM user_prescriptions WHERE user_id = %s),
                    (SELECT COUNT(*) FROM doctor_appointments WHERE user_id = %s),
                    (SELECT COUNT(*) FROM consultation_sessions WHERE user_id = %s)
            """, (USER_ID, USER_ID, USER_ID))
            dash_med_count, dash_app_count, dash_session_count = cur_dash.fetchone()

            cur_dash.execute("SELECT date, time, type FROM doctor_appointments WHERE user_id = %s ORDER BY id DESC LIMIT 1", (USER_ID,))
            last_app_row = cur_dash.fetchone()

            cur_dash.execute("""
                SELECT systolic, diastolic, heart_rate, bmi, glucose, to_char(recorded_date, 'DD/MM') 
                FROM health_vitals 
                WHERE user_id = %s 
                ORDER BY id DESC LIMIT 7
            """, (USER_ID,))
            vitals_history = cur_dash.fetchall()
    except Exception:
        dash_med_count, dash_app_count, dash_session_count = 0, 0, 0
        last_app_row = None
        vitals_history = []

    if vitals_history:
        curr_sys, curr_dia, curr_hr, curr_bmi, curr_glu, _ = vitals_history[0]
        bp_display = f"{curr_sys}/{curr_dia} mmHg" if curr_sys > 0 else "0 (Chưa đo)"
        hr_display = f"{curr_hr} lần/phút" if curr_hr > 0 else "0 (Chưa đo)"
        bmi_display = f"{curr_bmi:.1f}" if curr_bmi > 0 else "0 (Chưa tính)"
        glu_display = f"{curr_glu:.1f} mmol/L" if curr_glu > 0 else "0 (Chưa đo)"
    else:
        curr_sys, curr_dia, curr_hr, curr_bmi, curr_glu = 0, 0, 0, 0, 0
        bp_display, hr_display, bmi_display, glu_display = "0 (Chưa đo)", "0 (Chưa đo)", "0 (Chưa tính)", "0 (Chưa đo)"

    bp_warn = curr_sys >= 140 or curr_dia >= 90
    glu_warn = curr_glu >= 7.0
    hr_warn = curr_hr > 100 or (curr_hr < 50 and curr_hr > 0)

    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
    col_main, col_side = st.columns([2.6, 1.0], gap="medium")

    # CỘT CHÍNH (DASHBOARD)
    with col_main:
        # Hàm callback chuyển trang chuẩn Streamlit (giữ vững phiên đăng nhập 100%)
        def switch_nav(target_page):
            st.session_state.main_navigation = target_page
            st.session_state.current_reading_article_id = None

        # 1. BANNER THU GỌN VỪA VẶN MÀN HÌNH
        banner_b64 = get_render_media_asset("banner_home", "banner.png")
        if banner_b64:
            st.markdown(f"""
                <div class="hero-banner-compact" style="background-image: url('data:image/png;base64,{banner_b64}');">
                    <div class="hero-banner-compact-title">
                        Xin chào, {current_prof['full_name']}
                    </div>
                    <div class="hero-banner-compact-sub">
                        Chúc bạn luôn khỏe mạnh!
                    </div>
                </div>
            """, unsafe_allow_html=True)
        else:
            st.info("💡 Chưa có ảnh Banner. Vui lòng đặt file 'banner.png' cùng thư mục.")
        
        st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)

        # 2. BA THẺ TỔNG QUAN
        c_m1, c_m2, c_m3 = st.columns(3)
        last_date = last_app_row[0] if last_app_row else "Chưa có"
        last_type = last_app_row[2] if last_app_row else "Khám tổng quát"
        with c_m1:
            st.markdown(f"""
                <div class="summary-card">
                    <div class="summary-icon" style="background: #EBF5FE;">📅</div>
                    <div>
                        <div class="summary-label">Lần khám gần nhất</div>
                        <div class="summary-val">{last_date}</div>
                        <div class="summary-sub">{last_type}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
        with c_m2:
            st.markdown(f"""
                <div class="summary-card">
                    <div class="summary-icon" style="background: #FEF0F0;">🧪</div>
                    <div>
                        <div class="summary-label">Phiên tư vấn đã lưu</div>
                        <div class="summary-val">{dash_session_count} kết quả</div>
                        <div class="summary-sub" style="color: #059669;">Đã đồng bộ hệ thống</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
        with c_m3:
            st.markdown(f"""
                <div class="summary-card">
                    <div class="summary-icon" style="background: #EAF8F0;">💊</div>
                    <div>
                        <div class="summary-label">Đơn thuốc hiện tại</div>
                        <div class="summary-val">{dash_med_count} đơn</div>
                        <div class="summary-sub">Đang theo dõi sử dụng</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)

        # 3. TÌNH TRẠNG SỨC KHỎE + BIỂU ĐỒ (2 CỘT CÂN BẰNG)
        c_vital_left, c_chart_right = st.columns([1.35, 1.0], gap="medium")

        with c_vital_left:
            bp_tag_cls = "background:#FEE2E2; color:#DC2626;" if bp_warn else ("background:#DCFCE7; color:#16A34A;" if curr_sys > 0 else "background:#F1F5F9; color:#64748B;")
            bp_tag_txt = "Cảnh báo cao" if bp_warn else ("Bình thường" if curr_sys > 0 else "Chưa đo")

            hr_tag_cls = "background:#FEE2E2; color:#DC2626;" if hr_warn else ("background:#DCFCE7; color:#16A34A;" if curr_hr > 0 else "background:#F1F5F9; color:#64748B;")
            hr_tag_txt = "Bất thường" if hr_warn else ("Bình thường" if curr_hr > 0 else "Chưa đo")

            if curr_bmi <= 0:
                bmi_tag_cls, bmi_tag_txt = "background:#F1F5F9; color:#64748B;", "Chưa tính"
            elif curr_bmi < 18.5:
                bmi_tag_cls, bmi_tag_txt = "background:#FEF3C7; color:#D97706;", "Thiếu cân"
            elif 18.5 <= curr_bmi <= 22.9:
                bmi_tag_cls, bmi_tag_txt = "background:#DCFCE7; color:#16A34A;", "Bình thường"
            elif 23.0 <= curr_bmi <= 24.9:
                bmi_tag_cls, bmi_tag_txt = "background:#FFEDD5; color:#EA580C;", "Thừa cân"
            else:
                bmi_tag_cls, bmi_tag_txt = "background:#FEE2E2; color:#DC2626;", "Béo phì"

            glu_tag_cls = "background:#FEE2E2; color:#DC2626;" if glu_warn else ("background:#DCFCE7; color:#16A34A;" if curr_glu > 0 else "background:#F1F5F9; color:#64748B;")
            glu_tag_txt = "Nguy cơ cao" if glu_warn else ("Bình thường" if curr_glu > 0 else "Chưa đo")

            body_img_b64 = get_render_media_asset("vitals_body", "vitals_body.png")
            if body_img_b64:
                body_img_tag = f'<img src="data:image/png;base64,{body_img_b64}" style="width: 100%; height: 215px; object-fit: contain; object-position: center; mix-blend-mode: multiply; filter: drop-shadow(0 4px 10px rgba(2,132,199,0.12)); display: block;">'
            else:
                body_img_tag = '<div style="font-size: 80px; text-align: center; line-height: 215px;">🧍</div>'

            icon_bp = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z" fill="#FB7185"/><path d="M12 7v4M12 15h.01" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round"/></svg>"""
            icon_hr = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M3 12h4.5l2-6 4.5 12 2.5-8 1.5 2h3" stroke="#F43F5E" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
            icon_bmi = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="7" r="4" stroke="#0284C7" stroke-width="2.2"/><path d="M5.5 21v-2a6.5 6.5 0 0113 0v2" stroke="#0284C7" stroke-width="2.2" stroke-linecap="round"/><path d="M12 11v6" stroke="#0284C7" stroke-width="2.2" stroke-linecap="round"/></svg>"""
            icon_glu = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2.69l5.66 5.66a8 8 0 11-11.31 0z" fill="none" stroke="#0284C7" stroke-width="2.2" stroke-linejoin="round"/><circle cx="12" cy="14" r="2.5" fill="#0284C7"/></svg>"""

            with st.container(border=True):
                st.markdown("""
                    <div style="font-size: 15px; font-weight: 800; color: #0F172A; margin-bottom: 6px;">
                        Tình trạng sức khỏe tổng quan
                    </div>
                """, unsafe_allow_html=True)

                vitals_inner_html = f"""
                <div style="display: flex; flex-direction: row; align-items: center; justify-content: space-between; gap: 14px;">
                    <div style="flex: 0 0 36%; display: flex; align-items: center; justify-content: center; height: 215px;">
                        {body_img_tag}
                    </div>
                    <div style="flex: 1 1 64%; display: flex; flex-direction: column; justify-content: space-between; height: 215px;">
                        <div style="display: flex; align-items: center; padding: 4px 0; border-bottom: 1px solid #F1F5F9; gap: 10px;">
                            <div style="width: 32px; height: 32px; border-radius: 50%; background: #FFE4E6; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">{icon_bp}</div>
                            <div style="display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0;">
                                <span style="font-size: 11px; color: #64748B; font-weight: 600; line-height: 1.1;">Huyết áp</span>
                                <span style="font-size: 13px; font-weight: 800; color: #0F172A; line-height: 1.2;">{bp_display}</span>
                            </div>
                            <span style="font-size: 9.5px; font-weight: 700; padding: 2px 8px; border-radius: 12px; {bp_tag_cls}">{bp_tag_txt}</span>
                        </div>
                        <div style="display: flex; align-items: center; padding: 4px 0; border-bottom: 1px solid #F1F5F9; gap: 10px;">
                            <div style="width: 32px; height: 32px; border-radius: 50%; background: #FFF1F2; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">{icon_hr}</div>
                            <div style="display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0;">
                                <span style="font-size: 11px; color: #64748B; font-weight: 600; line-height: 1.1;">Nhịp tim</span>
                                <span style="font-size: 13px; font-weight: 800; color: #0F172A; line-height: 1.2;">{hr_display}</span>
                            </div>
                            <span style="font-size: 9.5px; font-weight: 700; padding: 2px 8px; border-radius: 12px; {hr_tag_cls}">{hr_tag_txt}</span>
                        </div>
                        <div style="display: flex; align-items: center; padding: 4px 0; border-bottom: 1px solid #F1F5F9; gap: 10px;">
                            <div style="width: 32px; height: 32px; border-radius: 50%; background: #E0F2FE; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">{icon_bmi}</div>
                            <div style="display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0;">
                                <span style="font-size: 11px; color: #64748B; font-weight: 600; line-height: 1.1;">Chỉ số BMI</span>
                                <span style="font-size: 13px; font-weight: 800; color: #0F172A; line-height: 1.2;">{bmi_display}</span>
                            </div>
                            <span style="font-size: 9.5px; font-weight: 700; padding: 2px 8px; border-radius: 12px; {bmi_tag_cls}">{bmi_tag_txt}</span>
                        </div>
                        <div style="display: flex; align-items: center; padding: 4px 0; gap: 10px;">
                            <div style="width: 32px; height: 32px; border-radius: 50%; background: #E0F2FE; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">{icon_glu}</div>
                            <div style="display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0;">
                                <span style="font-size: 11px; color: #64748B; font-weight: 600; line-height: 1.1;">Đường huyết</span>
                                <span style="font-size: 13px; font-weight: 800; color: #0F172A; line-height: 1.2;">{glu_display}</span>
                            </div>
                            <span style="font-size: 9.5px; font-weight: 700; padding: 2px 8px; border-radius: 12px; {glu_tag_cls}">{glu_tag_txt}</span>
                        </div>
                    </div>
                </div>
                """
                st.markdown(vitals_inner_html, unsafe_allow_html=True)

                col_sp, col_btn_detail = st.columns([1.2, 1.0])
                with col_btn_detail:
                    with st.popover("Xem chi tiết ➔", width="stretch"):
                        st.markdown("#### ⚙️ Chi tiết & Cập nhật chỉ số sinh hiệu")
                        with st.form("form_update_vitals_inside_card"):
                            in_sys = st.number_input("Huyết áp tâm thu (mmHg):", min_value=0, max_value=250, value=curr_sys if curr_sys > 0 else 120)
                            in_dia = st.number_input("Huyết áp tâm trương (mmHg):", min_value=0, max_value=150, value=curr_dia if curr_dia > 0 else 80)
                            in_hr = st.number_input("Nhịp tim (lần/phút):", min_value=0, max_value=200, value=curr_hr if curr_hr > 0 else 72)
                            in_glu = st.number_input("Đường huyết đói (mmol/L):", min_value=0.0, max_value=30.0, value=curr_glu if curr_glu > 0 else 5.2)
                            in_weight = st.number_input("Cân nặng (kg):", min_value=0.0, max_value=200.0, value=65.0)
                            in_height = st.number_input("Chiều cao (cm):", min_value=0.0, max_value=250.0, value=170.0)
                            
                            c_f1, c_f2 = st.columns(2)
                            with c_f1:
                                if st.form_submit_button("💾 Lưu chỉ số", type="primary", width="stretch"):
                                    calc_bmi = in_weight / ((in_height / 100) ** 2) if in_height > 0 else 0
                                    with db_cursor() as cur_v:
                                        cur_v.execute("""
                                            INSERT INTO health_vitals (user_id, systolic, diastolic, heart_rate, weight, height, bmi, glucose)
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                        """, (USER_ID, in_sys, in_dia, in_hr, in_weight, in_height, calc_bmi, in_glu))
                                    st.toast("✅ Đã cập nhật chỉ số sinh hiệu thành công!")
                                    st.rerun()
                            with c_f2:
                                if st.form_submit_button("🧑‍⚕️ Xem hồ sơ đầy đủ", width="stretch"):
                                    st.session_state.main_navigation = "🧑‍⚕️Khám Bệnh Online (Trợ Lý Y Tế)"
                                    st.rerun()

        with c_chart_right:
            if vitals_history:
                chart_dates = [v[5] for v in reversed(vitals_history)]
                chart_sys = [v[0] for v in reversed(vitals_history)]
                chart_glu = [v[4] for v in reversed(vitals_history)]
                chart_hr = [v[2] for v in reversed(vitals_history)]
            else:
                chart_dates = ['26/08', '27/08', '28/08', '29/08', '30/08', '31/08']
                chart_sys = [120, 125, 118, 122, 120, 120]
                chart_glu = [5.2, 5.5, 5.0, 5.4, 5.2, 5.2]
                chart_hr = [72, 75, 70, 74, 72, 72]

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=chart_dates, y=[g * 10 for g in chart_glu], name='Đường huyết',
                yaxis='y2', marker=dict(color='#10B981', cornerradius=3),
                customdata=chart_glu, hovertemplate='%{x}<br>Đường huyết: %{customdata:.1f} mmol/L<extra></extra>', offsetgroup=1
            ))
            fig.add_trace(go.Bar(
                x=chart_dates, y=chart_sys, name='Huyết áp (mmHg)',
                yaxis='y1', marker=dict(color='#0284C7', cornerradius=3),
                hovertemplate='%{x}<br>Huyết áp: %{y} mmHg<extra></extra>', offsetgroup=2
            ))
            fig.add_trace(go.Bar(
                x=chart_dates, y=chart_hr, name='Nhịp tim (bpm)',
                yaxis='y2', marker=dict(color='#EC4899', cornerradius=3),
                hovertemplate='%{x}<br>Nhịp tim: %{y} lần/phút<extra></extra>', offsetgroup=3
            ))

            fig.update_layout(
                title=dict(text="<b>Biểu đồ sức khỏe</b>", font=dict(size=15, color="#0F172A", family="Segoe UI, sans-serif"), x=0.04, y=0.96, xanchor="left", yanchor="top"),
                barmode='group', bargroupgap=0.15, bargap=0.62, height=320, margin=dict(l=14, r=14, t=38, b=48),
                paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                legend=dict(orientation="h", yanchor="top", y=-0.22, xanchor="center", x=0.5, font=dict(size=10.5, color="#334155")),
                xaxis=dict(showgrid=False, tickfont=dict(size=10, color="#475569")),
                yaxis=dict(title=dict(text="Huyết áp (mmHg)", font=dict(size=10, color="#0284C7")), tickfont=dict(size=9.5, color="#0284C7"), range=[0, 180], showgrid=True, gridcolor="#F1F5F9"),
                yaxis2=dict(title=dict(text="Nhịp tim & Đường huyết", font=dict(size=10, color="#EC4899")), tickfont=dict(size=9.5, color="#EC4899"), range=[0, 150], overlaying='y', side='right', showgrid=False)
            )
            st.plotly_chart(fig, width="stretch", config={'displayModeBar': False})

        # ==============================================================
        # 4. HÀNG DƯỚI CÙNG: DỊCH VỤ Y TẾ NHANH (TRÁI) & TIN TỨC Y TẾ (PHẢI)
        # ==============================================================
        st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
        col_quick_box, col_news_box = st.columns([1.3, 1.0], gap="medium")

        # CỘT TRÁI: DỊCH VỤ Y TẾ NHANH
        with col_quick_box:
            with st.container(border=True):
                st.markdown("""
                    <div style="font-size: 15px; font-weight: 800; color: #0F172A; margin-bottom: 8px;">
                        👉👉👉 Dịch Vụ Y Tế Nhanh
                    </div>
                """, unsafe_allow_html=True)

                def go_to_online_clinic(target_sub_tab="triage"):
                    st.session_state.main_navigation = "🧑‍⚕️Khám Bệnh Online (Trợ Lý Y Tế)"
                    st.session_state.clinic_active_tab = target_sub_tab
                    st.session_state.current_reading_article_id = None

                st.markdown('<div class="tight-service-box">', unsafe_allow_html=True)
                r1_c1, r1_c2 = st.columns(2, gap="small")
                with r1_c1:
                    st.button("📅 Đặt lịch khám bệnh", key="btn_q_app", on_click=switch_nav, args=("🩺 Khám Sức Khỏe & Đặc Quyền VIP",), use_container_width=True)
                with r1_c2:
                    st.button("🧪 Xét nghiệm tại nhà", key="btn_q_test", on_click=switch_nav, args=("🩺 Khám Sức Khỏe & Đặc Quyền VIP",), use_container_width=True)

                r2_c1, r2_c2 = st.columns(2, gap="small")
                with r2_c1:
                    st.button("🧑‍⚕️ Khám bệnh online", key="btn_q_ai", on_click=go_to_online_clinic, args=("triage",), use_container_width=True)
                with r2_c2:
                    st.button("💊 Đơn thuốc & Lịch uống", key="btn_q_med", on_click=go_to_online_clinic, args=("prescriptions",), use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)

        # CỘT PHẢI: TIN TỨC Y TẾ (HTML SẠCH 100%, KHÔNG BỊ IN MÃ THÔ)
        with col_news_box:
            with st.container(border=True):
                if "read_news_id" in st.query_params:
                    selected_art_id = int(st.query_params["read_news_id"])
                    st.query_params.clear()
                    st.session_state.main_navigation = "📰 Tin tức & Khuyến cáo y tế"
                    st.session_state.current_reading_article_id = selected_art_id
                    st.rerun()

                col_n_head, col_n_more = st.columns([1.5, 1.3])
                with col_n_head:
                    st.markdown("""
                        <div style="font-size: 15px; font-weight: 800; color: #0F172A; padding-top: 2px;">
                            Tin tức y tế
                        </div>
                    """, unsafe_allow_html=True)
                with col_n_more:
                    if st.button("Xem tất cả ➔", key="btn_see_all_news_redesign", use_container_width=True):
                        st.session_state.main_navigation = "📰 Tin tức & Khuyến cáo y tế"
                        st.session_state.current_reading_article_id = None
                        st.rerun()

                st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)

                news_items = [
                    {
                        "id": 1,
                        "title": "Đột quỵ có phải là căn bệnh không báo trước?",
                        "date": "21/08/2026",
                        "img": "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=120&auto=format&fit=crop&q=60"
                    },
                    {
                        "id": 6,
                        "title": "Chế độ ăn DASH kiểm soát tăng huyết áp mới nhất",
                        "date": "20/08/2026",
                        "img": "https://images.unsplash.com/photo-1498837167922-ddd27525d352?w=120&auto=format&fit=crop&q=60"
                    },
                    {
                        "id": 2,
                        "title": "Bản chất của ung thư & 7 tín hiệu 'kẻ thù thầm lặng'",
                        "date": "18/08/2026",
                        "img": "https://images.unsplash.com/photo-1579154204601-01588f351e67?w=120&auto=format&fit=crop&q=80"
                    }
                ]

                # Tạo chuỗi HTML sát lề trái, loại bỏ hoàn toàn khoảng thụt đầu dòng
                rows_html = []
                for item in news_items:
                    row = (
                        f'<a href="?read_news_id={item["id"]}" target="_self" class="news-item-row">'
                        f'<img src="{item["img"]}" class="news-item-thumb" />'
                        f'<div class="news-item-content">'
                        f'<div class="news-item-title">{item["title"]}</div>'
                        f'<div class="news-item-date">🕒 {item["date"]}</div>'
                        f'</div>'
                        f'</a>'
                        f'<div class="news-item-divider"></div>'
                    )
                    rows_html.append(row)

                full_news_html = f'<div class="news-list-flex-container">{"".join(rows_html)}</div>'
                st.markdown(full_news_html, unsafe_allow_html=True)

    # ============================================================
    # CỘT PHỤ (BÊN PHẢI NGANG HÀNG VỚI COL_MAIN)
    # ============================================================
    with col_side:
        st.markdown(f"""
            <div class="dashboard-card">
                <div class="card-title">
                    <span>👤 Thông tin cá nhân</span>
                    <span class="status-badge">{'⭐ VIP MEMBER' if current_prof.get('is_vip') else 'Chuẩn'}</span>
                </div>
                <div style="text-align: center; margin-bottom: 8px;">
                    <img src="{avatar_display}" width="75" height="75" style="border-radius: 50%; border: 3px solid #EBF4FC; object-fit: cover;">
                    <div style="font-size: 15px; font-weight: 800; color: #0E355B; margin-top: 6px;">{current_prof['full_name']}</div>
                    <div style="font-size: 12px; color: #7B8F9F;">{current_prof['gender']} · {current_prof['age']} tuổi</div>
                </div>
                <div style="font-size: 12px; color: #4B6E8C; line-height: 1.7;">
                    <div>📞 SĐT: <b>{current_prof.get('phone', 'Chưa có')}</b></div>
                    <div>✉️ Email: <b>{current_prof.get('email', 'Chưa có')}</b></div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        with st.popover("⚙️ Chỉnh sửa hồ sơ & Cài đặt", width="stretch"):
            with st.form("form_edit_profile_folder"):
                f_name = st.text_input("Họ và tên:", value=current_prof.get("full_name", ""))
                f_age = st.number_input("Tuổi:", min_value=1, max_value=120, value=int(current_prof.get("age", 30)))
                g_list = ["Nam", "Nữ", "Khác"]
                f_gender = st.selectbox("Giới tính:", g_list, index=g_list.index(current_prof.get("gender", "Nam")) if current_prof.get("gender") in g_list else 0)
                f_phone = st.text_input("Số điện thoại:", value=current_prof.get("phone", ""))
                f_email = st.text_input("Địa chỉ Email:", value=current_prof.get("email", ""))
                f_bio = st.text_area("🌱 Sở thích:", value=current_prof.get("bio", "Yêu thích thể thao, đọc sách y khoa"), height=70)
                f_av_file = st.file_uploader("Thay ảnh đại diện:", type=["png", "jpg", "jpeg"], key="pop_av_file")
                btn_save_folder_profile = st.form_submit_button("💾 Lưu thay đổi", type="primary", width="stretch")

            if btn_save_folder_profile:
                new_av_b64 = current_prof.get("avatar")
                if f_av_file:
                    new_av_b64 = f"data:{f_av_file.type};base64,{base64.b64encode(f_av_file.getvalue()).decode()}"

                with db_cursor() as cur_u:
                    cur_u.execute("""
                        UPDATE app_users 
                        SET full_name = %s, age = %s, gender = %s, phone = %s, email = %s, bio = %s, avatar = %s
                        WHERE user_id = %s
                    """, (f_name.strip(), f_age, f_gender, f_phone.strip(), f_email.strip(), f_bio.strip(), new_av_b64, USER_ID))

                get_cached_users_from_render.clear()
                st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()
                st.toast("🎉 Đã cập nhật hồ sơ cá nhân lên Render!")
                st.rerun()

        dash_my_prescriptions = []
        try:
            with db_cursor() as cur_dash:
                cur_dash.execute("SELECT medicine_name, dosage, alarm_time, instructions FROM user_prescriptions WHERE user_id = %s ORDER BY id ASC LIMIT 3", (USER_ID,))
                dash_my_prescriptions = cur_dash.fetchall()
        except Exception:
            dash_my_prescriptions = []

        if dash_my_prescriptions:
            med_times_count = len(dash_my_prescriptions)
            st.markdown(f"""
                <div style="background:#FFFFFF; border:1px solid #E2ECF5; border-radius:18px; padding:14px 16px; margin-bottom:12px; box-shadow:0 4px 18px rgba(15,60,100,0.03);">
                    <div style="display:flex; align-items:center; gap:12px;">
                        <div style="width:42px; height:42px; border-radius:12px; background:#F5F3FF; color:#7C3AED; display:flex; align-items:center; justify-content:center; font-size:20px;">🔔</div>
                        <div>
                            <div style="font-size:11px; color:#64748B; font-weight:600;">Nhắc nhở thuốc</div>
                            <div style="font-size:14.5px; font-weight:800; color:#0F172A;">{med_times_count} cữ uống/ngày</div>
                            <div style="font-size:10px; color:#7C3AED; font-weight:700;">Đang theo dõi</div>
                        </div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
                <div style="background:#FFFFFF; border:1px solid #E2ECF5; border-radius:18px; padding:14px 16px; margin-bottom:12px; box-shadow:0 4px 18px rgba(15,60,100,0.03);">
                    <div style="display:flex; align-items:center; gap:12px;">
                        <div style="width:42px; height:42px; border-radius:12px; background:#F1F5F9; color:#94A3B8; display:flex; align-items:center; justify-content:center; font-size:20px;">🔕</div>
                        <div>
                            <div style="font-size:11px; color:#64748B; font-weight:600;">Nhắc nhở thuốc</div>
                            <div style="font-size:13.5px; font-weight:700; color:#94A3B8;">Không có lịch uống</div>
                            <div style="font-size:10px; color:#64748B;">Hồ sơ hiện tại trống</div>
                        </div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        with st.container():
            c_med_t1, c_med_t2 = st.columns([1.5, 1.2])
            with c_med_t1:
                st.markdown('<div style="font-size:13px; font-weight:800; color:#0F172A; padding-top:4px;">Đơn thuốc & nhắc nhở</div>', unsafe_allow_html=True)
            with c_med_t2:
                if st.button("Xem tất cả →", key="btn_view_all_prescriptions", width="stretch"):
                    st.session_state.main_navigation = "🧑‍⚕️Khám Bệnh Online (Trợ Lý Y Tế)"
                    st.session_state.clinic_active_tab = "prescriptions"
                    st.session_state.current_reading_article_id = None
                    st.rerun()

            if not dash_my_prescriptions:
                st.markdown("""
                    <div style="background:#FFFFFF; border:1px solid #E2ECF5; border-radius:14px; padding:16px 12px; margin-top:4px; box-shadow:0 2px 8px rgba(15,60,100,0.02); text-align:center;">
                        <div style="font-size:24px; margin-bottom:4px;">💊</div>
                        <div style="font-size:12px; font-weight:700; color:#64748B;">Chưa có đơn thuốc nào</div>
                        <div style="font-size:10.5px; color:#94A3B8; margin-top:2px;">Bạn hiện không có lịch uống thuốc nào</div>
                    </div>
                """, unsafe_allow_html=True)
            else:
                med_rows = []
                for med in dash_my_prescriptions:
                    med_name, med_dos, med_time, _ = med
                    row = (
                        f'<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid #F1F5F9;">'
                        f'<div style="display:flex; align-items:center; gap:8px;">'
                        f'<span style="font-size:16px;">💊</span>'
                        f'<div>'
                        f'<div style="font-size:11.5px; font-weight:800; color:#0F172A;">{med_name}</div>'
                        f'<div style="font-size:9.5px; color:#64748B;">{med_dos}</div>'
                        f'</div>'
                        f'</div>'
                        f'<div style="text-align:right;">'
                        f'<div style="font-size:10px; font-weight:700; color:#0F172A;">{med_time}</div>'
                        f'<span style="font-size:9px; font-weight:700; background:#DCFCE7; color:#16A34A; padding:2px 6px; border-radius:6px;">Đang dùng</span>'
                        f'</div>'
                        f'</div>'
                    )
                    med_rows.append(row)

                full_med_box = (
                    f'<div style="background:#FFFFFF; border:1px solid #E2ECF5; border-radius:14px; padding:10px 12px; margin-top:4px; box-shadow:0 2px 8px rgba(15,60,100,0.02);">'
                    f'{"".join(med_rows)}'
                    f'</div>'
                )
                st.html(full_med_box)

# ==============================================================================
# PHÂN HỆ: TIN TỨC & KHUYẾN CÁO Y TẾ (TRỌN BỘ 12 BÀI BÁO - GIAO DIỆN GỌN GÀNG)
# ==============================================================================
elif st.session_state.main_navigation == "📰 Tin tức & Khuyến cáo y tế":

    ARTICLES_DATA = [
        {
            "id": 1,
            "category": "PHÓNG SỰ CHUYÊN SÂU - CẤP CỨU TIM MẠCH",
            "read_time": "15 phút đọc",
            "date": "06/09/2026",
            "author": "BS. Nguyễn Tiến Toàn (Khoa Hồi sức Tim mạch - Thần kinh)",
            "title": "Hồ Sơ Đột Quỵ: 'Cơn Địa Chấn' Trong Lòng Não Bộ Và Cuộc Đua Sinh Tử Với Từng Giây Đồng Hồ",
            "summary": "Khoảng 30% bệnh nhân trước khi đột quỵ thực sự từng trải qua 'cơn đột quỵ nhỏ' (TIA) nhưng bỏ qua vì tự hết sau vài phút. Dưới lăng kính sinh lý bệnh, đột quỵ không xảy ra ngẫu nhiên mà là đỉnh điểm của một chuỗi biến cố tổn thương lớp nội mạc mạch máu suốt nhiều năm. Cùng mổ xẻ cơ chế tắc mạch, vùng tranh tối tranh sáng và những điều cấm kỵ khi sơ cứu.",
            "image": "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: HUYỀN THOẠI VỀ "TIẾNG SÉT GIỮA TRỜI QUANG" VÀ THỰC TẾ SINH LÝ BỆNH

Trong đời sống thường nhật, dân gian vẫn thường xem tai biến mạch máu não (đột quỵ) như một biến cố bất thần, một sự xui rủi "trúng gió" ập đến không báo trước. Người ta thường kể về một người hôm qua vẫn khỏe mạnh, đi lại bình thường, bỗng một buổi sáng thức dậy ngã gục xuống sàn nhà, méo miệng và vĩnh viễn mất đi khả năng vận động hoặc tử vong.

Thế nhưng, dưới lăng kính của sinh lý bệnh học tim mạch hiện đại: **Không có ca đột quỵ nào là ngẫu nhiên**. 

Một cơn đột quỵ thiếu máu cục bộ (chiếm 85% tổng số ca bệnh) hay một ca xuất huyết não (chiếm 15%) thực chất là **điểm bùng nổ cuối cùng của một quá trình 'vỡ nợ' hệ thống tuần hoàn**. Nó là hệ quả tích tụ suốt 5 năm, 10 năm, thậm chí 30 năm bị tàn phá âm thầm bởi áp lực cơ học của dòng máu tăng huyết áp, sự lắng đọng của các hạt mỡ máu xấu (LDL-C) bị oxy hóa, sự hủy hoại nội mạc do nicotin trong khói thuốc lá và phản ứng viêm mãn tính tại thành mạch.

Mạch máu nuôi não của chúng ta vốn dĩ là những ống dẫn tinh vi và dẻo dai bậc nhất cơ thể. Tuy nhiên, khi các yếu tố nguy cơ không được kiểm soát, lòng mạch sẽ xuất hiện các mảng xơ vữa. Mảng xơ vữa lớn dần làm hẹp lòng ống, hoặc nguy hiểm hơn, lớp vỏ xơ mỏng manh của nó bị nứt vỡ dưới một cơn stress hay một đợt huyết áp tăng vọt. Khi mảng xơ vữa vỡ ra, tiểu cầu lập tức bám dính vào tạo thành cục huyết khối đỏ bít kín hoàn toàn nhánh động mạch. Dòng máu nuôi não bị chặn đứng ngay lập tức.

---

## PHẦN 2: TIA - TIẾNG CÒI BÁO ĐỘNG BỊ PHỚT LỜ TRƯỚC "CƠN ĐỊA CHẤN"

Các nghiên cứu dịch tễ học thần kinh quốc tế đã chỉ ra một con số giật mình: **Cứ 3 bệnh nhân đột quỵ thì có 1 người từng trải qua Cơn thiếu máu não thoáng qua (Transient Ischemic Attack - TIA) trước đó vài ngày hoặc vài tuần**. Đây chính là "cơn đột quỵ nhỏ" mà cơ thể phát đi để cầu cứu, nhưng thường bị nạn nhân và gia đình bỏ qua.

### Bản chất cơ chế học của cơn TIA:
Một cục máu đông nhỏ hoặc một mẩu mảng xơ vữa bong ra từ động mạch cảnh ở cổ trôi theo dòng máu lên não và kẹt lại ở một nhánh tiểu động mạch. Ngay lập tức, vùng tế bào não phụ trách khu vực đó bị ngưng trệ chức năng do thiếu oxy và glucose:
* Bệnh nhân đột ngột cảm thấy tê dại một bên má, khóe miệng trễ xuống.
* Tay đang cầm đũa ăn cơm hoặc cầm cốc nước bỗng nhiên rơi tuột xuống sàn, tay không thể nâng lên được.
* Mắt nhìn mờ đột ngột một bên, giống như có một tấm rèm xám sụp xuống che khuất một phần tầm nhìn.
* Đang nói chuyện bình thường bỗng nói ngọng, nói lắp, hoặc người khác nói nhưng đầu óc không thể hiểu được từ ngữ trong chốc lát.

### Cái bẫy chết người: Sự biến mất kỳ lạ của triệu chứng
Điểm nguy hiểm nhất của TIA là các triệu chứng này thường **chỉ kéo dài từ 2 đến 15 phút**, tối đa là dưới 1 giờ rồi tự biến mất hoàn toàn. Nguyên nhân là do cơ thể có hệ thống men tiêu sợi huyết tự nhiên kịp thời hòa tan cục máu đông nhỏ đó, hoặc do các vòng nối tuần hoàn bàng hệ bơm máu bù qua cứu sống tế bào não tạm thời.

Sau 15 phút, bệnh nhân lại thấy mình tỉnh táo, đi đứng, nói cười bình thường. Phần lớn đều tự trấn an: *"Chắc do sáng nay dậy sớm bị trúng gió"*, *"Chắc do làm việc căng thẳng quá nên mỏi cơ"*. Người nhà thì vội vàng lấy củ gừng đánh gió, cạo gió đỏ lưng hoặc bắt bệnh nhân nằm nghỉ ngơi.

**Cảnh báo y khoa:** Hội Đột quỵ Hoa Kỳ (ASA) nhấn mạnh rằng: Sau một cơn TIA, có tới **10 - 15% bệnh nhân sẽ chính thức đột quỵ thực sự trong vòng 90 ngày sau đó**, và đặc biệt, **một nửa trong số các ca đột quỵ nặng này xảy ra ngay trong 48 giờ đầu tiên**. Cơn TIA giống như một phát súng cảnh báo rằng mạch máu não đã tắc nghẽn đến mức báo động đỏ, nếu không vào viện khẩn cấp để dùng thuốc chống đông và can thiệp, cục máu đông lớn tiếp theo sẽ khóa chặt mạch máu vĩnh viễn.

---

## PHẦN 3: NĂNG LƯỢNG SINH HỌC CỦA NÃO VÀ KHÁI NIỆM "VÙNG TRANH TỐI TRANH SÁNG"

Tại sao các bác sĩ cấp cứu đột quỵ luôn nhắc đi nhắc lại câu khẩu hiệu: **"THỜI GIAN LÀ NÃO" (TIME IS BRAIN)**?

Bộ não người trưởng thành chỉ chiếm khoảng **2% trọng lượng toàn thân** (nặng khoảng 1.3 - 1.4 kg), nhưng nó lại là cơ quan tiêu thụ năng lượng khủng khiếp nhất: **đòi hỏi tới 20% tổng lượng oxy và 25% tổng lượng đường glucose** của toàn bộ vòng tuần hoàn máu cơ thể. Điều đặc biệt là tế bào thần kinh (nơ-ron) không có kho dự trữ năng lượng glycogen như các cơ bắp; chúng phụ thuộc 100% vào dòng máu chảy liên tục từng giây từng phút.

### Điều gì xảy ra khi động mạch não bị tắc?
Khi dòng máu bị ngắt, vùng não thiếu máu sẽ phân thành hai khu vực rõ rệt:

1. **Lõi hoại tử (Ischemic Core):** Là vùng trung tâm nơi mạch máu bị bít tắc hoàn toàn, lưu lượng máu giảm xuống dưới 10 ml/100g mô não/phút. Ở vùng này, chỉ sau 4 đến 5 phút thiếu oxy, các bơm ion trên màng tế bào sụp đổ, canxi tràn ồ ạt vào nội bào kích hoạt các enzym tự tiêu, nơ-ron thần kinh chết vĩnh viễn và không có cách nào hồi sinh được.
2. **Vùng tranh tối tranh sáng (Penumbra):** Là vùng mô não bao quanh lõi hoại tử. Vùng này lưu lượng máu bị giảm mạnh nhưng vẫn còn nhận được một phần máu ít ỏi từ các mạch máu lân cận (tuần hoàn bàng hệ) tưới qua. Tế bào ở vùng Penumbra rơi vào trạng thái "ngủ đông điện học" – chúng ngừng hoạt động dẫn truyền xung thần kinh nhưng cấu trúc tế bào vẫn còn sống sót.

**Tốc độ hủy diệt:** Ước tính trong nhồi máu não cấp, **mỗi phút trôi qua có khoảng 1.900.000 tế bào nơ-ron thần kinh và 14 tỷ khớp nối synap bị tiêu hủy vĩnh viễn**. Mỗi giờ cấp cứu chậm trễ, não bộ của người bệnh bị lão hóa tương đương với 3.6 năm tuổi thọ sinh học. Nếu không tái thông mạch máu kịp thời, vùng Penumbra sẽ chết dần từ ngoài vào trong và biến toàn bộ thành vùng hoại tử vĩnh viễn.

---

## PHẦN 4: CUỘC ĐUA VỚI "GIỜ VÀNG" CỦA Y HỌC HIỆN ĐẠI

Trước đây, người bị đột quỵ thường chỉ nằm chờ hồi phục tự nhiên hoặc tập phục hồi chức năng sau tai biến. Nhưng hiện nay, y học cấp cứu đã có thể can thiệp tái thông dòng máu nếu bệnh nhân đến viện trong **khung giờ vàng**:

* **Cửa sổ 3 giờ đến 4.5 giờ đầu (Thuốc tiêu sợi huyết đường tĩnh mạch - rTPA):**
  Thuốc Actilyse (rt-PA) được truyền qua tĩnh mạch. Thuốc hoạt động như một chiếc kéo sinh học, chuyển hóa plasminogen thành plasmin để phân cắt các sợi huyết fibrin của cục máu đông, làm tan cục máu và phục hồi dòng chảy.
* **Cửa sổ 6 giờ đến 24 giờ (Can thiệp lấy huyết khối cơ học bằng ống thông):**
  Đối với các ca tắc nhánh động mạch lớn trong não (như động mạch não giữa đoạn M1, động mạch thân nền), thuốc rTPA đường tĩnh mạch thường khó làm tan hoàn toàn cục máu đông lớn. Lúc này, các bác sĩ can thiệp mạch sẽ đưa một ống thông siêu nhỏ từ động mạch đùi ở bẹn, luồn ngược dòng máu lên tận mạch máu trong não. Dưới màn hình huỳnh quang tăng sáng, bác sĩ bung một chiếc khung lưới kim loại (stent retriever) ôm lấy cục máu đông hoặc dùng ống hút chân không áp lực âm để kéo toàn bộ cục huyết khối ra ngoài. Rất nhiều bệnh nhân đang hôn mê, liệt nửa người hoàn toàn có thể cử động lại được ngay trên bàn can thiệp nếu dòng máu được tái thông kịp thời.

---

## PHẦN 5: BA ĐIỀU CẤM KỴ TUYỆT ĐỐI KHI SƠ CỨU ĐỘT QUỴ TẠI NHÀ

Rất nhiều trường hợp bệnh nhân tử vong hoặc tàn phế suốt đời không phải do bản thân cơn đột quỵ quá nặng, mà do chính **những hành động sơ cứu phản khoa học của người thân trong gia đình**:

1. **CẤM CHÍCH MÁU 10 ĐẦU NGÓN TAY HOẶC DÁI TAI:**
   * *Nguồn gốc sai lầm:* Xuất phát từ những bài viết truyền miệng lan truyền trên mạng xã hội cho rằng chích máu giúp "giảm áp lực máu lên não".
   * *Sự thật y khoa:* Hành động lấy kim đâm vào các đầu ngón tay gây ra cơn đau đớn dữ dội cho bệnh nhân. Đau đớn sẽ kích hoạt hệ thần kinh giao cảm phóng thích ồ ạt hormone Adrenaline vào máu, làm huyết áp bệnh nhân vọt lên cao cực điểm. Một ca nhồi máu não (tắc mạch) có thể bị áp lực máu vọt cao làm vỡ tung thành mạch biến thành xuất huyết não tử vong tại chỗ.
2. **CẤM CHO UỐNG NƯỚC ĐƯỜNG, NƯỚC CHANH HOẶC VIÊN AN CUNG:**
   * Khi đột quỵ, vùng não kiểm soát phản xạ nuốt ở hành tủy thường bị tê liệt hoặc ức chế. Bệnh nhân nhìn có vẻ tỉnh táo nhưng thực tế không còn khả năng phối hợp đóng mở nắp thanh quản khi nuốt.
   * Bất kỳ giọt nước chanh hay viên thuốc nào đút vào miệng nạn nhân sẽ không chảy vào dạ dày mà chảy thẳng vào khí quản vào phổi. Hậu quả là gây ngạt thở cấp tính hoặc gây viêm phổi hít hóa chất, dẫn đến suy hô hấp tử vong trước khi xe cấp cứu kịp đến.
3. **CẤM TỰ Ý CHO UỐNG THUỐC HẠ HUYẾT ÁP KHẨN CẤP:**
   * Khi mạch máu não bị tắc, cơ thể có phản xạ tự nhiên sinh tồn là co mạch ngoại vi để đẩy huyết áp tăng cao lên (thường lên 170 - 180 mmHg) nhằm cố gắng ép dòng máu qua các mạch bàng hệ cứu vùng não Penumbra.
   * Nếu người nhà vội vã nhét viên thuốc ngậm hạ áp dưới lưỡi (như Adalat), huyết áp tụt dốc không phanh về mức bình thường. Áp lực bơm máu lên não biến mất hoàn toàn, vùng não đang hấp hối sẽ bị cắt đứt nguồn oxy cuối cùng và chết lập tức.

---

## PHẦN 6: GHI NHỚ NẰM LÒNG QUY TẮC F.A.S.T

Khi thấy một người có biểu hiện bất thường, hãy kiểm tra ngay 3 dấu hiệu sau đây trong vòng 30 giây:

* **F (Face - Khuôn mặt):** Yêu cầu người bệnh cười hoặc nhe răng. Quan sát xem một bên mặt có bị xệ xuống không? Khóe miệng có bị méo, rãnh mũi má một bên có bị mờ đi không?
* **A (Arm - Cánh tay):** Yêu cầu người bệnh nhắm mắt lại và giơ thẳng hai tay ra phía trước trong 10 giây. Nếu một bên tay yếu lả đi, rơi từ từ xuống hoặc không thể giơ lên được, đó là dấu hiệu liệt nửa người.
* **S (Speech - Lời nói):** Yêu cầu người bệnh lặp lại một câu nói đơn giản (ví dụ: *"Hôm nay trời nắng đẹp"*). Lắng nghe xem người bệnh có nói ngọng, líu lưỡi, nói từ ngữ vô nghĩa hoặc ú ớ không phát âm được không?
* **T (Time - Thời gian vàng):** Nếu xuất hiện **bất kỳ 1 trong 3 dấu hiệu trên**, không chần chừ thêm một phút nào: **Gọi ngay cấp cứu 115** hoặc đưa bệnh nhân bằng ô tô/taxi tới bệnh viện lớn gần nhất có chuyên khoa can thiệp đột quỵ. Ghi nhớ chính xác thời điểm bệnh nhân xuất hiện triệu chứng bất thường đầu tiên để báo cho bác sĩ cấp cứu.
            """
        },
        {
            "id": 2,
            "category": "CHUYÊN KHẢO SINH HỌC - UNG BƯỚU HỌC",
            "read_time": "14 phút đọc",
            "date": "05/09/2026",
            "author": "TS.BS. Nguyễn Văn Bình (Viện Nghiên cứu Ung thư Quốc gia)",
            "title": "Bản Chất Sinh Học Của Khối U: Khi Tế Bào 'Nổi Loạn' Và Đánh Cắp Hệ Miễn Dịch",
            "summary": "Tế bào ung thư không phải một kẻ xâm lược từ bên ngoài, mà chính là những tế bào bình thường bị hỏng hóc cơ chế kiểm soát mã gen. Tìm hiểu cách chúng bẻ khóa gen p53, xây dựng mạng lưới mạch máu nuôi dưỡng, ngụy trang qua điểm kiểm soát miễn dịch và 7 chỉ dấu CAUTION kinh điển.",
            "image": "https://images.unsplash.com/photo-1579154204601-01588f351e67?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: CUỘC NỔI LOẠN TỪ BÊN TRONG CẤU TRÚC DI TRUYỀN

Nhiều người bệnh vẫn hình dung ung thư giống như một loại ký sinh trùng hay vi khuẩn từ bên ngoài xâm nhập vào nội tạng. Nhưng về bản chất sinh học phân tử, **ung thư là một bệnh lý của chính bộ gen (DNA)** của cơ thể người.

Cơ thể chúng ta được vận hành bởi hơn 30.000 tỷ tế bào. Mỗi ngày có hàng tỷ tế bào già cỗi chết đi và tế bào mới được nhân đôi tạo ra thay thế. Để duy trì trật tự này, tự nhiên trang bị cho tế bào hai nhóm gen cốt lõi:
1. **Gen tiền ung thư (Proto-oncogene):** Đóng vai trò như "chân ga", thúc đẩy tế bào phân chia khi cơ thể cần bù đắp mô tổn thương.
2. **Gen đè nén khối u (Tumor Suppressor Genes - tiêu biểu là p53):** Đóng vai trò như "chân phanh" và "thanh tra di truyền". Khi phát hiện DNA bị đứt gãy hoặc sai lệch, p53 sẽ dừng chu kỳ tế bào lại để sửa chữa. Nếu hỏng hóc không thể khắc phục, p53 sẽ ra lệnh kích hoạt chu trình tự sát lập trình (**Apoptosis**).

Khi các tác nhân môi trường (hóa chất công nghiệp, khói thuốc, tia cực tím, vi khuẩn HP, virus HPV) liên tục tấn công, gen p53 bị đột biến mất chức năng. Chiếc xe mất phanh: tế bào lỗi không chịu chết, biến thành tế bào bất tử và tiếp tục nhân đôi vô tội vạ.

---

## PHẦN 2: BA NĂNG LỰC SIÊU VIỆT GIÚP KHỐI U BÀNH TRƯỚNG

Giáo sư Robert Weinberg và Douglas Hanahan đã đúc kết các đặc tính sinh học giúp tế bào ung thư lũng đoạn cơ thể:

### 1. Hiệu ứng Warburg (Chuyển hóa háu đường kỵ khí):
Tế bào bình thường sử dụng chu trình oxy hóa phosphoryl hóa trong ty thể để tạo năng lượng một cách hiệu quả (tạo ra 36 ATP từ 1 phân tử glucose). Tế bào ung thư thì khác: chúng chuyển sang quá trình đường phân kỵ khí dù trong điều kiện đầy đủ oxy. Chúng tiêu thụ glucose gấp hàng chục lần tế bào thường, tiết ra axit lactic làm toan hóa môi trường xung quanh, tiêu diệt các mô lân cận để tạo không gian bành trướng.

### 2. Tân sinh mạch máu (Angiogenesis):
Một khối u khi phát triển quá đường kính 2 mm sẽ bị thiếu hụt oxy và chất dinh dưỡng từ mạng mạch máu sẵn có. Để giải quyết, khối u tiết ra ồ ạt yếu tố tăng trưởng nội mô mạch máu (**VEGF**), buộc cơ thể phải mọc ra những nhánh mạch máu dị dạng mới đâm thẳng vào tim khối u để bơm máu nuôi chúng. Mạng mạch này lỏng lẻo, tạo điều kiện cho tế bào u chui vào lòng mạch để di căn xa.

### 3. Lớp áo tàng hình bẻ khóa miễn dịch (PD-L1):
Tế bào miễn dịch T của cơ thể vốn có nhiệm vụ đi tuần tra và tiêu diệt các tế bào bất thường. Để bảo vệ các mô khỏe mạnh khỏi bị tế bào T tấn công nhầm, tế bào lành thường biểu hiện phân tử khóa PD-L1. Tế bào ung thư đã học cách biểu hiện dày đặc phân tử PD-L1 trên bề mặt của chúng. Khi tế bào T tiến lại gần, PD-L1 sẽ gắn vào thụ thể PD-1 của tế bào T, đóng vai trò như một chiếc "thẻ căn cước giả" ru ngủ tế bào miễn dịch, khiến khối u tự do phát triển mà không bị thanh trừ.

---

## PHẦN 3: BẢNG CHỈ DẤU LÂM SÀNG C.A.U.T.I.O.N

Ở giai đoạn 1 và giai đoạn tại chỗ (In Situ), khối u nhỏ chưa chèn ép bao dây thần kinh nên **hoàn toàn không đau đớn**. Việc chờ đợi đến khi "thấy đau dữ dội" mới đi khám đồng nghĩa với việc bệnh nhân đã bỏ lỡ cơ hội vàng. Hiệp hội Ung bướu Quốc tế khuyến cáo người dân cần ghi nhớ quy tắc CAUTION:

* **C (Change in bowel/bladder habits):** Thay đổi thói quen đại tiểu tiện kéo dài quá 2 tuần: phân dẹt hình lá lúa, táo bón xen kẽ tiêu lỏng vô cớ, mót rặn giả, tiểu rắt, tiểu ngắt quãng.
* **A (A sore that does not heal):** Vết trợt loét ở niêm mạc lưỡi, khóe môi, vòm họng hoặc vùng sinh dục không liền sau 14 ngày dù đã bôi thuốc nhiệt miệng.
* **U (Unusual bleeding or discharge):** Hiện tượng xuất huyết lạ: ho đờm có dính tia máu, đại tiện phân đen như bã cà phê hoặc lẫn máu tươi, phụ nữ mãn kinh nhiều năm bỗng nhiên thấy ra máu âm đạo bất thường.
* **T (Thickening or lump):** Sờ thấy cục rắn chắc không đau ở tuyến vú, vùng nách, hạch thượng đòn hoặc góc hàm.
* **I (Indigestion or difficulty swallowing):** Nuốt nghẹn tăng dần từ thức ăn đặc sang thức ăn lỏng, cảm giác đầy tức thượng vị kèm ợ chua dai dẳng không giảm khi đổi chế độ ăn.
* **O (Obvious change in wart or mole):** Nốt ruồi thay đổi tính chất theo bảng ABCD: Bất đối xứng (Asymmetry), Bờ nham nhở (Border), Màu sắc không đồng nhất (Color), Đường kính trên 6 mm (Diameter).
* **N (Nagging cough or hoarseness):** Ho khan kích ứng kéo dài trên 3 tuần không sốt, không đờm, khàn tiếng biến đổi giọng nói kéo dài không rõ căn nguyên.

---

## PHẦN 4: HÀNH ĐỘNG DỰ PHÒNG CHỦ ĐỘNG TẦNG TẾ BÀO

1. **Thanh lọc các tác nhân gây đột biến:** Cắt đứt hoàn toàn nguồn khói thuốc lá chủ động và thụ động; kiểm soát nhiễm trùng mạn tính (tiêm vắc-xin ngừa HPV, viêm gan B; điều trị triệt để vi khuẩn Helicobacter pylori trong dạ dày).
2. **Chế độ ăn giảm viêm:** Tăng cường nhóm thực phẩm giàu chất chống oxy hóa tự nhiên (polyphenol, anthocyanin, sulforaphane trong bông cải xanh) để trung hòa các gốc tự do ROS trước khi chúng kịp bẻ gãy chuỗi xoắn kép DNA.
            """
        },
        {
            "id": 3,
            "category": "DƯỢC LÝ SINH HỌC PHÂN TỬ",
            "read_time": "15 phút đọc",
            "date": "04/09/2026",
            "author": "Viện Nghiên cứu Dược liệu Sinh học Phân tử",
            "title": "Nghiên Cứu Hiệp Đồng EGCG & Curcumin: 'Lá Chắn Xanh' Khóa Chu Kỳ Tế Bào Ung Thư",
            "summary": "Không dừng lại ở những bài thuốc dân gian truyền miệng, sự kết hợp giữa phân tử polyphenol trong trà xanh non (EGCG) và thân rễ củ nghệ vàng (Curcumin) đã được hàng loạt tạp chí y học quốc tế chứng minh có khả năng cộng hưởng triệt tiêu tín hiệu viêm NF-κB và chặn đứng chu kỳ phân chia tế bào bất thường.",
            "image": "https://images.unsplash.com/photo-1544787219-7f47ccb76574?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: GIẢI MÃ HAI PHÂN TỬ HOẠT TÍNH SINH HỌC KINH ĐIỂN

Trong tự nhiên, trà xanh non (*Camellia sinensis*) và nghệ vàng (*Curcuma longa*) là hai thảo dược đã được y học phương Đông sử dụng hàng ngàn năm. Tuy nhiên, bước sang thế kỷ 21, các nhà khoa học dược lý phân tử bắt đầu đi sâu giải mã cấu trúc không gian và đích tác động nội bào của hai hợp chất hoạt tính mạnh nhất trong chúng:

* **EGCG (Epigallocatechin-3-gallate):** Là catechin dồi dào nhất chiếm hơn 50-60% tổng lượng polyphenol của lá trà. Cấu trúc hóa học của EGCG gồm 8 nhóm hydroxyl (-OH) phenolic tự do, cho phép phân tử này vừa bẫy các gốc tự do oxy hóa (ROS) với hoạt tính chống oxy hóa cao gấp hàng chục lần Vitamin E, vừa có khả năng chui qua màng tế bào gắn đặc hiệu vào các thụ thể kinase.
* **Curcumin (Diferuloylmethane):** Là một polyphenol kỵ nước có sắc tố vàng cam đặc trưng. Curcumin nổi tiếng với khả năng can thiệp sâu vào các con đường dẫn truyền tín hiệu điều hòa phản ứng viêm và ức chế sự biểu hiện của các enzyme gây viêm như COX-2 và iNOS.

---

## PHẦN 2: HIỆU ỨNG HIỆP ĐỒNG (SYNERGY) - KHI 1 + 1 > 2

Một rào cản lớn trong việc ứng dụng Curcumin và EGCG đơn lẻ trong y khoa là **vấn đề sinh khả dụng**: Curcumin kém tan trong nước, nhanh chóng bị gan giáng hóa và đào thải qua mật; trong khi EGCG dễ bị phân hủy bởi nhiệt độ và môi trường kiềm ruột non. 

Tuy nhiên, hàng loạt công trình nghiên cứu tiền lâm sàng đăng tải trên các tạp chí ung bướu học uy tín (International Journal of Molecular Sciences, Cancer Research, PLoS ONE) đã phát hiện ra: khi phối hợp hai hoạt chất này ở tỷ lệ tối ưu, chúng tạo ra **Chỉ số kết hợp (Combination Index - CI) nhỏ hơn 1**, biểu thị cho hiện tượng hiệp đồng tương hỗ mạnh mẽ.

### Công trình 1: Ức chế đồng thời hai chốt kiểm soát chu kỳ tế bào (Cell Cycle Arrest)
* *Tạp chí:* International Journal of Molecular Sciences (PMID: 23739680).
* *Cơ chế:* Để một tế bào phân chia, nó phải vượt qua các trạm kiểm soát nghiêm ngặt: chuyển từ pha G1 sang pha S (tổng hợp DNA) và từ pha S sang pha G2/M (nguyên phân). 
  * EGCG nồng độ thấp nhắm vào việc ức chế phức hợp **Cyclin D1 / CDK4**.
  * Curcumin phong tỏa phức hợp **Cyclin B1 / CDC2**.
  * Sự phối hợp đồng thời đã "khóa chặt" tế bào dị sản ở cả hai đầu, ngăn cản hoàn toàn việc nhân bản bộ gen bị lỗi mà không hề gây độc tính lên các tế bào biểu mô lành mạnh.

### Công trình 2: Triệt tiêu tế bào gốc ung thư vú qua trục NF-κB và STAT3
* *Tạp chí:* BMC Cancer & PubMed Central (PMC4290892).
* *Phát hiện:* Tế bào gốc ung thư (Cancer Stem Cells - CSCs) được xem là "hạt giống ngầm" gây ra hiện tượng kháng hóa chất và tái phát di căn sau phẫu thuật. 
  * Nghiên cứu nuôi cấy khối u cầu vú (mammospheres) cho thấy: Dùng riêng lẻ EGCG 10 µM chỉ giảm 30% số lượng cụm u; dùng riêng Curcumin 10 µM giảm 35%. 
  * Nhưng khi kết hợp cả hai ở cùng liều thấp đó, tỷ lệ hình thành khối cầu giảm tới hơn **85%**. Kỹ thuật Western Blot khẳng định phức hợp này đã triệt hạ con đường truyền tin của **STAT3** (yếu tố giúp tế bào gốc u sống sót) và dập tắt phức hợp phiên mã **NF-κB** (ngòi nổ của phản ứng viêm mạn tính sinh ung).

### Công trình 3: Tái kích hoạt protein kìm hãm khối u p21 trên dòng tế bào kháng Apoptosis
* *Tạp chí:* Molecular and Cellular Biochemistry (PMC4576954).
* *Thực nghiệm:* Trên dòng tế bào ung thư tiền liệt tuyến kháng thuốc PC3 (dòng tế bào đã mất hoàn toàn gen chỉ huy p53).
  * Thông thường tế bào mất p53 sẽ không thể kích hoạt quy trình tự chết. 
  * Tuy nhiên, sự xuất hiện đồng thời của Curcumin và EGCG đã kích hoạt con đường độc lập p53, đánh thức protein ức chế chu kỳ tế bào **p21 (WAF1/CIP1)**. Nồng độ p21 vọt lên đã kích hoạt enzyme Caspase-3 và Caspase-9, buộc các tế bào kháng thuốc phải tự vỡ nhân và phân rã theo chu trình Apoptosis tự nhiên.

---

## PHẦN 3: CẨM NANG THỰC HÀNH TỐI ƯU SINH KHẢ DỤNG TẠI NHÀ

Để đưa những nghiên cứu phòng thí nghiệm vào đời sống một cách khoa học, người tiêu dùng cần tránh những sai lầm làm mất hoạt tính của hai dược liệu này:

1. **Bí quyết với Curcumin - Quy tắc hạt tiêu đen và chất béo:**
   * Hoạt chất **Piperine** trong hạt tiêu đen có khả năng ức chế enzyme glucuronidation tại gan và thành ruột non. Nghiên cứu của Đại học Y khoa Michigan chỉ ra rằng kết hợp chỉ 1% Piperine cùng Curcumin làm tăng sinh khả dụng của Curcumin lên tới **2000% (gấp 20 lần)**.
   * Curcumin tan trong chất béo (lipophilic). Do đó, nghệ nên được nấu cùng các loại dầu lành mạnh (dầu ô-liu, dầu dừa, nước cốt dừa) hoặc dùng dạng công nghệ Nano-Curcumin hạt siêu nhỏ để hấp thu thẳng vào tĩnh mạch cửa.
2. **Bí quyết với EGCG - Nhiệt độ và môi trường axit:**
   * Không bao giờ dùng nước sôi sùng sục 100°C để pha trà xanh, vì nhiệt độ quá cao sẽ làm biến tính vòng phenolic của catechin, khiến trà bị chát đắng và mất hoạt tính. Nhiệt độ lý tưởng là **80°C - 85°C**, hãm trong vòng 3 đến 5 phút.
   * EGCG rất nhạy cảm với pH kiềm nhẹ ở ruột non. Vắt thêm **vài giọt nước cốt chanh** vào ly trà xanh ấm (bổ sung Vitamin C - Axit Ascorbic) sẽ tạo môi trường axit nhẹ bảo vệ cấu trúc phân tử EGCG ổn định hơn gấp 3 lần trước khi được hấp thu qua niêm mạc ruột.
            """
        },
        {
            "id": 4,
            "category": "Y HỌC THỰC CHỨNG - UNG BƯỚU HIỆN ĐẠI",
            "read_time": "13 phút đọc",
            "date": "04/09/2026",
            "author": "BS. Ung bướu Lâm sàng",
            "title": "Ung Thư Có Thể Chữa Khỏi Hoàn Toàn? Cuộc Cách Mạng Thuốc Trúng Đích & Liệu Pháp Miễn Dịch",
            "summary": "Thay vì coi ung thư như bản án tử hình, y học chính xác thế kỷ 21 đã định nghĩa lại mục tiêu điều trị: kiểm soát khỏi triệt để ở giai đoạn sớm và biến ung thư giai đoạn muộn thành bệnh mạn tính sống chung hòa bình như đái tháo đường hay tăng huyết áp.",
            "image": "https://images.unsplash.com/photo-1532938911079-1b06ac7ceec7?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: ĐỊNH NGHĨA LẠI KHÁI NIỆM "CHỮA KHỎI"

Trong ngôn ngữ thường ngày, "chữa khỏi" được hiểu là căn bệnh vĩnh viễn biến mất không bao giờ quay lại. Tuy nhiên, trong ung bướu học lâm sàng, do đặc tính vi di căn vô hình của các tế bào đơn lẻ, khái niệm này được lượng hóa bằng chỉ số: **Tỷ lệ sống thêm không tái phát sau 5 năm (5-Year Disease-Free Survival)**.

Nếu một bệnh nhân sau khi kết thúc phác đồ điều trị triệt căn (phẫu thuật, xạ trị, hóa chất) duy trì được mốc 5 năm mà các xét nghiệm sinh học phân tử, chụp cắt lớp vi tính (CT), cộng hưởng từ (MRI) hoặc PET-CT không phát hiện bất kỳ tế bào ung thư nào, thì xác suất bệnh bùng phát trở lại sẽ hạ xuống mức cực thấp, tương đương nguy cơ mắc ung thư mới của một người bình thường trong cùng độ tuổi.

---

## PHẦN 2: NHỮNG LOẠI UNG THƯ ĐẠT TỶ LỆ KHỎI TRÊN 90% Ở GIAI ĐOẠN SỚM

Nhờ các chương trình tầm soát dân số trên diện rộng và trang thiết bị nội soi phóng đại, ngày càng có nhiều ca bệnh được bắt giữ ngay ở giai đoạn biểu mô (Stage I):

* **Ung thư tuyến giáp thể nhú:** Nếu phát hiện khi u còn khu trú trong bao giáp, phẫu thuật cắt thùy hoặc toàn bộ tuyến giáp đem lại tỷ lệ sống sau 5 năm lên tới **hơn 98%**, bệnh nhân duy trì tuổi thọ tự nhiên bình thường.
* **Ung thư vú giai đoạn 1:** Phẫu thuật bảo tồn kết hợp xạ trị và liệu pháp nội tiết giúp tỷ lệ chữa khỏi đạt **95 - 99%**, bệnh nhân không cần cắt bỏ toàn bộ bầu ngực.
* **Ung thư đại trực tràng giai đoạn tiền xâm lấn (Polyp thoái hóa ác tính):** Can thiệp cắt polyp qua nội soi ống mềm đại tràng không cần mổ hở mang lại tỷ lệ khỏi bệnh gần như **100%**.
* **Ung thư cổ tử cung giai đoạn 0 (CIS):** Chỉ cần một thủ thuật khoét chóp cổ tử cung đơn giản bằng vòng điện (LEEP), người phụ nữ có thể bảo tồn hoàn toàn thiên chức làm mẹ và loại bỏ mầm mống ung thư vĩnh viễn.

---

## PHẦN 3: KỶ NGUYÊN Y HỌC CHÍNH XÁC: THUỐC TRÚNG ĐÍCH VÀ MIỄN DỊCH

Trước đây, hóa trị cổ điển được ví như việc "ném bom rải thảm": thuốc truyền vào tĩnh mạch tiêu diệt cả tế bào ung thư lẫn các tế bào phân chia nhanh khỏe mạnh của cơ thể (tế bào tủy xương, nang tóc, niêm mạc dạ dày), dẫn đến rụng tóc, thiếu máu, suy kiệt trầm trọng.

Y học hiện đại thế kỷ 21 đã bước sang kỷ nguyên **Y học chính xác (Precision Medicine)** với hai vũ khí tối thượng:

### 1. Liệu pháp trúng đích (Targeted Therapy):
Bác sĩ tiến hành sinh thiết lỏng hoặc giải trình tự gen của khối u để tìm đột biến đặc hiệu. 
* Ví dụ: Bệnh nhân ung thư phổi không tế bào nhỏ có đột biến gen **EGFR** sẽ được chỉ định uống các viên thuốc ức chế men Tyrosine Kinase (TKI như Osimertinib, Gefitinib). Viên thuốc đi khắp cơ thể nhưng chỉ gắn chặt vào thụ thể EGFR đột biến trên tế bào u để ngắt nguồn năng lượng của chúng, hoàn toàn không làm hại tế bào phổi lành, không gây rụng tóc hay nôn ói.

### 2. Liệu pháp miễn dịch giải phóng điểm kiểm soát (Immunotherapy):
Vinh danh giải Nobel Y học năm 2018 (trao cho James Allison và Tasuku Honjo).
* Thuốc kháng thể đơn dòng (như Pembrolizumab, Nivolumab) không trực tiếp giết tế bào ung thư. Chúng đến gắn vào thụ thể PD-1 của tế bào lympho T hoặc PD-L1 của tế bào u, tháo bỏ chiếc mặt nạ tàng hình của khối u.
* Hệ miễn dịch của người bệnh bừng tỉnh, nhận diện khối u là dị vật và điều động lực lượng đại thực bào, tế bào T độc tự nhiên tiêu diệt khối u từ gốc rễ. Rất nhiều bệnh nhân ung thư hắc tố da (Melanoma) hay ung thư phổi giai đoạn 4 di căn não đã có sự thoái lui u kỳ diệu và kéo dài sự sống thêm nhiều năm với chất lượng cuộc sống cao.
            """
        },
        {
            "id": 5,
            "category": "DỊCH TỄ & ĐỜI SỐNG ĐÔ THỊ",
            "read_time": "14 phút đọc",
            "date": "03/09/2026",
            "author": "Hội Dịch tễ học Ung bướu",
            "title": "Báo Động Đỏ: Tại Sao Ung Thư Đường Tiêu Hóa Lại Trẻ Hóa Khủng Khiếp Ở Nhóm Dưới 40 Tuổi?",
            "summary": "Các nghiên cứu thống kê trên toàn cầu giai đoạn 2020 - 2026 cho thấy tỷ lệ ung thư đại trực tràng và dạ dày ở người trẻ dưới 40 tuổi tăng với tốc độ gần 2-3% mỗi năm. Cùng bóc tách 5 thủ phạm quen thuộc trong nhịp sống đô thị đang biến đổi hệ vi sinh và bào mòn hàng rào niêm mạc.",
            "image": "https://images.unsplash.com/photo-1517838277536-f5f99be501cd?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: THỰC TRẠNG ĐÁNG LO NGẠI TẠI CÁC BỆNH VIỆN TUYẾN CUỐI

Nếu như 20 năm trước, một ca ung thư đại tràng ở độ tuổi 28 hay ung thư dạ dày ở độ tuổi 32 được xếp vào diện hiếm gặp, thì ngày nay, các khoa phẫu thuật tiêu hóa tiếp nhận hàng loạt ca bệnh nhân còn rất trẻ.

Theo số liệu từ Cơ quan Nghiên cứu Ung thư Quốc tế (IARC), ung thư đại trực tràng khởi phát sớm (Early-Onset Colorectal Cancer) đang gia tăng trên toàn cầu. Đáng lo ngại hơn, ung thư ở người trẻ thường có **độ ác tính sinh học cao hơn**: tế bào kém biệt hóa, tỷ lệ u thể nhẫn cao, tốc độ di căn hạch nhanh và thường chỉ được phát hiện ở giai đoạn muộn do tâm lý chủ quan "mình còn trẻ, không thể mắc ung thư".

---

## PHẦN 2: NĂM MẮT XÍCH ĐỘC HẠI TRONG ĐỜI SỐNG CÔNG NGHIỆP HÓA

### 1. Sự xâm lăng của thực phẩm siêu chế biến (Ultra-Processed Foods):
* Giới trẻ tiêu thụ lượng lớn xúc xích, thịt xông khói, dăm bông, đồ hộp. Các loại thịt đỏ chế biến sẵn này chứa muối **Nitrit và Nitrat** để tạo màu hồng và chống vi khuẩn clostridium. Khi vào dạ dày gặp môi trường axit, chúng phản ứng với các amin tạo thành hợp chất **N-nitroso** - chất gây ung thư nhóm 1 làm gãy chuỗi DNA.
* Thói quen ăn nướng BBQ trên than hoa: Mỡ động vật nhỏ giọt xuống than hồng ở nhiệt độ cao trên 300°C bốc khói tạo ra các hydrocacbon thơm đa vòng (**PAH**) và amin dị vòng (**HCA**), bám chặt vào miếng thịt cháy cạnh đưa thẳng vào lòng ruột non.

### 2. Hội chứng "Đoản mạch vi sinh" do thiếu hụt chất xơ:
* Chế độ ăn uống hiện đại thừa năng lượng nhưng thiếu chất xơ trầm trọng. Chất xơ không chỉ giúp nhuận tràng cơ học, mà quan trọng hơn, chất xơ hòa tan là thức ăn sống còn của hệ lợi khuẩn đại tràng.
* Khi lợi khuẩn tiêu hóa chất xơ, chúng lên men tạo ra các **Axit béo chuỗi ngắn (SCFA)**, đặc biệt là **Butyrate**. Butyrate chính là nguồn năng lượng nuôi sống tế bào biểu mô niêm mạc đại tràng, đồng thời có khả năng kháng viêm và kích hoạt quá trình tự chết của các polyp ác tính. Thiếu chất xơ, lớp chất nhầy bảo vệ ruột bị mỏng đi, độc tố tiếp xúc trực tiếp với niêm mạc gây viêm loét mạn tính sinh u.

### 3. Béo phì tạng và tình trạng viêm mạn tính cấp độ thấp:
* Ngồi văn phòng liên tục 8 - 10 tiếng, lười vận động thể chất khiến mỡ thừa tích tụ quanh các cơ quan nội tạng. 
* Mô mỡ nội tạng không phải là kho dự trữ trơ ì; chúng là một cơ quan nội tiết bất thường, liên tục giải phóng các cytokine gây viêm như **TNF-alpha, IL-6** và làm tăng nồng độ yếu tố tăng trưởng tương tự insulin (**IGF-1**). IGF-1 nồng độ cao trong máu đóng vai trò như một chất kích thích cực mạnh, thúc đẩy các tế bào biểu mô niêm mạc phân chia mất kiểm soát.

### 4. Thức khuya triền miên và sự sụp đổ của Melatonin:
* Mắt tiếp xúc với ánh sáng xanh từ màn hình điện thoại, máy tính sau 23h đêm làm ức chế hoàn toàn tuyến tùng tiết ra hormone **Melatonin**.
* Melatonin không chỉ mang lại giấc ngủ sâu; trong sinh học phân tử, Melatonin là một trong những chất chống oxy hóa nội sinh mạnh nhất cơ thể, chịu trách nhiệm chính trong việc kiểm tra, sửa chữa các sai sót sao chép DNA diễn ra vào ban đêm. Thức khuya liên tục đồng nghĩa với việc tước bỏ lực lượng thanh tra mã gen của tế bào.

### 5. Hạt vi nhựa (Microplastics) và hóa chất gây rối loạn nội tiết:
* Thói quen đựng thức ăn nóng, nước sôi trong các loại túi nilon tái chế, cốc nhựa dùng một lần chứa **Bisphenol A (BPA)** và **Phthalate**. 
* Khi gặp nhiệt độ cao và dầu mỡ, các hạt vi nhựa và monomer hóa học thôi nhiễm trực tiếp vào thức ăn, bắt chước hormone estrogen làm rối loạn nội tiết tố cơ thể và phá vỡ hàng rào miễn dịch màng nhầy ruột.

---

## PHẦN 3: LỜI KHUYÊN HÀNH ĐỘNG DỰ PHÒNG THỰC TẾ

* **Đưa chất xơ trở lại bàn ăn:** Đảm bảo tối thiểu 25 - 30 g chất xơ/ngày thông qua rau xanh lá đậm, các loại đậu hạt, củ quả nguyên vỏ.
* **Thay đổi thứ tự ăn uống:** Bắt đầu bữa ăn bằng rau luộc/salad $\rightarrow$ ăn món giàu đạm $\rightarrow$ cuối cùng mới ăn cơm/tinh bột.
* **Chủ động nội soi tầm soát:** Bất kỳ ai trên 35 tuổi nếu có các triệu chứng đầy bụng khó tiêu kéo dài, hoặc trong gia đình có người thân từng mắc polyp đại tràng, ung thư tiêu hóa thì bắt buộc phải chủ động đi nội soi đường tiêu hóa (dạ dày - đại tràng) ít nhất một lần để phát hiện và gắp bỏ các polyp tiền ung thư khi chúng còn ở dạng lành tính.
            """
        },
        {
            "id": 6,
            "category": "DINH DƯỠNG & TIM MẠCH DỰ PHÒNG",
            "read_time": "12 phút đọc",
            "date": "03/09/2026",
            "author": "BS. Dinh dưỡng Lâm sàng",
            "title": "Chế Độ Ăn DASH: 'Chiếc Phanh' Thủy Lực Cho Hệ Mạch Máu Của Người Tăng Huyết Áp",
            "summary": "Tăng huyết áp được mệnh danh là kẻ giết người thầm lặng phá hủy thận, tim và não. Phương pháp dinh dưỡng DASH (Dietary Approaches to Stop Hypertension) đã được chứng minh lâm sàng có khả năng hạ từ 8 đến 14 mmHg huyết áp tâm thu, tương đương với hiệu lực của một liều thuốc hạ áp đơn trị liệu.",
            "image": "https://images.unsplash.com/photo-1498837167922-ddd27525d352?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: ÁP LỰC THỦY LỰC VÀ SỰ BÀO MÒN NỘI MÔ MẠCH MÁU

Huyết áp là áp lực của dòng máu tác động lên thành động mạch. Khi chỉ số này vượt quá 140/90 mmHg, mỗi nhát bóp của tim sẽ tống một luồng áp lực thủy lực cực lớn đập thẳng vào mạng lưới mao mạch mỏng manh của cơ thể. 

Hậu quả là lớp nội mô mạch máu bị xé rách li ti, tạo điều kiện cho mỡ máu chui vào tạo mảng xơ vữa; tiểu cầu thận bị xơ hóa dẫn đến suy thận mạn; thất trái của tim phải co bóp quá tải dẫn đến phì đại cơ tim và suy tim toàn bộ.

---

## PHẦN 2: NGHỊCH LÝ ĐIỆN GIẢI THỜI HIỆN ĐẠI: THỪA NATRI, THIẾU KALI

Nguyên nhân gốc rễ thúc đẩy huyết áp leo thang trong chế độ ăn hiện đại nằm ở sự đảo lộn tỷ lệ điện giải nội bào:
* **Thừa Natri:** Natri (có trong muối ăn, bột ngọt, mắm cá) giữ nước trong lòng mạch máu theo quy luật thẩm thấu. Khi lượng natri nạp vào quá cao, thể tích tuần hoàn máu trong ống mạch tăng lên, buộc tim phải đập mạnh hơn, áp lực dồn lên thành mạch tăng vọt.
* **Thiếu Kali:** Kali là ion nội bào chịu trách nhiệm trực tiếp trong việc thư giãn các tế bào cơ trơn quanh thành mạch, đồng thời kích thích thận mở các kênh vận chuyển để trục xuất lượng Natri dư thừa ra ngoài qua nước tiểu. Người hiện đại ăn quá ít rau quả tươi khiến tỷ lệ Na/K bị lệch nghiêm trọng, giữ mạch máu luôn ở trạng thái co thắt căng thẳng.

---

## PHẦN 3: BA TRỤ CỘT CỦA CHẾ ĐỘ ĂN DASH

### 1. Giảm muối có kỷ luật:
* Khuyến cáo từ Viện Tim Phổi Huyết học Quốc gia Hoa Kỳ (NHLBI): Giới hạn lượng muối dưới **5 g muối ăn/ngày** (tương đương khoảng 2.000 mg Natri nguyên chất). Với người đã có bệnh tim mạch hoặc suy thận, hạ xuống dưới **3.5 g muối/ngày**.
* **Nhận diện các "ổ muối" giấu mặt:** 
  * 1 gói mì ăn liền chứa tới 4.2 g muối (gần đủ hạn mức cả ngày).
  * 1 thìa canh nước mắm nguyên chất chứa khoảng 2.5 g muối.
  * 100g giò lụa, chả lụa chứa 1.8 g muối do phụ gia phosphate tạo độ giòn dai.

### 2. Tăng cường Kali và Magie từ thực vật tự nhiên:
* Mỗi ngày cần nạp tối thiểu 4.700 mg Kali thông qua thực phẩm giàu kali tự nhiên: **Cải bó xôi (rau chân vịt), chuối tiêu chín vừa, khoai lang nướng nguyên vỏ, súp lơ xanh, bơ sáp, cà chua bi**.
* Magie tham gia vào hơn 300 phản ứng enzym, giúp chống co thắt tiểu động mạch. Nguồn cung cấp Magie dồi dào nhất là các loại hạt thô: **Hạnh nhân, hạt điều, hạt bí ngô, hạt chia và yến mạch nguyên cám**.

### 3. Cắt giảm chất béo bão hòa và cholesterol:
* Chuyển đổi hoàn toàn mỡ lợn, bơ động vật sang dầu thực vật chứa chất béo không bão hòa đơn và đa: **Dầu ô-liu ép lạnh, dầu hạt cải, dầu quả bơ**.
* Loại bỏ thịt đỏ có nhiều mỡ giắt, thay thế bằng đạm trắng từ **ức gà bỏ da, cá hồi, cá thu, đậu phụ**.
            """
        },
        {
            "id": 7,
            "category": "NHI KHOA & HÔ HẤP DỰ PHÒNG",
            "read_time": "13 phút đọc",
            "date": "02/09/2026",
            "author": "BS. CKI Nhi khoa Hoàng Thị Lan",
            "title": "Bệnh Hô Hấp Mùa Lạnh Ở Trẻ: Cơ Chế Bội Nhiễm & Chiến Lược Giữ Ấm '4 Điểm Vàng'",
            "summary": "Nhiệt độ hạ thấp kèm không khí khô hanh là khắc tinh làm tê liệt hàng rào nhầy - lông chuyển bảo vệ biểu mô đường thở của trẻ. Cha mẹ cần nắm vững kỹ năng nhận diện thở gắng sức và tuyệt đối tránh những thói quen ủ ấm sai cách gây nhiễm lạnh ngược.",
            "image": "https://images.unsplash.com/photo-1516733725897-1aa73b87c8e8?w=1000&auto=format&fit=crop&q=80",
            "content": r"""
## PHẦN 1: ĐẶC ĐIỂM SINH LÝ ĐƯỜNG THỞ NON NỚT CỦA TRẺ

Đường thở của trẻ nhỏ, đặc biệt là lứa tuổi nhũ nhi dưới 2 tuổi, có đường kính vô cùng nhỏ hẹp, mạch máu dưới niêm mạc phong phú nhưng các cấu trúc sụn nâng đỡ lại mềm yếu.

Lớp biểu mô đường hô hấp được bao phủ bởi hàng triệu sợi lông chuyển siêu vi (cilia) đung đưa nhịp nhàng từ 1.000 đến 1.500 lần/phút, phối hợp cùng lớp dịch nhầy tạo thành hệ thống băng chuyền cuốn trôi vi khuẩn, virus và bụi mịn ngược lên họng để tống ra ngoài.

Khi nhiệt độ môi trường giảm xuống dưới 18°C và độ ẩm không khí dưới 50%:
* Các mao mạch dưới niêm mạc co thắt đột ngột làm giảm lưu lượng máu nuôi và giảm bạch cầu tại chỗ.
* Dịch tiết nhầy bị khô quánh lại, làm tê liệt hoàn toàn chuyển động rung của lông chuyển.
* Cánh cửa bảo vệ sụp đổ, tạo cơ hội cho virus Hợp bào hô hấp (**RSV**), cúm mùa (Influenza) và vi khuẩn Phế cầu (*Streptococcus pneumoniae*) bám dính vào màng tế bào phế nang, nhân lên với tốc độ lũy thừa gây viêm tiểu phế quản và viêm phổi thùy cấp tính.

---

## PHẦN 2: CHIẾN LƯỢC GIỮ ẤM '4 ĐIỂM VÀNG' - TRÁNH Ủ ẤM QUÁ MỨC

Một sai lầm rất phổ biến của các bà mẹ là cho con mặc 4 đến 5 lớp áo len thật dày, bọc kín mít trong chăn khi ngủ. Trẻ nhỏ có chuyển hóa cơ bản rất cao, thân nhiệt dễ tăng lên. Khi bị ủ quá nóng, trẻ sẽ vã mồ hôi đầm đìa ở lưng và gáy. Mồ hôi không thoát được sẽ thấm ngược trở lại cơ thể, khiến trẻ bị nhiễm lạnh ngược và viêm phổi ngay giữa mùa đông.

Quy tắc chuẩn là: **Mặc áo nhiều lớp mỏng bằng vải cotton thoáng khí** và tập trung bảo vệ 4 vị trí thoát nhiệt nhạy cảm nhất:
1. **Cổ họng:** Cổ là nơi tập trung của thanh quản và khí quản nông sát da. Một chiếc khăn cotton mềm ôm nhẹ quanh cổ giúp giữ ổn định nhiệt độ luồng khí hít vào.
2. **Lồng ngực và lưng:** Giữ ấm khoang ngực bảo vệ phổi, nhưng phải thường xuyên kiểm tra bàn tay luồn vào lưng trẻ: nếu thấy da ẩm ướt mồ hôi, phải dùng khăn khô lau ngay và thay áo mỏng hơn.
3. **Lòng bàn chân:** Vùng tập trung rất nhiều đầu mút thần kinh và các thụ thể vận mạch. Luôn cho trẻ đi tất khô ráo, tránh để chân trần tiếp xúc trực tiếp với sàn gạch lạnh.
4. **Đỉnh đầu và thóp:** Với trẻ dưới 12 tháng tuổi, diện tích bề mặt đầu chiếm tỷ lệ rất lớn so với toàn bộ cơ thể, là nơi mất nhiệt nhanh nhất. Cần đội mũ thóp mỏng vừa vặn khi đưa trẻ ra ngoài trời gió rét.

---

## PHẦN 3: KỸ NĂNG ĐẾM NHỊP THỞ PHÁT HIỆN SỚM VIÊM PHỔI

* **Cách đếm chuẩn:** Cho trẻ nằm yên tĩnh hoặc ngủ say (không đếm khi trẻ đang quấy khóc hay đang bú). Vạch áo để lộ rõ bụng và ngực trẻ. Nhìn vào lồng ngực/bụng nhô lên hạ xuống: mỗi lần nhô lên rồi hạ xuống được tính là **1 nhịp**. Dùng đồng hồ bấm giây đếm trọn vẹn trong **đúng 1 phút (60 giây)**.
* **Ngưỡng nhịp thở nhanh theo độ tuổi của Tổ chức Y tế Thế giới (WHO):**
  * Trẻ dưới 2 tháng tuổi: $\ge 60$ lần/phút.
  * Trẻ từ 2 đến 11 tháng tuổi: $\ge 50$ lần/phút.
  * Trẻ từ 12 tháng đến 5 tuổi: $\ge 40$ lần/phút.
* **Dấu hiệu rút lõm lồng ngực (Báo động đỏ):** Nhìn vào phần ranh giới giữa ngực và bụng. Khi trẻ hít vào, lồng ngực bình thường sẽ phồng lên. Nếu thấy phần dưới lồng ngực lõm sâu vào một cách bất thường, chứng tỏ phổi đã bị đông đặc, phế nang giảm đàn hồi buộc cơ hoành và các cơ liên sườn phải co kéo hết công suất. Phải bế trẻ đến phòng cấp cứu của bệnh viện gần nhất ngay lập tức.
            """
        },
        {
            "id": 8,
            "category": "DỊCH TỄ HỌC NHIỆT ĐỚI",
            "read_time": "12 phút đọc",
            "date": "01/09/2026",
            "author": "Khoa Bệnh Nhiệt đới & Dịch tễ",
            "title": "Mùa Mưa Lũ: Hiểm Họa Thoát Huyết Tương Trong Sốt Xuất Huyết & Vi Khuẩn Leptospira",
            "summary": "Mùa mưa không chỉ là thời điểm sinh sôi của muỗi vằn Aedes truyền virus Dengue mà còn ẩn chứa mầm bệnh xoắn khuẩn vàng da Leptospira qua nguồn nước ngập úng. Hiểu rõ giai đoạn nguy hiểm thực sự của bệnh và những điều cấm kỵ khi hạ sốt.",
            "image": "https://images.unsplash.com/photo-1515694346937-94d85e41e6f0?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: NGHỊCH LÝ GIAI ĐOẠN CỦA SỐT XUẤT HUYẾT DENGUE

Một quan niệm sai lầm kinh điển trong cộng đồng là: *Sốt xuất huyết thì khi đang sốt cao là nguy hiểm nhất, còn khi hết sốt là đã khỏi bệnh*. Thực tế lâm sàng tại các bệnh viện nhiệt đới hoàn toàn ngược lại:

Bệnh nhân sốt xuất huyết trải qua 3 giai đoạn:
1. **Giai đoạn sốt (Ngày 1 - 3):** Bệnh nhân sốt cao đột ngột 39 - 40°C, nhức hốc mắt dữ dội, đau đầu, đau mỏi cơ khớp. Giai đoạn này gây mệt mỏi nhưng rất hiếm khi đe dọa tính mạng.
2. **Giai đoạn nguy hiểm (Ngày 3 - 7):** Lúc này người bệnh thường **đã hạ sốt**, chỉ còn sốt nhẹ hoặc nhiệt độ về mức bình thường. Nhưng đây chính là lúc **cơn bão cytokine** kích hoạt phản ứng viêm toàn thân, làm tăng tính thấm thành mạch mao mạch. Huyết tương trong lòng mạch thoát ồ ạt ra các khoang màng phổi, màng bụng, màng tim.
   * Thể tích máu trong ống mạch cạn kiệt, máu bị cô đặc (chỉ số Hematocrit tăng vọt), dẫn đến tụt huyết áp kẹt, mạch nhanh nhỏ khó bắt và **sốc trụy tim mạch** chỉ trong vòng vài giờ nếu không được truyền dịch điện giải bù trừ kịp thời.
   * Đồng thời, tiểu cầu sụt giảm nghiêm trọng kết hợp tổn thương tế bào nội mô mạch máu gây chảy máu cam ồ ạt, chảy máu chân răng, nôn ra máu đen hoặc xuất huyết tạng nguy kịch.
3. **Giai đoạn hồi phục (Ngày 7 - 10):** Cơ thể bắt đầu tái hấp thu dịch từ các khoang mô kẽ trở lại lòng mạch máu. Lúc này phải hạn chế truyền dịch quá mức để tránh biến chứng phù phổi cấp quá tải tuần hoàn.

---

## PHẦN 2: SAI LẦM DÙNG THUỐC HẠ SỐT DẪN ĐẾN XUẤT HUYẾT TIÊU HÓA

* **Tự ý dùng Aspirin hoặc Ibuprofen (biệt dược như Gofen, Advil, Decolgen dạng kết hợp):** Các thuốc giảm đau hạ sốt nhóm NSAID này có cơ chế ức chế không hồi phục chức năng kết tập tiểu cầu và gây ăn mòn niêm mạc dạ dày. Khi bệnh nhân sốt xuất huyết đã bị hạ tiểu cầu mà uống thêm Aspirin/Ibuprofen, hậu quả sẽ là **chảy máu dạ dày dữ dội, xuất huyết tiêu hóa ồ ạt** không cầm được, rất dễ dẫn đến tử vong.
* **Nguyên tắc dùng thuốc an toàn duy nhất:** Chỉ sử dụng thuốc hạ sốt chứa hoạt chất **Paracetamol đơn chất** với liều lượng chuẩn từ **10 - 15 mg/kg thể trọng cho một lần uống**, khoảng cách giữa hai lần uống tối thiểu từ **4 đến 6 tiếng**, tổng liều không được vượt quá 60 mg/kg/24 giờ để tránh gây hoại tử tế bào gan cấp tính.

---

## PHẦN 3: HIỂM HỌA XOẮN KHUẨN LEPTOSPIRA TRONG NƯỚC BẨN

Mùa mưa bão ngập úng đưa đến một mối nguy dịch tễ khác mang tên **Bệnh xoắn khuẩn vàng da Leptospira**.
* **Đường lây truyền:** Xoắn khuẩn ký sinh trong ống thận của các loài gặm nhấm (chuột cống) và gia súc, thải ra ngoài môi trường theo nước tiểu, tồn tại hàng tuần trong bùn lầy và nước ngập.
* **Cơ chế xâm nhập:** Khi lội nước ngập, xoắn khuẩn chui qua các vết trầy xước nhỏ xíu ở gót chân, kẽ ngón chân hoặc qua niêm mạc mắt mũi.
* **Biểu hiện bệnh:** Sốt cao rét run, đặc biệt là triệu chứng **đau dữ dội khối cơ bắp chân** (khiến người bệnh không dám bước đi), xung huyết đỏ rực kết mạc mắt. Sau vài ngày, bệnh nhân tiến triển vàng da, vàng mắt sẫm, suy gan, tiểu ra ít dần và suy thận cấp vô niệu.
* **Phòng ngừa:** Luôn đi ủng cao su cách nước khi phải dọn dẹp vệ sinh sau ngập lụt; khử trùng nguồn nước sinh hoạt bằng Cloramin B; rửa sạch chân tay bằng xà phòng sát khuẩn ngay sau khi tiếp xúc với nước bùn bẩn.
            """
        },
        {
            "id": 9,
            "category": "TIM MẠCH CHUYÊN SÂU",
            "read_time": "14 phút đọc",
            "date": "31/08/2026",
            "author": "BS. Nguyễn Tiến Toàn",
            "title": "Mảng Xơ Vữa Động Mạch: 'Quả Bom Nổ Chậm' Và Cơ Chế Nứt Vỡ Kích Hoạt Cơn Đột Tử",
            "summary": "Không giống một đường ống nước bị đóng cặn cơ học đơn thuần, mảng xơ vữa thực chất là một ổ viêm hoại tử lipid nằm sâu bên dưới lớp nội mạc. Nghịch lý y khoa: Chính những mảng xơ vữa mềm chỉ hẹp 30-40% mới là thủ phạm gây ra phần lớn các cơn nhồi máu cơ tim đột tử.",
            "image": "https://images.unsplash.com/photo-1559757175-5700dde675bc?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: QUÁ TRÌNH HÌNH THÀNH: TỪ HẠT MỠ OXY HÓA ĐẾN TẾ BÀO BỌT

Rất nhiều người vẫn lầm tưởng rằng mảng xơ vữa chỉ là mỡ bám dính ngoài bề mặt lòng mạch máu giống như dầu mỡ bám vào ống xả bồn rửa bát. Dưới kính hiển vi điện tử sinh lý bệnh học, xơ vữa là một **phản ứng viêm mạn tính phức tạp diễn ra sâu bên trong thành mạch (lớp dưới nội mạc)**:

1. **Khởi phát tổn thương nội mạc:** Khói thuốc lá, các gốc tự do, đường huyết tăng cao hoặc áp lực cơ học của dòng máu huyết áp cao làm rách các mối liên kết chặt chẽ của lớp tế bào nội mạc lót trong lòng động mạch.
2. **Sự xâm nhập và oxy hóa của LDL:** Các phân tử cholesterol xấu tỉ trọng thấp (**LDL-C**) lọt qua khe nứt nội mạc, chui xuống khoảng dưới nội mạc và bị mắc kẹt lại tại đây. Dưới tác động của các gốc tự do, chúng bị oxy hóa thành dạng độc hại mang tên **ox-LDL**.
3. **Sự xuất hiện của 'Tế bào bọt' (Foam cells):** Cơ thể nhận diện ox-LDL là vật chất lạ độc hại, lập tức phát tín hiệu hóa hướng động thu hút các tế bào bạch cầu đơn nhân (monocyte) từ dòng máu chui qua nội mạc biến thành đại thực bào. Đại thực bào ra sức "nuốt chửng" ox-LDL. Tuy nhiên, do nuốt quá nhiều lipid vượt quá khả năng tiêu hóa, các đại thực bào này trương phình lên, chứa đầy những bọc mỡ lấp lánh như bọt xà phòng, biến thành **tế bào bọt**.
4. **Lõi hoại tử và vỏ bao xơ:** Các tế bào bọt chết đi giải phóng một lượng lớn cholesterol tinh thể tích tụ lại tạo thành **Lõi lipid hoại tử**. Cơ thể cố gắng cô lập ổ mủ mỡ này bằng cách kích thích các tế bào cơ trơn tăng sinh collagen dệt thành một chiếc **Vỏ xơ (Fibrous cap)** ngăn cách lõi mỡ với dòng máu chảy trong lòng mạch.

---

## PHẦN 2: NGHỊCH LÝ LÂM SÀNG: MẢNG XƠ VỮA HẸP NHẸ NGUY HIỂM HƠN HẸP NẶNG

* **Mảng xơ vữa ổn định (Vỏ dày, lõi nhỏ):** Thường phát triển chậm rãi qua 10 - 20 năm, làm hẹp dần 70% đến 80% đường kính lòng mạch. Dạng này cản trở dòng máu khi người bệnh gắng sức, gây ra triệu chứng đau thắt ngực ổn định (đi bộ nhanh, leo cầu thang thấy nặng ngực, ngồi nghỉ thì hết). Vì có triệu chứng rõ rệt nên bệnh nhân thường đi khám sớm, đặt stent nong mạch chủ động.
* **Mảng xơ vữa không ổn định (Mảng xơ vữa mềm - Vỏ mỏng, lõi lipid khổng lồ):** Đây mới là quả bom nổ chậm đáng sợ nhất. Dạng mảng này chỉ làm hẹp nhẹ từ 30% đến 50% lòng mạch, dòng máu vẫn lưu thông thông thoáng nên bệnh nhân **hoàn toàn không có bất kỳ triệu chứng đau tức ngực nào**, điện tâm đồ nghỉ ngơi bình thường.
* **Biến cố nứt vỡ kích hoạt cục máu đông trong vài chục giây:**
  * Dưới một cơn stress tâm lý tột cùng, một trận cãi vã nảy lửa, hoặc huyết áp tăng vọt khi trời trở lạnh đột ngột, lớp vỏ xơ mỏng manh của mảng xơ vữa mềm bị nứt toác.
  * Lõi lipid hoại tử tiếp xúc trực tiếp với các yếu tố đông máu trong lòng mạch. Tiểu cầu trong dòng máu nhận diện vết nứt lập tức lao đến bám dính, giải phóng thromboxane A2 và ADP, kích hoạt thác ghềnh đông máu ngoại sinh. 
  * Chỉ trong vòng **30 đến 90 giây**, một **cục huyết khối đỏ tươi (Red Thrombus)** bện chặt bởi mạng lưới sợi fibrin hình thành, bít tắc hoàn toàn 100% động mạch vành nuôi tim. Dòng máu nuôi cơ tim ngừng phụt, cơ tim hoại tử cấp tính gây rung thất tử vong trước khi kịp gọi xe cấp cứu.

---

## PHẦN 3: CHIẾN LƯỢC ĐẢO NGƯỢC VÀ ỔN ĐỊNH VỎ MẢNG XƠ VỮA

* **Hạ LDL-C mục tiêu bằng Statin và ức chế PCSK9:** 
  * Với người có nguy cơ rất cao (đã từng nhồi máu cơ tim, đã đặt stent, hoặc bị đái tháo đường biến chứng): Mục tiêu LDL-C phải ép xuống dưới **1.4 mmol/L (< 55 mg/dL)**. Nồng độ mỡ máu siêu thấp này tạo ra một gradien nồng độ ngược, hút bớt cholesterol tự do từ lõi mảng xơ vữa quay trở lại máu để gan chuyển hóa tiêu hủy.
* **Duy trì lối sống kháng viêm nội mạc:** Ngừng hút thuốc lá vĩnh viễn (chất nicotine và carbon monoxide là thủ phạm hàng đầu gây rách vỏ xơ); duy trì vận động thể lực nhịp điệu (chạy bộ chậm, đạp xe, bơi lội) 30 phút mỗi ngày giúp tế bào nội mạc sản sinh ra **Nitric Oxide (NO)** - thần dược nội sinh làm giãn mạch và chống kết tập tiểu cầu tự nhiên.
            """
        },
        {
            "id": 10,
            "category": "SẢN PHỤ KHOA & NỘI TIẾT",
            "read_time": "14 phút đọc",
            "date": "30/08/2026",
            "author": "ThS.BS. Sản khoa Nguyễn Minh Hạnh",
            "title": "Đái Tháo Đường Thai Kỳ: Khi Hormone Bánh Nhau Đối Kháng Với Tuyến Tụy Người Mẹ",
            "summary": "Không đơn thuần là tăng đường huyết thoáng qua, đái tháo đường thai kỳ là một cuộc chiến chuyển hóa giữa mẹ và thai nhi. Nghiệm pháp dung nạp 75g đường huyết từ tuần 24 đến 28 là tiêu chuẩn vàng phát hiện sớm, chặn đứng nguy cơ tiền sản giật và sốc hạ đường huyết ở trẻ sơ sinh.",
            "image": "https://images.unsplash.com/photo-1584515979956-d9f6e5d09982?w=1000&auto=format&fit=crop&q=80",
            "content": r"""
## PHẦN 1: CƠ CHẾ SINH HỌC: SỰ ĐỐI KHÁNG HORMONE TỰ NHIÊN

Trong quá trình mang thai, bánh nhau không chỉ là cầu nối trao đổi dưỡng chất mà còn là một cơ quan nội tiết khổng lồ. Bánh nhau tiết ra hàng loạt hormone chuyển hóa như: **Human Placental Lactogen (hPL), Estrogen, Progesterone, Prolactin và Cortisol**.

Mục đích sinh học nguyên thủy của các hormone này là: **gây ra tình trạng kháng Insulin sinh lý nhẹ ở các mô cơ và mỡ của người mẹ**. Nhờ đó, đường glucose trong máu mẹ ít bị các tế bào của mẹ tiêu thụ hơn, nồng độ đường trong máu mẹ duy trì ở mức cao hơn để dễ dàng khuếch tán qua hàng rào bánh nhau nuôi nấng thai nhi phát triển não bộ và cơ thể.

Để cân bằng lại hiện tượng kháng insulin này, tuyến tụy của người mẹ bình thường phải tự động tăng công suất, sản xuất thêm lượng Insulin gấp **2 đến 3 lần** so với trước khi mang thai. Tuy nhiên, ở những thai phụ có sẵn cơ địa thừa cân, tuổi mang thai trên 35, hoặc có tiền sử gia đình đái tháo đường:
* Tế bào beta của đảo tụy không thể đáp ứng nổi nhu cầu tiết insulin khổng lồ này.
* Lượng đường trong máu người mẹ tăng vọt không kiểm soát, dẫn đến bệnh cảnh **Đái tháo đường thai kỳ (Gestational Diabetes Mellitus - GDM)**.

---

## PHẦN 2: NGUY CƠ TIỀM TÀNG CHO CẢ MẸ VÀ CON

### Đối với thai nhi trong bụng mẹ:
* **Thai to khổng lồ (Macrosomia):** Glucose từ máu mẹ dễ dàng đi qua bánh nhau, nhưng hormone Insulin của mẹ thì không qua được. Lượng đường huyết dồi dào tràn sang thai nhi sẽ kích thích tuyến tụy non nớt của em bé phải hoạt động cật lực, tăng tiết insulin riêng của mình. Insulin lại là một hormone đồng hóa cực mạnh, nó thúc đẩy tích tụ mô mỡ ồ ạt, đặc biệt là vùng vai và thân trên, khiến thai nhi nặng trên 4.000g. Hậu quả là mẹ bị đẻ khó, chuyển dạ kéo dài, nguy cơ gãy xương đòn hoặc **kẹt vai thai nhi** - một cấp cứu sản khoa vô cùng nguy hiểm có thể gây ngạt thở và liệt đám rối thần kinh cánh tay của bé.
* **Cơn sốc hạ đường huyết sơ sinh (Neonatal Hypoglycemia):** Đây là biến cố nguy kịch nhất ngay sau khi em bé chào đời. Khi còn trong bụng mẹ, tuyến tụy của bé đang quen với việc tiết lượng lớn insulin để xử lý dòng đường vô tận từ mẹ truyền sang. Ngay khi bác sĩ kẹp cắt dây rốn, nguồn đường tiếp tế từ mẹ bị ngắt đứt đột ngột. Trong khi đó, nồng độ insulin trong máu bé vẫn còn rất cao. Insulin thừa sẽ quét sạch toàn bộ lượng đường còn lại trong máu em bé chỉ sau vài chục phút, khiến bé bị co giật, hạ thân nhiệt, hôn mê và tổn thương não vĩnh viễn nếu không được cho bú sớm hoặc truyền glucose kịp thời.

### Đối với người mẹ:
* Nguy cơ cao bị **Tiền sản giật** (tăng huyết áp thai kỳ kèm protein niệu, phù nề, co giật sản giật đe dọa tính mạng).
* Tình trạng đa ối làm tử cung căng quá mức, dễ gây vỡ ối non, sinh non hoặc băng huyết sau sinh do đờ tử cung.
* **Hơn 50% phụ nữ** mắc đái tháo đường thai kỳ sẽ chính thức phát triển thành bệnh đái tháo đường tuýp 2 thực sự trong vòng 5 đến 10 năm sau sinh.

---

## PHẦN 3: NGHIỆM PHÁP DUNG NẠP 75G GLUCOSE (OGTT) VÀ DINH DƯỠNG

Đái tháo đường thai kỳ hầu như không có triệu chứng lâm sàng rầm rộ. Do đó, tất cả thai phụ đều được khuyến cáo thực hiện xét nghiệm từ **tuần thai thứ 24 đến 28**:
* Nhịn đói từ 8 đến 12 tiếng qua đêm $\rightarrow$ Lấy máu đo đường huyết đói lần 1.
* Uống một cốc nước hòa tan đúng **75 g đường glucose** chuẩn trong vòng 5 phút.
* Lấy máu xét nghiệm lần 2 sau 1 giờ, và lần 3 sau 2 giờ.
* **Tiêu chuẩn chẩn đoán (theo ADA/Bộ Y tế):** Chỉ cần **1 trong 3 chỉ số** sau đây bằng hoặc vượt ngưỡng là xác định mắc bệnh:
  * Lúc đói: $\ge 5.1\,\text{mmol/L}$ ($92\,\text{mg/dL}$).
  * Sau 1 giờ: $\ge 10.0\,\text{mmol/L}$ ($180\,\text{mg/dL}$).
  * Sau 2 giờ: $\ge 8.5\,\text{mmol/L}$ ($153\,\text{mg/dL}$).

### Chế độ ăn kiểm soát:
* **Quy tắc đĩa thức ăn 1/2 - 1/4 - 1/4:** Mỗi bữa chính gồm 1/2 là rau củ chất xơ ít tinh bột; 1/4 là thực phẩm giàu đạm (cá nạc, thịt nạc, trứng luộc, đậu hũ); 1/4 còn lại là tinh bột giải phóng chậm (gạo lứt, yến mạch, khoai lang luộc).
* **Tránh xa:** Nước mía, nước dừa nguyên trái uống liên tục, chè ngọt, sinh tố thêm sữa đặc và các loại trái cây có chỉ số đường huyết cực cao (nhãn, vải, sầu riêng, mít).
            """
        },
        {
            "id": 11,
            "category": "TÂM THỂ HỌC TIM MẠCH",
            "read_time": "14 phút đọc",
            "date": "29/08/2026",
            "author": "BS. Nguyễn Tiến Toàn",
            "title": "Hội Chứng Trái Tim Tan Vỡ (Takotsubo): Khi Nỗi Đau Tinh Thần Làm Biến Dạng Quả Tim",
            "summary": "Một cú sốc tâm lý cực độ hay áp lực công việc mãn tính có thể kích hoạt cơn bão hormone giao cảm tàn phá cơ tim. Hình ảnh thất trái phình to như một chiếc bình bắt bạch tuộc của ngư dân Nhật Bản là minh chứng rõ nét nhất cho mối liên hệ mật thiết giữa não bộ và trái tim.",
            "image": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=1000&auto=format&fit=crop&q=80",
            "content": """
## PHẦN 1: CÂU CHUYỆN LỊCH SỬ VÀ TÊN GỌI KỲ LẠ CỦA CƠN ĐAU TIM

Năm 1990, tại Nhật Bản, bác sĩ Hikaru Sato và các cộng sự lần đầu tiên báo cáo một nhóm bệnh nhân nhập viện với các triệu chứng lâm sàng giống hệt một cơn nhồi máu cơ tim tối cấp: đau thắt ngực dữ dội, nghẹt thở, vã mồ hôi, điện tâm đồ có đoạn ST chênh lên rõ rệt và các chỉ số men tim hoại tử (Troponin T, Troponin I) tăng cao trong máu.

Thế nhưng, khi các bác sĩ lập tức đưa bệnh nhân vào phòng can thiệp mạch để chụp động mạch vành qua da, họ sững sờ kinh ngạc: **Hệ thống động mạch vành nuôi tim của bệnh nhân hoàn toàn thông thoáng, không có bất kỳ mảng xơ vữa hay cục máu đông nào bít tắc**.

Khi tiến hành chụp buồng tâm thất trái cản quang, hình ảnh buồng tim co bóp hiện lên một cách dị thường: phần đáy tim vẫn bóp mạnh, nhưng toàn bộ vùng thân và mỏm thất trái bị liệt bất động hoàn toàn và phình to tròn ra. Hình dáng quả tim lúc đó giống hệt như chiếc **Tako-tsubo** - một loại bình gốm đáy tròn cổ hẹp mà các ngư dân truyền thống Nhật Bản hay dùng để thả xuống đáy biển bẫy bạch tuộc. Từ đó, hội chứng này chính thức mang tên **Bệnh cơ tim Takotsubo (Hội chứng Trái tim tan vỡ - Broken Heart Syndrome)**.

---

## PHẦN 2: CƠN BÃO CATECHOLAMINE HỦY DIỆT TỪ NÃO BỘ

Phần lớn các bệnh nhân mắc hội chứng Takotsubo (chiếm hơn 90% là phụ nữ tuổi mãn kinh) đều vừa trải qua một sang chấn tinh thần hoặc thể xác cực độ trong vòng vài giờ trước đó: nhận tin người thân đột ngột qua đời, đổ vỡ hôn nhân, mất trắng tài sản, phá sản kinh tế, hoặc trải qua một cuộc phẫu thuật lớn.

Cơ chế sinh lý bệnh học đã được làm sáng tỏ:
1. **Phản xạ chiến hay biến (Fight or Flight) vượt ngưỡng:** Dưới cú sốc tâm lý khủng khiếp, vùng dưới đồi và hạch hạnh nhân của não bộ phát tín hiệu khẩn cấp, kích thích tuyến thượng thận giải phóng ồ ạt các hormone căng thẳng gồm **Adrenaline (Epinephrine) và Noradrenaline** vào máu. Nồng độ Catecholamine trong máu bệnh nhân có thể vọt lên cao gấp **7 đến 30 lần** so với người bình thường.
2. **Hiện tượng ngộ độc cơ tim và co thắt vi mạch:** 
   * Trái tim bình thường chứa rất nhiều thụ thể beta-adrenergic để đón nhận tín hiệu giao cảm. Vùng mỏm tim là nơi có mật độ thụ thể này dày đặc nhất.
   * Cơn bão nồng độ Adrenaline quá đậm đặc làm tê liệt và gây độc trực tiếp lên các tế bào cơ tim vùng mỏm, làm quá tải canxi nội bào khiến các sợi tơ cơ không thể co rút được nữa.
   * Đồng thời, hormone này gây co thắt dữ dội toàn bộ mạng lưới vi mao mạch siêu nhỏ nuôi tim, dẫn đến tình trạng thiếu máu cơ tim lan tỏa cấp tính dù mạch vành lớn vẫn thông. Hậu quả là người bệnh rơi vào cơn suy tim cấp tính, tụt huyết áp và phù phổi cấp.

---

## PHẦN 3: ĐIỀU TRỊ VÀ PHƯƠNG PHÁP CÂN BẰNG TÂM THỂ

Điều kỳ diệu của bệnh cơ tim Takotsubo là: **Khác với nhồi máu cơ tim để lại sẹo xơ hóa vĩnh viễn, tổn thương của Takotsubo phần lớn có thể hồi phục hoàn toàn**. Sau 4 đến 8 tuần điều trị nội khoa hỗ trợ (thuốc chẹn beta giao cảm, ức chế men chuyển) và ổn định tâm lý, buồng thất trái sẽ dần co nhỏ lại hình dạng ban đầu và chức năng bơm máu phục hồi như bình thường.

### Bài tập thở 4-7-8 hóa giải căng thẳng:
* Thở hết hơi ra bằng miệng.
* Hít vào nhẹ nhàng bằng mũi trong **4 giây**.
* Giữ hơi thở lại trong lồng ngực đúng **7 giây**.
* Thở ra từ từ bằng miệng qua kẽ môi phát ra tiếng gió trong **8 giây**.
* Lặp lại chu kỳ 4 lần. Kỹ thuật thở này kéo giãn lồng ngực và kích thích mạnh mẽ nhánh thần kinh đối giao cảm (hệ phó giao cảm), ra lệnh cho tim lập tức giảm nhịp đập và hạ áp lực dòng máu chỉ sau vài phút.
            """
        },
        {
            "id": 12,
            "category": "HỒI SỨC CẤP CỨU NGOẠI VIỆN",
            "read_time": "15 phút đọc",
            "date": "28/08/2026",
            "author": "BS. Đỗ Trọng Nghĩa (Khoa Hồi sức Cấp cứu)",
            "title": "Tai Nạn Điện Giật: Cơ Chế Bỏng Sâu, Rung Thất Và 180 Giây Vàng Ép Tim",
            "summary": "Dòng điện 220V xoay chiều trong ổ điện gia đình nguy hiểm hơn nhiều người tưởng: chúng gây co cứng cơ khiến nạn nhân dính chặt vào nguồn điện và kích hoạt cơn rung thất tử vong tức thì. Nắm vững nguyên tắc an toàn cho người cứu hộ và kỹ thuật ép tim liên tục không gián đoạn.",
            "image": "https://images.unsplash.com/photo-1509228468518-180dd4864904?w=1000&auto=format&fit=crop&q=80",
            "content": r"""
## PHẦN 1: TẠI SAO DÒNG ĐIỆN XOAY CHIỀU 220V LẠI CỰC KỲ NGUY HIỂM?

Trong mạng lưới điện dân dụng tại Việt Nam, dòng điện được sử dụng là **dòng điện xoay chiều (Alternating Current - AC)** có điện áp 220V và tần số 50Hz (50 chu kỳ đảo chiều mỗi giây). Dưới góc độ điện sinh lý học, dòng điện này đặc biệt nguy hiểm đối với cơ thể người vì hai cơ chế:

1. **Tần số 50 Hz trùng với tần số kích thích cơ vận động:** Dòng điện xoay chiều kích thích liên tục các tế bào thần kinh vận động của cơ vân, gây ra hiện tượng **co cứng cơ liên tục (Tetanic Contraction)**. Nhóm cơ gấp của bàn tay và cẳng tay luôn khỏe hơn nhóm cơ duỗi. Do đó, khi vô tình chạm bàn tay vào dây điện hở, dòng điện 50Hz sẽ ép các ngón tay gập chặt lại, khiến nạn nhân nắm chặt lấy dây điện hoặc thiết bị rò rỉ mà **không tài nào tự buông tay ra được**, làm kéo dài thời gian dòng điện chạy qua cơ thể.
2. **Hiện tượng Rung thất (Ventricular Fibrillation):** Trái tim duy trì nhịp đập nhịp nhàng nhờ hệ thống dẫn truyền điện học nội tại (nút xoang nhĩ, nút nhĩ thất, bó His). Khi dòng điện đi ngang qua tim (từ tay này sang tay kia, hoặc từ tay xuống chân) đúng vào thời điểm cơ tim đang tái cực (sóng T trên điện tâm đồ), dòng điện sẽ phá vỡ hoàn toàn sự đồng bộ điện thế. Tim mất khả năng co bóp tống máu, biến thành một khối cơ rung giật hỗn loạn vô hiệu ở tần số 300 - 500 lần/phút. Huyết áp tụt về 0, dòng máu lên não ngừng phụt ngay lập tức, nạn nhân ngã gục bất tỉnh sau 5 giây.
3. **Bỏng sâu từ bên trong (Joule Heating):** Định luật Joule chỉ ra nhiệt lượng sinh ra tỷ lệ với điện trở của mô: $Q = I^2 \cdot R \cdot t$. Cơ thể người có điện trở khác nhau giữa các lớp mô: da khô có điện trở cao nhất, sau đó đến xương, mỡ, cơ và mạch máu. Dòng điện đi vào cơ thể sẽ biến điện năng thành nhiệt năng nung nóng từ bên trong, gây hoại tử sâu các bó cơ và đông vón mạch máu dọc theo đường đi, dù vết bỏng ngoài da nhìn chỉ như một chấm cháy đen nhỏ.

---

## PHẦN 2: NGUYÊN TẮC AN TOÀN CHO NGƯỜI CỨU HỘ

Không ít trường hợp thương tâm xảy ra khi người thân hốt hoảng lao vào dùng tay không kéo nạn nhân bị điện giật ra, kết quả là cả hai người cùng bị giật và tử vong chung.

* **Nguyên tắc số 1:** Tuyệt đối **KHÔNG chạm trực tiếp vào người nạn nhân** bằng tay không khi chưa chắc chắn nguồn điện đã được ngắt hoàn toàn.
* **Hành động khẩn cấp:**
  * Ngay lập tức lao đến dập cầu dao tổng, kéo aptomat hoặc giật phích cắm thiết bị ra khỏi ổ điện.
  * Nếu không tiếp cận được cầu dao: Người cứu hộ phải tự bảo vệ mình bằng cách đứng trên các vật liệu khô cách điện (tấm ván gỗ khô, tập sách báo dày, tấm đệm cao su khô, đi ủng cao su). 
  * Dùng một cây gậy gỗ khô, cán chổi tre khô, hoặc ống nhựa PVC khô dài để gạt dây điện văng ra khỏi người nạn nhân hoặc móc vào quần áo khô của nạn nhân kéo ra xa vùng nhiễm điện.

---

## PHẦN 3: KỸ THUẬT CPR CẤP CỨU TRONG 180 GIÂY ĐẦU

Ngay sau khi đã tách được nạn nhân ra khỏi nguồn điện an toàn, gọi to kêu gọi người xung quanh hỗ trợ gọi cấp cứu **115**, đồng thời người cứu hộ lập tức kiểm tra: Lay gọi bờ vai không phản ứng, ghé sát tai vào mũi miệng không thấy hơi thở và lồng ngực không phập phồng $\rightarrow$ **Nạn nhân đã ngừng tuần hoàn, ngừng hô hấp**.

Phải tiến hành ép tim ngoài lồng ngực (**Hands-Only CPR**) ngay lập tức, mỗi giây chần chừ não bộ sẽ chết dần:
* **Tư thế nạn nhân:** Đặt nằm ngửa trên mặt sàn phẳng, cứng (sàn nhà, mặt đất khô), nới rộng cổ áo.
* **Vị trí đặt tay:** Đặt gót bàn tay thuận vào **chính giữa nửa dưới xương ức** (nằm giữa hai núm vú). Đặt gót bàn tay kia đan các ngón tay lên trên bàn tay trước. Giữ hai khuỷu tay thẳng đứng vuông góc 90° với lồng ngực nạn nhân.
* **Động lực ép:** Dùng toàn bộ trọng lượng nửa thân trên ấn mạnh xuống lồng ngực lún sâu ít nhất **5 cm - 6 cm** ở người lớn.
* **Tần số ép tim:** Ép nhanh và dứt khoát với tốc độ **100 - 120 lần/phút** (nhịp điệu tương đương bài hát *Stayin' Alive* của Bee Gees).
* **Quy tắc giải phóng ngực:** Sau mỗi lần ép xuống, phải thả lỏng hoàn toàn để lồng ngực nở lại tối đa giúp máu từ tĩnh mạch hút trở về tim, nhưng không được nhấc rời tay khỏi xương ức.
* **Kiên trì liên tục:** Ép tim liên tục không ngừng nghỉ cho đến khi có nhân viên y tế mang máy sốc điện khử rung đến tiếp quản hoặc khi nạn nhân tự thở, cử động lại được.

---

## PHẦN 4: LƯU Ý Y KHOA BẮT BUỘC SAU TAI NẠN

Kể cả khi nạn nhân bị điện giật chỉ trong tích tắc, sau đó đã tỉnh táo lại, nói chuyện bình thường và khẳng định mình "không sao cả", **vẫn bắt buộc phải đưa nạn nhân đến bệnh viện cấp cứu để đo Điện tâm đồ (ECG) và làm xét nghiệm men tim, chức năng thận**. 

Dòng điện chạy qua cơ thể có thể để lại những tổn thương vi thể âm thầm ở hệ dẫn truyền cơ tim, gây ra các cơn **rối loạn nhịp thất muộn (nhanh thất, rung thất)** xuất hiện sau đó 6 đến 12 tiếng gây đột tử khi đang ngủ. Đồng thời, chất độc myoglobin giải phóng từ các khối cơ bị dòng điện nướng chín sẽ trôi về làm tắc ống thận, dẫn đến suy thận cấp vô niệu nếu không được truyền dịch kiềm hóa nước tiểu kịp thời.
            """
        }
    ]

    if "current_reading_article_id" not in st.session_state:
        st.session_state.current_reading_article_id = None

    # ==========================================================================
    # VIEW 1: GIAO DIỆN ĐỌC TOÀN VĂN BÀI BÁO (BUNG TOÀN TRANG DÀI NHƯ BÁO ĐIỆN TỬ)
    # ==========================================================================
    if st.session_state.current_reading_article_id is not None:
        target_article = next((a for a in ARTICLES_DATA if a["id"] == st.session_state.current_reading_article_id), None)
        if target_article:
            if st.button("⬅️ Quay lại danh sách tất cả bài báo", key="btn_back_top_reader", type="secondary"):
                st.session_state.current_reading_article_id = None
                st.rerun()

            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

            # Header bài viết
            st.markdown(f"""
                <div style="background: white; border-radius: 16px; padding: 26px 32px; border: 1px solid #E2E8F0; box-shadow: 0 4px 20px rgba(0,0,0,0.03); margin-bottom: 20px;">
                    <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap;">
                        <span style="background: #E0F2FE; color: #0284C7; font-weight: 800; font-size: 11.5px; padding: 4px 12px; border-radius: 20px; text-transform: uppercase;">
                            {target_article['category']}
                        </span>
                        <span style="font-size: 12.5px; color: #64748B;">🕒 {target_article['read_time']}</span>
                        <span style="font-size: 12.5px; color: #CBD5E1;">•</span>
                        <span style="font-size: 12.5px; color: #64748B;">📅 {target_article['date']}</span>
                        <span style="font-size: 12.5px; color: #CBD5E1;">•</span>
                        <span style="font-size: 12.5px; color: #0C3861; font-weight: 700;">✍️ {target_article['author']}</span>
                    </div>
                    <h1 style="font-size: 28px; font-weight: 900; color: #0F172A; line-height: 1.35; margin-bottom: 16px;">
                        {target_article['title']}
                    </h1>
                    <div style="font-size: 15px; color: #334155; font-style: italic; line-height: 1.7; border-left: 4px solid #0284C7; background: #F8FAFC; padding: 16px 20px; border-radius: 0 10px 10px 0;">
                        {target_article['summary']}
                    </div>
                </div>
            """, unsafe_allow_html=True)

            # Ảnh chính của bài báo
            # Ảnh banner thu nhỏ gọn gàng, không choán hết màn hình
            st.markdown(f"""
                <div style="width: 100%; height: 280px; max-height: 280px; overflow: hidden; border-radius: 14px; margin-bottom: 20px; box-shadow: 0 4px 14px rgba(0,0,0,0.06);">
                    <img src="{target_article['image']}" 
                         style="width: 100%; height: 100%; object-fit: cover; object-position: center; display: block;" />
                </div>
            """, unsafe_allow_html=True)

            # Nội dung chi tiết bài báo (Render Markdown trực tiếp không bọc div gò bó chiều cao)
            st.markdown("""
                <style>
                    .article-body-container h2 {
                        color: #0C3861 !important;
                        font-size: 20px !important;
                        font-weight: 800 !important;
                        margin-top: 32px !important;
                        margin-bottom: 14px !important;
                        border-bottom: 2px solid #E2E8F0 !important;
                        padding-bottom: 8px !important;
                    }
                    .article-body-container h3 {
                        color: #0284C7 !important;
                        font-size: 16.5px !important;
                        font-weight: 700 !important;
                        margin-top: 20px !important;
                        margin-bottom: 10px !important;
                    }
                    .article-body-container p, .article-body-container li {
                        font-size: 15px !important;
                        line-height: 1.85 !important;
                        color: #1E293B !important;
                    }
                </style>
                <div class="article-body-container" style="background: white; border-radius: 16px; padding: 32px 36px; border: 1px solid #E2E8F0; margin-top: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.02);">
            """, unsafe_allow_html=True)

            # Đẩy toàn bộ văn bản chuyên khảo dài ra màn hình
            st.markdown(target_article["content"])

            st.markdown("""
                    <hr style="margin: 36px 0 18px 0; border: 0; border-top: 1px solid #E2E8F0;">
                    <div style="font-size: 12px; color: #64748B; line-height: 1.6;">
                        <b>Tuyên bố trách nhiệm y khoa:</b> Bài viết mang tính chất phổ biến tri thức khoa học đời sống và hướng dẫn dự phòng sức khỏe cộng đồng theo khuyến cáo của Hội Tim mạch & Đột quỵ. Người bệnh khi có dấu hiệu nghi ngờ cần lập tức đến cơ sở y tế có đơn vị cấp cứu đột quỵ để được chẩn đoán hình ảnh và xử trí y lệnh chính thức từ bác sĩ chuyên khoa.
                    </div>
                </div>
            """, unsafe_allow_html=True)

            st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)
            if st.button("⬅️ Quay lại danh sách tất cả bài báo", key="btn_back_bottom_reader", type="secondary"):
                st.session_state.current_reading_article_id = None
                st.rerun()

    # ==========================================================================
    # VIEW 2: TRANG CHỦ DANH SÁCH 12 BÀI BÁO (BỐ CỤC 3 CỘT GỌN GÀNG, ẢNH NHỎ VỪA VẶN)
    # ==========================================================================
    else:
        st.markdown("""
            <div style="background: linear-gradient(135deg, #0C3861 0%, #0369A1 100%); color: white; padding: 18px 22px; border-radius: 14px; margin-bottom: 20px; box-shadow: 0 4px 14px rgba(3, 105, 161, 0.15);">
                <div style="font-size: 20px; font-weight: 900; letter-spacing: 0.3px;">📰 TẠP CHÍ Y KHOA & BẢN TIN SỨC KHỎE CỘNG ĐỒNG </div>
                <div style="font-size: 12.5px; color: #BAE6FD; margin-top: 4px;">Cập nhật các hướng dẫn lâm sàng, phát hiện dược học và cẩm nang dự phòng bệnh tật chuẩn Bộ Y Tế</div>
            </div>
        """, unsafe_allow_html=True)

        # Hiển thị 3 cột giúp khung bài viết nhỏ gọn, vừa mắt
        c_grid1, c_grid2, c_grid3 = st.columns(3, gap="medium")

        for idx, art in enumerate(ARTICLES_DATA):
            col_target = c_grid1 if idx % 3 == 0 else (c_grid2 if idx % 3 == 1 else c_grid3)
            with col_target:
                with st.container(border=True):
                    # Ảnh thumbnail chuẩn chiều cao 135px, không bị choán diện tích
                    st.markdown(f"""
                        <div style="width: 100%; height: 135px; border-radius: 8px; overflow: hidden; margin-bottom: 8px;">
                            <img src="{art['image']}" style="width: 100%; height: 100%; object-fit: cover;" />
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <span style="background: #E0F2FE; color: #0284C7; font-size: 9.5px; font-weight: 800; padding: 2px 8px; border-radius: 10px; text-transform: uppercase;">
                                {art['category']}
                            </span>
                            <span style="font-size: 10.5px; color: #94A3B8;">🕒 {art['read_time']}</span>
                        </div>
                        <div style="font-size: 14px; font-weight: 800; color: #0F172A; line-height: 1.35; min-height: 38px; margin-bottom: 6px;">
                            {art['title']}
                        </div>
                        <div style="font-size: 11.5px; color: #64748B; line-height: 1.45; min-height: 48px; margin-bottom: 10px;">
                            {art['summary'][:95]}...
                        </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button("📖 Đọc bài báo", key=f"btn_read_news_item_{art['id']}", width="stretch", type="primary"):
                        st.session_state.current_reading_article_id = art["id"]
                        st.rerun()
                        
# ==============================================================================
# PHÂN HỆ 1: Khám Bệnh Online (Trợ Lý Y Tế)
# ==============================================================================
elif st.session_state.main_navigation == "🧑‍⚕️Khám Bệnh Online (Trợ Lý Y Tế)":
    st.markdown("<div style='font-size:20px; font-weight:800; color:#0C3861; margin-bottom:4px;'>🩺 TRỢ LÝ Y TẾ THÔNG MINH - HỖ TRỢ CHĂM SÓC SỨC KHỎE</div>", unsafe_allow_html=True)
    st.markdown("<div style='font-size:12px; color:#64748B; margin-bottom:14px;'>👨‍⚕️ Phụ trách chuyên môn: DR. Nguyễn Tiến Toàn | ✉️ Email: toanbvtimhn@gmail.com</div>", unsafe_allow_html=True)

    # CSS giữ nguyên hiển thị chữ trên tab, không bị co ngắn hay mất chữ
    st.markdown("""
        <style>
            button[data-baseweb="tab"] {
                white-space: nowrap !important;
                flex-shrink: 0 !important;
                font-size: 13.5px !important;
                padding-left: 14px !important;
                padding-right: 14px !important;
            }
        </style>
    """, unsafe_allow_html=True)

    if "clinic_active_tab" not in st.session_state:
        st.session_state.clinic_active_tab = "triage"

    # Tự động ưu tiên đưa tab Đơn thuốc lên đầu nếu người dùng bấm nút Đơn thuốc ở ngoài
    if st.session_state.clinic_active_tab == "prescriptions":
        tab_overview, tab_ai, tab_history = st.tabs([
            "📊 Tổng Quan Phác Đồ Điều Trị & Đơn Thuốc",
            "🩺 Khám Bệnh Online Trả Lời & Phân Tích Bệnh Lý (Lâm Sàng + Cận Lâm Sàng)",
            "📋 Lịch Sử Khám Bệnh"
        ])
    else:
        tab_ai, tab_overview, tab_history = st.tabs([
            "🩺 Khám Bệnh Online Trả Lời & Phân Tích Bệnh Lý (Lâm Sàng + Cận Lâm Sàng)",
            "📊 Tổng Quan Phác Đồ Điều Trị & Đơn Thuốc",
            "📋 Lịch Sử Khám Bệnh"
        ])

    # ==========================================================================
    # TAB 1: KHÁM BỆNH ONLINE & TRIAGE AI (ĐÃ ĐẨY LÊN ĐẦU TIÊN)
    # ==========================================================================
    with tab_ai:
        st.markdown("""
            <div style="background: linear-gradient(135deg, #0C3861 0%, #0284C7 100%); padding: 16px 20px; border-radius: 14px; margin-bottom: 16px; color: #FFFFFF; box-shadow: 0 4px 14px rgba(2,132,199,0.15);">
                <div style="font-size: 17px; font-weight: 800; letter-spacing: 0.3px;">🩺 HỆ THỐNG PHÂN TẦNG LÂM SÀNG & TRA SOÁT BỆNH AI (TRIAGE AI)</div>
                <div style="font-size: 12px; color: #E0F2FE; margin-top: 4px;">Ứng dụng thuật toán chẩn đoán phân biệt (Differential Diagnosis) & Phác đồ chuẩn Bộ Y Tế</div>
            </div>
        """, unsafe_allow_html=True)

        def save_newly_learned_knowledge(disease_keyword: str, icd_code: str, official_guideline: str):
            """Tự động lưu phác đồ mới học vào PostgreSQL Render."""
            if not disease_keyword or not official_guideline:
                return
            try:
                with db_cursor() as cur:
                    cur.execute("""
                        INSERT INTO medical_knowledge_base (disease_keyword, icd_code, official_guideline, source_url)
                        VALUES (%s, %s, %s, 'kcb.vn - Bo Y Te Viet Nam')
                        ON CONFLICT (disease_keyword) DO UPDATE 
                        SET official_guideline = EXCLUDED.official_guideline,
                            icd_code = EXCLUDED.icd_code,
                            learned_at = CURRENT_TIMESTAMP
                    """, (disease_keyword.strip(), icd_code.strip(), official_guideline.strip()))
            except Exception as e:
                print(f"Lỗi lưu tri thức học được: {e}")

        def find_existing_guideline(query_text: str):
            """Tìm phác đồ đã được nạp sẵn trong CSDL PostgreSQL."""
            try:
                with db_cursor() as cur:
                    cur.execute("SELECT disease_keyword, icd_code, official_guideline FROM medical_knowledge_base")
                    rows = cur.fetchall()
                    for kw, icd, guide in rows:
                        if kw and kw.lower() in query_text.lower():
                            return {"keyword": kw, "icd": icd, "guideline": guide, "source": "CSDL Nội bộ (populate.kcb)"}
            except Exception as e:
                print(f"Lỗi đọc tri thức: {e}")
            return None

        def auto_learn_and_save_guideline(disease_keyword: str, icd_code: str, official_guideline: str):
            """Tự động ghi nhớ phác đồ mới chuẩn Bộ Y Tế."""
            if not disease_keyword or not official_guideline:
                return
            try:
                with db_cursor() as cur:
                    cur.execute("""
                        INSERT INTO medical_knowledge_base (disease_keyword, icd_code, official_guideline, source_url)
                        VALUES (%s, %s, %s, 'kcb.vn - Bo Y Te Viet Nam (AI tu hoc)')
                        ON CONFLICT (disease_keyword) DO UPDATE 
                        SET official_guideline = EXCLUDED.official_guideline,
                            icd_code = EXCLUDED.icd_code,
                            learned_at = CURRENT_TIMESTAMP
                    """, (disease_keyword.strip(), icd_code.strip(), official_guideline.strip()))
            except Exception as e:
                print(f"Lỗi lưu tri thức mới: {e}")

        def run_multimodal_clinical_triage(symptoms: str, age: int, gender: str, images_list: list = None):
            import time
            import json
            import traceback

            try:
                from google import genai
                from google.genai import types
            except ImportError:
                st.error("❌ Thiếu thư viện google-genai. Vui lòng chạy: `pip install google-genai`.")
                return None

            load_dotenv(override=True)
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if not api_key and "GEMINI_API_KEY" in st.secrets:
                api_key = str(st.secrets["GEMINI_API_KEY"]).strip()

            if not api_key:
                try:
                    with db_cursor() as cur_k:
                        cur_k.execute("SELECT key_value FROM system_settings WHERE key_name = 'GEMINI_API_KEY'")
                        row_k = cur_k.fetchone()
                        if row_k and row_k[0]:
                            api_key = row_k[0].strip()
                except Exception:
                    pass

            if not api_key:
                st.error("❌ Không tìm thấy API Key hợp lệ.")
                return None

            model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-3.6-flash").strip() or "gemini-3.6-flash"

            matched_kb = find_existing_guideline(symptoms)
            if matched_kb:
                kb_instruction = f"""
ĐÃ CÓ PHÁC ĐỒ TRONG CƠ SỞ DỮ LIỆU NỘI BỘ:
- Bệnh lý: {matched_kb['keyword']} (Mã ICD: {matched_kb['icd']})
- Hướng dẫn điều trị: {matched_kb['guideline']}
YÊU CẦU: Hãy ưu tiên áp dụng đúng hướng dẫn trên để đưa ra phác đồ.
"""
            else:
                kb_instruction = """
CHƯA CÓ PHÁC ĐỒ TRONG CƠ SỞ DỮ LIỆU NỘI BỘ:
YÊU CẦU: Dựa hoàn toàn vào các Hướng dẫn Chẩn đoán và Điều trị chuẩn chính thức của Cục Quản lý Khám chữa bệnh - Bộ Y Tế Việt Nam (kcb.vn) để xây dựng phác đồ thuốc chi tiết và tóm tắt lại để hệ thống nạp vào bộ nhớ tự học.
"""

            sys_instruction = f"""Bạn là Bác sĩ Trưởng Khoa & Chuyên gia Phân tầng Lâm sàng theo chuẩn Bộ Y Tế Việt Nam (kcb.vn).
{kb_instruction}

NHIỆM VỤ:
1. Phân tích toàn bộ thông tin bệnh cảnh và hình ảnh cận lâm sàng/xét nghiệm.
2. Liệt kê TẤT CẢ các nguyên nhân có thể xảy ra (Chẩn đoán phân biệt) kèm mức độ nghi ngờ và căn cứ y khoa.
3. Đưa ra 3-5 câu hỏi sàng lọc triệu chứng lâm sàng quan trọng nhất nhằm giúp tìm ra đúng nguyên nhân gốc rễ.
4. Đưa ra phác đồ điều trị cụ thể:
   - Tên hoạt chất, liều lượng, cách dùng, thời gian dùng theo chuẩn Bộ Y Tế.
   - Các dấu hiệu cảnh báo đỏ (Red Flags) phải vào viện khẩn cấp.
   - Hướng dẫn chăm sóc và chế độ sinh hoạt an toàn tại nhà.
5. Cung cấp một đoạn tóm tắt phác đồ ("learned_guideline_summary") để hệ thống tự động lưu vào bộ nhớ.

YÊU CẦU ĐỊNH DẠNG:
Trả về DUY NHẤT một chuỗi JSON thuần túy (không dùng markdown ```json, không thêm chữ ngoài):
{{
  "category": "Tên chuyên khoa phù hợp",
  "disease_keyword": "Tên bệnh lý ngắn gọn",
  "differential_diagnosis": [
    {{"name": "Tên bệnh lý chính", "icd": "Mã ICD-10", "level": "Ưu tiên cao (85%)", "badge": "background:#FEE2E2; color:#DC2626;", "reason": "Căn cứ theo hướng dẫn chẩn đoán của Bộ Y Tế"}},
    {{"name": "Bệnh phân biệt 1", "icd": "Mã ICD-10", "level": "Trung bình (50%)", "badge": "background:#FEF3C7; color:#D97706;", "reason": "Triệu chứng có nét tương đồng"}},
    {{"name": "Bệnh phân biệt 2", "icd": "Mã ICD-10", "level": "Thấp (20%)", "badge": "background:#F1F5F9; color:#64748B;", "reason": "Cần loại trừ"}}
  ],
  "triage_options": [
    "Câu hỏi sàng lọc số 1 để xác định đúng nguyên nhân?",
    "Câu hỏi sàng lọc số 2 để khu trú bệnh?",
    "Câu hỏi sàng lọc số 3 để loại trừ bệnh nguy hiểm?"
  ],
  "red_flags": [
    "Dấu hiệu cảnh báo nguy hiểm cần đi cấp cứu ngay lập tức"
  ],
  "home_care": [
    "Tên thuốc, liều dùng, số ngày điều trị theo chuẩn Bộ Y Tế (kcb.vn)",
    "Chế độ dinh dưỡng, theo dõi triệu chứng",
    "Lịch tái khám hoặc xét nghiệm bổ sung"
  ],
  "is_new_guideline": {str(not bool(matched_kb)).lower()},
  "learned_guideline_summary": "Tóm tắt phác đồ điều trị chuẩn kcb.vn gồm chỉ định thuốc chính, liều lượng và lưu ý."
}}"""

            prompt_content = f"""BỆNH ÁN TIẾP NHẬN:
- Giới tính: {gender}
- Tuổi: {age}
- Triệu chứng khởi phát: "{symptoms if symptoms else 'Đánh giá qua hình ảnh xét nghiệm đính kèm'}"
Hãy phân tích nguyên nhân, tạo câu hỏi sàng lọc và xây dựng phác đồ chuẩn xác."""

            contents_payload = [prompt_content]

            if images_list:
                for img_item in images_list:
                    try:
                        raw_b = img_item["bytes"] if isinstance(img_item, dict) and "bytes" in img_item else img_item.getvalue()
                        image = Image.open(io.BytesIO(raw_b))
                        if image.mode in ("RGBA", "P"):
                            image = image.convert("RGB")
                        image.thumbnail((640, 640), Image.Resampling.BILINEAR)
                        out_io = io.BytesIO()
                        image.save(out_io, format="JPEG", quality=60, optimize=True)
                        contents_payload.append(types.Part.from_bytes(data=out_io.getvalue(), mime_type="image/jpeg"))
                    except Exception as e_img:
                        st.warning(f"⚠️ Bỏ qua ảnh lỗi: {e_img}")

            response = None
            max_retries = 3

            try:
                client = genai.Client(api_key=api_key)
                for attempt in range(1, max_retries + 1):
                    try:
                        with st.spinner(f"⚡ Doctor đang kiểm tra dữ liệu hồ sơ bệnh án của bạn (Lần {attempt})..."):
                            response = client.models.generate_content(
                                model=model_name,
                                contents=contents_payload,
                                config=types.GenerateContentConfig(
                                    system_instruction=sys_instruction,
                                    temperature=0.0,
                                    max_output_tokens=4096,
                                    response_mime_type="application/json"
                                )
                            )
                        if response and response.text:
                            break
                    except Exception as e_call:
                        err_str = str(e_call)
                        if ("503" in err_str or "UNAVAILABLE" in err_str) and attempt < max_retries:
                            time.sleep(2)
                            continue
                        raise e_call

                if not response or not response.text:
                    st.error("❌ Gemini không phản hồi dữ liệu.")
                    return None

                raw_text = response.text.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                elif raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

                match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
                clean_text = match.group(1) if match else raw_text

                # 1. Cắt bỏ các khối markdown nếu có
                raw_text = response.text.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                elif raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

                # 2. Loại bỏ khẩu hiệu thừa ở cuối bằng cách chỉ lấy từ ký tự { đầu tiên
                start_idx = raw_text.find("{")
                if start_idx != -1:
                    raw_text = raw_text[start_idx:]

                # 3. Thuật toán tự động đóng ngoặc nếu chuỗi JSON bị ngắt giữa chừng
                def clean_and_repair_json(text_str):
                    try:
                        return json.loads(text_str)
                    except json.JSONDecodeError:
                        # Thử tìm vị trí dấu đóng ngoặc } cuối cùng hợp lệ
                        last_brace = text_str.rfind("}")
                        if last_brace != -1:
                            try:
                                return json.loads(text_str[:last_brace + 1])
                            except json.JSONDecodeError:
                                pass

                        # Nếu bị cắt ngang khi đang mở ngoặc nhọn hoặc mảng vuông
                        repaired = text_str.rstrip()
                        # Xóa bỏ phần văn bản rác thừa sau dấu phẩy hoặc chuỗi dở dang
                        repaired = re.sub(r',\s*$', '', repaired)
                        open_braces = repaired.count("{") - repaired.count("}")
                        open_brackets = repaired.count("[") - repaired.count("]")

                        # Nếu đang mở chuỗi string dở dang (số dấu " là số lẻ)
                        if repaired.count('"') % 2 != 0:
                            repaired += '"'

                        repaired += ("]" * max(0, open_brackets))
                        repaired += ("}" * max(0, open_braces))

                        return json.loads(repaired)

                parsed_json = clean_and_repair_json(raw_text)

                try:
                    kw = parsed_json.get("disease_keyword") or parsed_json["differential_diagnosis"][0]["name"]
                    icd = parsed_json["differential_diagnosis"][0].get("icd", "N/A")
                    summary = parsed_json.get("learned_guideline_summary") or "\n".join(parsed_json.get("home_care", []))

                    if not matched_kb or parsed_json.get("is_new_guideline"):
                        auto_learn_and_save_guideline(kw, icd, summary)
                        st.toast(f"🧠 AI đã lưu phác đồ mới: {kw} ({icd}) vào CSDL!")
                    else:
                        st.toast(f"📖 Đã áp dụng phác đồ có sẵn: {matched_kb['keyword']}")
                except Exception as e_save:
                    print(f"Lỗi nạp tri thức: {e_save}")

                return parsed_json

            except json.JSONDecodeError:
                st.error("❌ Gemini trả về định dạng văn bản không đúng cú pháp JSON.")
                with st.expander("🔍 Bấm vào đây để xem dữ liệu thô nhận được từ Gemini"):
                    st.code(response.text if response else "Không có phản hồi")
                return None
            except Exception as ex_sdk:
                st.error(f"❌ Lỗi xử lý Gemini AI: {ex_sdk}")
                with st.expander("🔍 Chi tiết lỗi kỹ thuật"):
                    st.code(traceback.format_exc())
                return None

        if st.session_state.final_conclusion:
            concl = st.session_state.final_conclusion
            top_d = concl.get("top_disease", {})
            triage = concl.get("triage", {})

            st.success(f"🎉 **Đã hoàn tất phiên khám bệnh #{concl.get('session_id', 'Mới')} — Nếu chưa yên tâm hãy liên hệ lại với chúng tôi**")

            st.markdown(f"""
                <div style="background: #FFFFFF; border: 2px solid #0284C7; border-radius: 14px; padding: 18px; margin-bottom: 16px; box-shadow: 0 4px 16px rgba(2,132,199,0.1);">
                    <div style="font-size: 13px; font-weight: 700; color: #0284C7; text-transform: uppercase;">KẾT QUẢ PHÂN TÍCH LÂM SÀNG ƯU TIÊN</div>
                    <div style="font-size: 20px; font-weight: 900; color: #0C3861; margin: 4px 0 8px 0;">
                        {top_d.get('name', 'Chưa xác định')} <span style="font-size: 14px; color: #64748B; font-weight: normal;">(Mã ICD-10: <b>{top_d.get('icd', 'N/A')}</b>)</span>
                    </div>
                    <div style="font-size: 13px; color: #334155; line-height: 1.5;">
                        <b>Độ tin cậy:</b> <span style="color: #DC2626; font-weight: 800;">{top_d.get('level', 'Cao')}</span> — {top_d.get('reason', '')}
                    </div>
                </div>
            """, unsafe_allow_html=True)

            col_res_l, col_res_r = st.columns([1, 1], gap="medium")

            with col_res_l:
                home_care_html = "".join([f"<li style='margin-bottom: 6px;'>{hc}</li>" for hc in triage.get("home_care", [])])
                st.markdown(f"""
                    <div style="background: #F0FDF4; border: 1.5px solid #86EFAC; border-radius: 14px; padding: 16px; height: 100%; box-shadow: 0 4px 12px rgba(22,163,74,0.06);">
                        <div style="display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 800; color: #166534; margin-bottom: 10px;">
                            <span>🏡</span> HƯỚNG DẪN CHĂM SÓC TẠI NHÀ (HOME CARE)
                        </div>
                        <ul style="font-size: 12.5px; color: #14532D; line-height: 1.6; padding-left: 18px; margin: 0;">
                            {home_care_html}
                        </ul>
                    </div>
                """, unsafe_allow_html=True)

            with col_res_r:
                red_flags_html = "".join([f"<li style='margin-bottom: 6px;'><b>{rf}</b></li>" for rf in triage.get("red_flags", [])])
                st.markdown(f"""
                    <div style="background: #FEF2F2; border: 1.5px solid #FCA5A5; border-radius: 14px; padding: 16px; height: 100%; box-shadow: 0 4px 12px rgba(220,38,38,0.06);">
                        <div style="display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 800; color: #991B1B; margin-bottom: 10px;">
                            <span>🚨</span> CẢNH BÁO ĐỎ (ĐI VIỆN KHẨN CẤP)
                        </div>
                        <ul style="font-size: 12.5px; color: #7F1D1D; line-height: 1.6; padding-left: 18px; margin: 0;">
                            {red_flags_html}
                        </ul>
                    </div>
                """, unsafe_allow_html=True)

            st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
            # ==============================================================
            # KHỐI HỎI ĐÁP & YÊU CẦU BỔ SUNG VỀ BỆNH LÝ NÀY
            # ==============================================================
            st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
            
            with st.container(border=True):
                st.markdown("""
                    <div style="font-size: 14.5px; font-weight: 800; color: #0C3861; margin-bottom: 6px;">
                        💬 Bạn còn yêu cầu hoặc câu hỏi nào về bệnh lý này không?
                    </div>
                    <div style="font-size: 12px; color: #64748B; margin-bottom: 10px;">
                        Hỏi thêm bác sĩ về chế độ ăn uống kiêng cữ, tương tác thuốc, thời gian hồi phục hoặc làm rõ chỉ định...
                    </div>
                """, unsafe_allow_html=True)
                
                with st.form("form_additional_medical_inquiry"):
                    user_extra_question = st.text_area(
                        "Nhập thắc mắc hoặc yêu cầu bổ sung của bạn:",
                        placeholder="Ví dụ: Bệnh này có cần kiêng đồ tanh không? Uống thuốc này có gây buồn ngủ không? Bao lâu thì khỏi hẳn?...",
                        height=85,
                        label_visibility="collapsed"
                    )
                    btn_send_inquiry = st.form_submit_button("🚀 Gửi câu hỏi cho Bác sĩ ", type="secondary", width="stretch")
                
                if btn_send_inquiry and user_extra_question.strip():
                    try:
                        import time
                        from google import genai
                        from google.genai import types
                        
                        api_key = os.getenv("GEMINI_API_KEY", "").strip()
                        if not api_key:
                            with db_cursor() as cur_k:
                                cur_k.execute("SELECT key_value FROM system_settings WHERE key_name = 'GEMINI_API_KEY'")
                                row_k = cur_k.fetchone()
                                if row_k and row_k[0]:
                                    api_key = row_k[0].strip()
                        
                        client = genai.Client(api_key=api_key)
                        
                        # Danh sách model ưu tiên kèm fallback
                        candidate_models = [
                            os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
                            "gemini-2.5-flash",
                            "gemini-1.5-flash"
                        ]
                        
                        prompt_followup = f"""Bạn là Bác sĩ Trưởng khoa điều trị. Bệnh nhân đang được chẩn đoán:
- Bệnh chính: {top_d.get('name')} (Mã ICD-10: {top_d.get('icd')})
- Triệu chứng ban đầu: {concl.get('symptoms')}

Bệnh nhân hỏi thêm thắc mắc sau:
"{user_extra_question.strip()}"

YÊU CẦU: Trả lời ngắn gọn, chuẩn y khoa theo Bộ Y Tế, rõ ràng, ân cần, gạch đầu dòng các lưu ý thực tế để bệnh nhân dễ áp dụng ngay tại nhà."""

                        resp_followup = None
                        with st.spinner("👨‍⚕️ Bác sĩ đang phân tích và giải đáp thắc mắc của bạn..."):
                            for target_model in list(dict.fromkeys(candidate_models)):
                                for attempt in range(1, 3):
                                    try:
                                        resp_followup = client.models.generate_content(
                                            model=target_model,
                                            contents=prompt_followup,
                                            config=types.GenerateContentConfig(temperature=0.2)
                                        )
                                        if resp_followup and resp_followup.text:
                                            break
                                    except Exception as e_retry:
                                        err_str = str(e_retry)
                                        if ("503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str):
                                            time.sleep(1.5)
                                            continue
                                        raise e_retry
                                if resp_followup and resp_followup.text:
                                    break
                        
                        if resp_followup and resp_followup.text:
                            if "inquiry_chat_history" not in st.session_state:
                                st.session_state.inquiry_chat_history = []
                            st.session_state.inquiry_chat_history.append({
                                "q": user_extra_question.strip(),
                                "a": resp_followup.text.strip()
                            })
                            st.rerun()
                        else:
                            st.error("⚠️ Máy chủ AI đang bảo trì cục bộ. Bạn vui lòng bấm gửi lại sau vài giây.")
                    except Exception as e_extra:
                        st.error(f"Lỗi phản hồi từ AI: {e_extra}")

                # Hiển thị các câu hỏi & giải đáp đã gửi
                if st.session_state.get("inquiry_chat_history"):
                    st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
                    for idx_iq, iq in enumerate(st.session_state.inquiry_chat_history):
                        st.markdown(f"""
                            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 10px 14px; margin-bottom: 8px;">
                                <div style="font-size: 13px; font-weight: 700; color: #0369A1;">❓ Bạn hỏi: <i>"{iq['q']}"</i></div>
                                <div style="font-size: 13px; color: #1E293B; line-height: 1.5; margin-top: 6px; white-space: pre-wrap;">💡 <b>Bác sĩ giải đáp:</b>\n{iq['a']}</div>
                            </div>
                        """, unsafe_allow_html=True)

            st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True) 

            # ==============================================================
            # KHỐI 2 NÚT HÀNH ĐỘNG
            # ==============================================================
            c_fin1, c_fin2 = st.columns([1.2, 1.8])
            with c_fin1:
                if st.button("🔄 Thực hiện tra soát ca khám mới", type="primary", width="stretch"):
                    st.session_state.step1_data = None
                    st.session_state.final_conclusion = None
                    st.session_state.uploaded_medical_images = []
                    st.rerun()
            with c_fin2:
                if st.button("📅 Đặt lịch khám Bác sĩ Chuyên khoa ngay", width="stretch"):
                    st.session_state.main_navigation = "🩺 Khám Sức Khỏe & Đặc Quyền VIP"
                    st.rerun()

        elif st.session_state.step1_data:
            step1 = st.session_state.step1_data
            triage = step1.get("triage", {})

            category_display = triage.get("category", "Chuyên khoa tổng hợp")
            st.info(f"📋 **Bệnh cảnh & Dữ liệu:** *\"{step1.get('symptoms', '')}\"* | Bệnh nhân: **{step1.get('gender', 'Nam')}**, **{step1.get('age', 30)} tuổi** (Chuyên khoa: **{category_display}**)")

            st.markdown("#### 🎯 1. Phân tầng xác suất bệnh lý (Differential Diagnosis)")
            for dis in triage.get("differential_diagnosis", []):
                st.markdown(f"""
                    <div style="background: #FFFFFF; border: 1px solid #E2ECF5; border-radius: 12px; padding: 12px 14px; margin-bottom: 8px; box-shadow: 0 2px 8px rgba(15,60,100,0.03);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <b style="font-size: 14px; color: #0C3861;">🩺 {dis.get('name')} <span style="font-size: 12px; color: #64748B;">(ICD-10: <code>{dis.get('icd')}</code>)</span></b>
                            <span style="font-size: 11px; font-weight: 800; padding: 3px 10px; border-radius: 20px; {dis.get('badge', '')}">Mức độ: {dis.get('level')}</span>
                        </div>
                        <div style="font-size: 12.5px; color: #475569; line-height: 1.4;"><b>Lý do nghi ngờ:</b> {dis.get('reason')}</div>
                    </div>
                """, unsafe_allow_html=True)

            st.markdown("---")
            st.markdown("#### ❓ 2. Khai thác dữ liệu chuyên sâu (Triage Checklist)")
            st.caption("💡 Tích chọn các dấu hiệu thực tế của bạn/người bệnh để AI xác lập phác đồ chính xác:")

            with st.form("form_step2_triage"):
                selected_answers = []
                for idx, q_text in enumerate(triage.get("triage_options", [])):
                    chk = st.checkbox(f"**Dấu hiệu {idx+1}:** {q_text}", key=f"triage_chk_{idx}")
                    if chk:
                        selected_answers.append(f"✓ {q_text}")

                extra_notes = st.text_input("Ghi chú bổ sung khác (nếu có):", placeholder="VD: Đã làm thủ thuật cách đây 3 ngày...")

                c_s2_1, c_s2_2 = st.columns([1.6, 1])
                with c_s2_1:
                    btn_submit_step2 = st.form_submit_button("🩺 Tổng Hợp Phác Đồ & Xử Trí An Toàn (Bước 2)", type="primary", width="stretch")
                with c_s2_2:
                    btn_restart_ai = st.form_submit_button("🔄 Khám ca khác", width="stretch")

            if btn_restart_ai:
                st.session_state.step1_data = None
                st.session_state.final_conclusion = None
                st.session_state.uploaded_medical_images = []
                st.rerun()

            if btn_submit_step2:
                with st.spinner("🤖 Đang tổng hợp phác đồ điều trị và phân tích dấu hiệu cảnh báo..."):
                    diff_list = triage.get("differential_diagnosis", [])
                    top_disease = diff_list[0] if diff_list else {
                        "name": "Chưa xác định",
                        "icd": "R68.8",
                        "level": "Cần theo dõi",
                        "reason": "Cần theo dõi thêm diễn biến lâm sàng."
                    }

                    second_disease_str = (
                        f"{diff_list[1]['name']} ({diff_list[1]['level']})"
                        if len(diff_list) > 1
                        else "Chưa có chẩn đoán phân biệt thứ 2"
                    )

                    triage_summary = "\n".join(selected_answers) if selected_answers else "Không ghi nhận triệu chứng báo động kèm theo."
                    if extra_notes.strip():
                        triage_summary += f"\n- Ghi chú: {extra_notes.strip()}"

                    home_care_items = triage.get("home_care", [])
                    home_care_formatted = "\n".join([f"• {item.lstrip('•- ')}" for item in home_care_items]) if home_care_items else "• Tuân thủ chỉ định điều trị và tái khám từ Bác sĩ lâm sàng."

                    red_flags_items = triage.get("red_flags", [])
                    red_flags_joined = "\n".join([f"• {rf.lstrip('•- ')}" for rf in red_flags_items]) if red_flags_items else "• Đến viện ngay nếu triệu chứng tăng nặng đột ngột."

                    top_name = top_disease.get('name', 'Chưa xác định')
                    top_icd = top_disease.get('icd', 'N/A')
                    top_lvl = top_disease.get('level', 'N/A')

                    final_assessment_text = (
                        f"Chẩn đoán ưu tiên: {top_name} (ICD-10: {top_icd}) - Độ tin cậy: {top_lvl}\n"
                        f"Chẩn đoán phân biệt: {second_disease_str}\n"
                        f"Dấu hiệu sàng lọc & Ghi chú:\n{triage_summary}\n"
                        f"PHÁC ĐỒ ĐIỀU TRỊ & CHĂM SÓC (CHUẨN BỘ Y TẾ):\n{home_care_formatted}\n"
                        f"CẢNH BÁO NGUY HIỂM (CẦN ĐI VIỆN CẤP CỨU NGAY):\n{red_flags_joined}"
                    )

                    try:
                        with db_cursor() as cur_cs:
                            cur_cs.execute("""
                                INSERT INTO consultation_sessions (user_id, initial_symptoms, followup_answers, final_assessment, top_icd10)
                                VALUES (%s, %s, %s, %s, %s) RETURNING session_id
                            """, (USER_ID, step1['symptoms'], triage_summary, final_assessment_text, top_disease['icd']))
                            s_id = cur_cs.fetchone()[0]

                        st.session_state.final_conclusion = {
                            "session_id": s_id,
                            "top_disease": top_disease,
                            "triage": triage,
                            "triage_summary": triage_summary,
                            "symptoms": step1['symptoms']
                        }
                        st.rerun()
                    except Exception as ex:
                        st.error(f"Lỗi lưu trữ phiên khám: {ex}")

        else:
            with st.form("form_step1_inputs"):
                st.markdown("##### 📝 1. Thông tin bệnh nhân & Triệu chứng khởi phát")
                col_i1, col_i2 = st.columns([1, 1])
                with col_i1:
                    inp_age = st.number_input("🎂 Độ tuổi:", min_value=1, max_value=120, value=int(current_prof.get('age', 30)))
                with col_i2:
                    inp_gender = st.radio("Giới tính sinh học:", ["Nam", "Nữ"], horizontal=True)

                symptoms_input = st.text_area(
                    "Biểu hiện triệu chứng chi tiết: (nếu có kết quả khám xét nghiệm, xquang... hãy gửi lên cho bác sĩ)",
                    placeholder="Mô tả cụ thể cảm giác khó chịu, vị trí, thời gian bắt đầu...",
                    height=100
                )

                btn_step1 = st.form_submit_button(
                    "🔍 Khởi Chạy Phân Tầng Lâm Sàng (Bước 1)", 
                    type="primary", 
                    width="stretch"
                )

            st.markdown("##### 📸 Tải lên hoặc Chụp ảnh phiếu khám / kết quả xét nghiệm")

            if "uploaded_medical_images" not in st.session_state:
                st.session_state.uploaded_medical_images = []

            col_cam, col_gal = st.columns(2)

            with col_cam:
                with st.popover("📷 Mở Camera Chụp Ảnh", width="stretch"):
                    st.caption("Bấm nút chụp bên dưới để ghi nhận hình ảnh:")
                    camera_photo = st.camera_input("Chụp ảnh cận lâm sàng", key="camera_scanner_widget")
                    if camera_photo is not None:
                        photo_bytes = camera_photo.getvalue()
                        photo_name = f"camera_{datetime.datetime.now().strftime('%H%M%S')}.png"
                        if not any(img["name"] == photo_name for img in st.session_state.uploaded_medical_images):
                            st.session_state.uploaded_medical_images.append({
                                "name": photo_name,
                                "bytes": photo_bytes
                            })
                            st.toast("📷 Đã lưu ảnh chụp thành công!")
                            st.rerun()

            with col_gal:
                gallery_photos = st.file_uploader(
                    "🖼️ Chọn ảnh từ thư viện",
                    type=["png", "jpg", "jpeg", "webp"],
                    accept_multiple_files=True,
                    key="gallery_uploader_widget"
                )
                if gallery_photos:
                    for photo in gallery_photos:
                        photo_bytes = photo.getvalue()
                        if not any(img["name"] == photo.name for img in st.session_state.uploaded_medical_images):
                            st.session_state.uploaded_medical_images.append({
                                "name": photo.name,
                                "bytes": photo_bytes
                            })

            if st.session_state.uploaded_medical_images:
                st.markdown("##### 🖼️ Ảnh đã đính kèm (Thu nhỏ):")
                cols_preview = st.columns(6)
                for idx, img_item in enumerate(list(st.session_state.uploaded_medical_images)):
                    with cols_preview[idx % 6]:
                        st.image(img_item["bytes"], caption=img_item["name"][:10], width=90)
                        if st.button("❌ Xóa", key=f"del_img_btn_{idx}_{img_item['name']}"):
                            st.session_state.uploaded_medical_images.pop(idx)
                            st.rerun()

            if btn_step1:
                triage_res = run_multimodal_clinical_triage(
                    symptoms=symptoms_input.strip() if symptoms_input else "",
                    age=int(inp_age),
                    gender=inp_gender,
                    images_list=st.session_state.get("uploaded_medical_images", [])
                )

                if triage_res:
                    st.session_state.step1_data = {
                        "symptoms": symptoms_input.strip() if symptoms_input else "Xem kết quả cận lâm sàng đính kèm",
                        "age": int(inp_age),
                        "gender": inp_gender,
                        "triage": triage_res
                    }
                    st.rerun()

    # ==========================================================================
    # TAB 2: TỔNG QUAN PHÁC ĐỒ & ĐƠN THUỐC (Ở GIỮA)
    # ==========================================================================
    with tab_overview:
        st.markdown("#### 📜 Phác Đồ Điều Trị & Hướng Dẫn Chuẩn Bộ Y Tế")

        last_session = None
        try:
            with db_cursor() as cur_sess:
                cur_sess.execute("""
                    SELECT session_id, initial_symptoms, final_assessment, top_icd10, created_at 
                    FROM consultation_sessions 
                    WHERE user_id = %s 
                    ORDER BY session_id DESC LIMIT 1
                """, (USER_ID,))
                last_session = cur_sess.fetchone()
        except Exception as e:
            print(f"Lỗi lấy phác đồ điều trị: {e}")

        if last_session and last_session[2]:
            s_id, s_sym, s_assessment, s_icd, s_time = last_session

            # 1. Thu dọn khoảng trắng, chuẩn hóa các dòng sát cạnh nhau
            clean_lines = [line.strip() for line in s_assessment.splitlines() if line.strip()]
            
            formatted_html_parts = []
            for line in clean_lines:
                if "PHÁC ĐỒ ĐIỀU TRỊ" in line.upper():
                    # Dòng tiêu đề Phác đồ điều trị: Màu VÀNG CHANH nổi bật, viền bo nhẹ
                    formatted_html_parts.append(
                        f'<div style="background: #FEF08A; color: #854D0E; font-weight: 800; font-size: 13.5px; '
                        f'padding: 5px 10px; border-radius: 6px; margin: 8px 0 4px 0; border-left: 4px solid #EAB308;">'
                        f'📋 {line}</div>'
                    )
                elif "CẢNH BÁO NGUY HIỂM" in line.upper() or "CẢNH BÁO ĐỎ" in line.upper():
                    # Dòng Cảnh báo nguy hiểm: Màu ĐỎ NHẸ cảnh báo
                    formatted_html_parts.append(
                        f'<div style="background: #FEE2E2; color: #991B1B; font-weight: 800; font-size: 13.5px; '
                        f'padding: 5px 10px; border-radius: 6px; margin: 8px 0 4px 0; border-left: 4px solid #EF4444;">'
                        f'🚨 {line}</div>'
                    )
                elif line.startswith("•") or line.startswith("-") or line.startswith("✓"):
                    # Các dòng gạch đầu dòng nằm sát nhau, line-height gọn gàng
                    formatted_html_parts.append(
                        f'<div style="padding-left: 12px; margin-bottom: 2px; color: #1E293B; font-size: 13px; line-height: 1.35;">{line}</div>'
                    )
                else:
                    # Các dòng văn bản thông thường
                    formatted_html_parts.append(
                        f'<div style="margin-bottom: 3px; color: #1E293B; font-size: 13px; line-height: 1.35;">{line}</div>'
                    )

            final_formatted_content = "".join(formatted_html_parts)

            st.markdown(f"""
                <div style="background: #F8FBFE; border: 1.5px solid #BAE6FD; border-left: 5px solid #0284C7; border-radius: 12px; padding: 12px 16px; margin-bottom: 14px; box-shadow: 0 2px 8px rgba(2,132,199,0.05);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                        <span style="font-size: 14px; font-weight: 800; color: #0369A1;">
                            📋 Phác đồ ca khám #{s_id} (Mã ICD-10: <code>{s_icd}</code>)
                        </span>
                        <small style="font-size: 11px; color: #64748B;">Ngày: {str(s_time)[:16]}</small>
                    </div>
                    <div style="font-size: 12px; color: #475569; margin-bottom: 6px;">
                        <b>Bệnh cảnh / Xét nghiệm:</b> <i>"{s_sym}"</i>
                    </div>
                    <div style="background: #FFFFFF; border: 1px solid #E2ECF5; border-radius: 8px; padding: 10px 14px;">
                        {final_formatted_content}
                    </div>
                    <div style="margin-top: 6px; font-size: 11px; color: #0284C7; font-weight: 700;">
                        🏛️ Hướng dẫn chẩn đoán và điều trị chuẩn hóa theo tài liệu Bộ Y tế Việt Nam.
                    </div>
                </div>
            """, unsafe_allow_html=True)
        else:
            st.info("💡 Bạn chưa có phác đồ điều trị nào được lưu. Hãy qua tab **'🩺 Khám Bệnh Online Trả Lời & Phân Tích Bệnh Lý'** để tra soát bệnh và thiết lập phác đồ.")

        st.markdown("<hr style='margin: 14px 0;'>", unsafe_allow_html=True)

        with st.expander("➕ TỰ THÊM THUỐC MỚI VÀO ĐƠN", expanded=False):
            with st.form("form_add_manual_med_render"):
                in_m_name = st.text_input("Tên thuốc & hàm lượng:", placeholder="VD: Panadol Extra 500mg...")
                in_m_dos = st.text_input("Liều dùng:", value="1 viên")
                in_m_time = st.text_input("Khung giờ uống:", value="08:00, 18:00")
                in_m_inst = st.text_area("Hướng dẫn sử dụng:", value="Uống sau ăn no 30 phút")
                btn_save_manual_med = st.form_submit_button("✅ Lưu Thuốc Vào Đơn (Render)", type="primary")

            if btn_save_manual_med and in_m_name.strip():
                with db_cursor() as cur_save_m:
                    cur_save_m.execute("""
                        INSERT INTO user_prescriptions (user_id, medicine_name, dosage, alarm_time, total_quantity, start_date, instructions, source)
                        VALUES (%s, %s, %s, %s, '10 viên', %s, %s, 'Nhập thủ công')
                    """, (USER_ID, in_m_name.strip(), in_m_dos.strip(), in_m_time.strip(), str(datetime.date.today()), in_m_inst.strip()))
                st.toast("🎉 Đã thêm thuốc vào đơn thành công!")
                st.rerun()

        st.markdown("#### 💊 Lịch Uống Thuốc Hàng Ngày")

        user_medicines = []
        try:
            with db_cursor() as cur_meds:
                cur_meds.execute("""
                    SELECT id, medicine_name, dosage, alarm_time, instructions, source 
                    FROM user_prescriptions WHERE user_id = %s ORDER BY id ASC
                """, (USER_ID,))
                user_medicines = cur_meds.fetchall()
        except Exception:
            user_medicines = []

        if not user_medicines:
            st.info("💡 Bạn hiện chưa có lịch uống thuốc nào. Nhấn vào mục 'Tự thêm thuốc mới' ở trên để tạo đơn.")
        else:
            for med in user_medicines:
                m_id, m_name, m_dos, m_time, m_inst, m_src = med
                with st.container():
                    col_info, col_edit, col_del = st.columns([3.8, 0.8, 0.8], gap="small")

                    with col_info:
                        st.markdown(f"""
                            <div style="background:#F8FAFC; border:1px solid #CBD5E1; border-radius:10px; padding:10px 14px; margin-bottom:6px;">
                                <div style="font-size:14px; color:#0F172A;">
                                    🔔 Giờ uống: <b style="color:#0284C7;">{m_time}</b> | 💊 <b>{m_name}</b> ({m_dos})
                                </div>
                                <div style="font-size:12px; color:#64748B; margin-top:2px;">
                                    📋 Hướng dẫn: {m_inst} <span style="font-size:11px; color:#94A3B8;">({m_src})</span>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)

                    with col_edit:
                        with st.popover("✏️ Sửa", width="stretch"):
                            st.markdown(f"**Chỉnh sửa:** {m_name}")
                            with st.form(f"form_edit_med_{m_id}"):
                                edit_name = st.text_input("Tên thuốc & hàm lượng:", value=m_name)
                                edit_dos = st.text_input("Liều dùng:", value=m_dos)
                                edit_time = st.text_input("Giờ uống:", value=m_time)
                                edit_inst = st.text_area("Hướng dẫn:", value=m_inst)
                                btn_update_med = st.form_submit_button("💾 Cập nhật", type="primary", width="stretch")

                            if btn_update_med and edit_name.strip():
                                with db_cursor() as cur_up:
                                    cur_up.execute("""
                                        UPDATE user_prescriptions 
                                        SET medicine_name = %s, dosage = %s, alarm_time = %s, instructions = %s
                                        WHERE id = %s AND user_id = %s
                                    """, (edit_name.strip(), edit_dos.strip(), edit_time.strip(), edit_inst.strip(), m_id, USER_ID))
                                st.toast(f"✅ Đã cập nhật thông tin thuốc {edit_name}!")
                                st.rerun()

                    with col_del:
                        if st.button("🗑️ Xóa", key=f"btn_delete_med_{m_id}", type="secondary", width="stretch"):
                            with db_cursor() as cur_del:
                                cur_del.execute("DELETE FROM user_prescriptions WHERE id = %s AND user_id = %s", (m_id, USER_ID))
                            st.toast(f"🗑️ Đã xóa thuốc {m_name} khỏi đơn!")
                            st.rerun()

    # ==========================================================================
    # TAB 3: LỊCH SỬ KHÁM BỆNH (Ở CUỐI CÙNG)
    # ==========================================================================
    with tab_history:
        st.subheader(f"📋 Lịch Sử Tra Soát Bệnh: {current_prof['full_name']}")
        sessions_history = []
        try:
            with db_cursor() as cur_h:
                cur_h.execute("SELECT session_id, initial_symptoms, final_assessment, top_icd10, created_at FROM consultation_sessions WHERE user_id = %s ORDER BY session_id DESC", (USER_ID,))
                sessions_history = cur_h.fetchall()
        except Exception:
            sessions_history = []

        if sessions_history:
            for item in sessions_history:
                with st.expander(f"🩺 Ca khám #{item[0]} (ICD: {item[3]}) - Ngày: {str(item[4])[:19]}"):
                    st.markdown(f"**Triệu chứng:** {item[1]}")
                    if item[2]:
                        st.markdown(f"**Kết luận:** {item[2]}")
        else:
            st.info("💡 Bạn chưa có lịch sử tra soát bệnh nào.")


# ==============================================================================
# PHÂN HỆ 2: KHÁM SỨC KHỎE & ĐẶC QUYỀN VIP
# ==============================================================================
elif st.session_state.main_navigation == "🩺 Khám Sức Khỏe & Đặc Quyền VIP":
    st.markdown("<div style='font-size:20px; font-weight:800; color:#0C3861; margin-bottom:4px;'>🩺 TRUNG TÂM KHÁM SỨC KHỎE & ĐẶC QUYỀN VIP</div>", unsafe_allow_html=True)
    st.markdown("<div style='font-size:12px; color:#64748B; margin-bottom:14px;'>👨‍⚕️ Cố vấn chuyên môn: DR. Nguyễn Tiến Toàn | ✉️ Email: toanbvtimhn@gmail.com</div>", unsafe_allow_html=True)

    c_vip1, c_vip2 = st.columns([1.2, 1])
    with c_vip1:
        st.markdown("#### 📅 Đặt Lịch Hẹn Gặp Bác Sĩ Tư Vấn Trực Tiếp")
        with st.form("form_book_doc_render"):
            app_date = st.date_input("Chọn ngày hẹn khám:", min_value=datetime.date.today())
            app_time = st.selectbox("Khung giờ:", ["08:30 - 09:30 (Sáng)", "14:30 - 15:30 (Chiều)", "19:30 - 20:30 (Tối Online)"])
            app_type = st.radio("Hình thức:", ["Gặp trực tiếp tại Phòng khám", "Tư vấn Online qua Video Call"], horizontal=True)
            app_notes = st.text_area("Mô tả vấn đề cần tư vấn:")
            btn_book = st.form_submit_button("Gửi Lịch Hẹn Bác Sĩ (Lưu Render)", type="primary")

        if btn_book and app_notes.strip():
            with db_cursor() as cur_ap:
                cur_ap.execute("""
                    INSERT INTO doctor_appointments (user_id, patient_name, patient_phone, date, time, type, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (USER_ID, current_prof['full_name'], current_prof.get('phone', ''), str(app_date), app_time, app_type, app_notes.strip()))
            get_system_notifications.clear()
            st.success("🎉 Đã lưu lịch hẹn lên Render thành công! Bác sĩ sẽ liên hệ sớm.")

    with c_vip2:
        st.markdown("#### 🧪 Đăng Ký Gói Xét Nghiệm MEDLATEC Tận Nhà")
        with st.form("form_medlatec_reg_render"):
            pkg_choice = st.selectbox("Chọn gói tầm soát:", [
                "Gói 1: Tầm soát ung thư đường tiêu hóa (1.350.000 đ)",
                "Gói 2: Chức năng Gan - Thận - Mỡ máu (765.000 đ)",
                "Gói 3: Tầm soát đột quỵ & Tim mạch (1.620.000 đ)"
            ])
            sample_addr = st.text_input("Địa chỉ lấy mẫu tận nơi:", value="Theo địa chỉ hồ sơ")
            btn_med_reg = st.form_submit_button("🎁 Xác Nhận Đăng Ký (Lưu Render)", type="primary")

        if btn_med_reg:
            with db_cursor() as cur_mr:
                cur_mr.execute("""
                    INSERT INTO medlatec_registrations (user_id, patient_name, patient_phone, package, address)
                    VALUES (%s, %s, %s, %s, %s)
                """, (USER_ID, current_prof['full_name'], current_prof.get('phone', ''), pkg_choice, sample_addr))
            st.success("🎉 Đã tiếp nhận đăng ký xét nghiệm tận nhà lên Render!")


# ==============================================================================
# PHÂN HỆ 3: CẤP CỨU 115 & HƯỚNG DẪN SƠ CỨU TẠI CHỖ
# ==============================================================================
elif st.session_state.main_navigation == "🚨 Cấp Cứu 115 & Sơ Cứu Tại Chỗ":
    st.markdown("""
        <div style="background: linear-gradient(135deg, #DC2626 0%, #991B1B 100%); color: white; padding: 18px 20px; border-radius: 16px; margin-bottom: 20px; box-shadow: 0 4px 18px rgba(220, 38, 38, 0.25);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <div style="font-size: 20px; font-weight: 900; letter-spacing: 0.5px;">🚨 TRUNG TÂM CẤP CỨU 115 TOÀN QUỐC & SƠ CỨU TẠI CHỖ</div>
                    <div style="font-size: 13px; color: #FEE2E2; margin-top: 4px;">Tài liệu hướng dẫn sơ cấp cứu khẩn cấp theo chuẩn Hiệp hội Tim mạch Hoa Kỳ (AHA) & Bộ Y Tế</div>
                </div>
                <div>
                    <a href="tel:115" style="background: #FFFFFF; color: #DC2626; font-size: 15px; font-weight: 900; padding: 10px 18px; border-radius: 12px; text-decoration: none; display: inline-block; box-shadow: 0 2px 8px rgba(0,0,0,0.15);">
                        📞 GỌI NGAY 115
                    </a>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # 1. NGỪNG TUẦN HOÀN - HÔ HẤP (CPR)
    with st.expander("🚨 1. Sơ cứu Ngừng tim / Ngừng thở (Hồi sinh tim phổi - CPR) [KHẨN CẤP]", expanded=True):
        st.markdown("""
        * **Nhận diện:** Nạn nhân bất tỉnh, lay gọi không phản ứng, không thở hoặc chỉ thở ngáp cá.
        * **Các bước hành động ngay lập tức:**
            1. **Gọi hỗ trợ:** Hô hoán người xung quanh và bấm gọi ngay **115** (bật loa ngoài).
            2. **Tư thế nạn nhân:** Đặt nạn nhân nằm ngửa trên nền phẳng, cứng, khô ráo.
            3. **Vị trí ép tim:** Đặt gót một bàn tay vào chính giữa nửa dưới xương ức nạn nhân, bàn tay kia đan lên trên. Giữ thẳng hai tay.
            4. **Tần số & Độ sâu:** 
                * Ép sâu ít nhất **5 – 6 cm** ở người lớn.
                * Tốc độ ép: **100 – 120 lần/phút** (theo nhịp bài hát *Stayin' Alive*).
                * Để lồng ngực nở lại hoàn toàn sau mỗi lần ép, không tì đè liên tục.
            5. **Nếu đã được đào tạo thổi ngạt:** Tỉ lệ **30 lần ép tim : 2 lần thổi ngạt**. Nếu không thổi ngạt được, **chỉ cần ép tim liên tục không ngừng nghỉ** cho tới khi nhân viên y tế tiếp cận.
        """)

    # 2. ĐỘT QUỴ NÃO (FAST)
    with st.expander("🧠 2. Nhận biết và Xử trí Đột quỵ não (Tai biến mạch máu não) [QUY TẮC FAST]"):
        st.markdown("""
        * **Nhận diện nhanh quy tắc F.A.S.T:**
            * **F (Face - Mặt):** Miệng méo, nhân trung lệch, một bên mặt xệ xuống khi cười hoặc nhe răng.
            * **A (Arm - Tay):** Yếu hoặc liệt một bên tay/chân, không thể giơ đều 2 tay lên cao.
            * **S (Speech - Giọng nói):** Nói ngọng, phát âm khó, nói không rõ từ hoặc không hiểu lời nói.
            * **T (Time - Thời gian):** Nếu có bất kỳ dấu hiệu nào trên, gọi **115 đưa đi viện ngay lập tức**!
        * **Nguyên tắc "Giờ Vàng" (Dưới 3 - 4.5 giờ đầu):**
            * **TUYỆT ĐỐI KHÔNG:** Cạo gió, bấm huyệt, chích máu 10 đầu ngón tay hay cho uống thuốc hạ áp/viên An Cung Trúc Hoàn (nguy cơ sặc gây tắc đường thở tử vong).
            * **NÊN LÀM:** Đặt nạn nhân nằm nghiêng an toàn (nếu hôn mê/nôn), nới lỏng cổ áo, kê đầu cao 30 độ và đưa ngay đến bệnh viện có đơn vị đột quỵ can thiệp mạch não.
        """)

    # 3. DỊ VẬT ĐƯỜNG THỞ (HÓC NGHẸN)
    with st.expander("🫁 3. Xử trí Hóc dị vật đường thở (Thủ thuật Heimlich)"):
        st.markdown("""
        * **Nhận diện:** Nạn nhân đột ngột ôm cổ họng, ho không ra tiếng, mặt tím tái, khó thở dữ dội.
        * **Đối với người lớn và trẻ lớn còn tỉnh:**
            1. Đứng phía sau nạn nhân, vòng 2 tay qua eo.
            2. Nắm một bàn tay thành nắm đấm, đặt ngón cái ngay phía trên rốn và dưới mũi ức.
            3. Tay kia nắm chặt lấy nắm đấm, giật mạnh **hướng vào trong và lên trên** một cách dứt khoát.
            4. Lặp lại liên tục cho đến khi dị vật bật ra ngoài hoặc nạn nhân thở lại được.
        * **Đối với trẻ nhỏ dưới 1 tuổi (Vỗ lưng - Ấn ngực):**
            * Đặt trẻ nằm sấp dọc theo cẳng tay người cứu hộ, đầu chúc thấp hơn ngực.
            * Dùng gót bàn tay vỗ dứt khoát **5 lần** vào lưng giữa hai xương bả vai.
            * Nếu chưa ra: Lật ngửa trẻ lại, dùng 2 ngón tay ấn mạnh vào giữa ngực **5 lần**.
        """)

    # 4. NHỒI MÁU CƠ TIM (ĐAU THẮT NGỰC)
    with st.expander("❤️ 4. Sơ cứu Cơn đau thắt ngực cấp & Nghi ngờ Nhồi máu cơ tim"):
        st.markdown("""
        * **Nhận diện:** Đau thắt, đè nặng sau xương ức kéo dài trên 15 phút, lan lên vai trái, cổ, hàm hoặc cánh tay trái; kèm vã mồ hôi lạnh, khó thở, hoảng loạn.
        * **Các bước xử trí:**
            1. Cho nạn nhân dừng mọi hoạt động ngay lập tức, ngồi nghỉ ngơi ở tư thế nửa nằm nửa ngồi (tư thế Fowler) để giảm tải cho tim.
            2. Nới rộng cổ áo, thắt lưng, mở cửa thông thoáng gió.
            3. Bấm gọi cấp cứu **115** ngay.
            4. Nếu nạn nhân có tiền sử bệnh mạch vành và có sẵn thuốc Bác sĩ kê (như viên ngậm dưới lưỡi Nitroglycerin), hướng dẫn ngậm 1 viên.
        """)

    # 5. XỬ TRÍ CO GIẬT / ĐỘNG KINH
    with st.expander("⚡ 5. Xử trí Cơn Co giật / Động kinh"):
        st.markdown("""
        * **Việc NÊN làm:**
            * Đỡ nạn nhân nằm xuống sàn nhẹ nhàng, kê vật mềm (gối, áo) dưới đầu.
            * Gạt bỏ các vật sắc nhọn, bàn ghế xung quanh để tránh va đập chấn thương.
            * Sau khi hết co giật, lật nạn nhân nằm nghiêng sang một bên để đờm nhớt chảy ra ngoài.
            * Ghi lại thời gian cơn co giật diễn ra.
        * **Việc TUYỆT ĐỐI TRÁNH:**
            * **Không** cố gắng đè chặt, kìm kẹp tay chân nạn nhân.
            * **Không** nhét bất cứ thứ gì vào miệng nạn nhân (thìa, đũa, ngón tay) vì có thể làm gãy răng, tổn thương hàm hoặc rơi vào đường thở gây ngạt thở.
        """)

    # 6. SƠ CỨU BỎNG NHIỆT
    with st.expander("🔥 6. Sơ cứu Vết Bỏng (Nhiệt độ cao / Nước sôi)"):
        st.markdown("""
        * **Bước 1 (Làm mát vết bỏng):** Ngâm hoặc xả nhẹ nhàng vùng bị bỏng dưới vòi **nước mát sạch** (15 – 25°C) liên tục trong ít nhất **15 – 20 phút**.
        * **Bước 2:** Nhẹ nhàng cởi bỏ đồ trang sức, đồng hồ, giày dép hoặc quần áo chật trước khi vùng bỏng bị sưng nề.
        * **Bước 3:** Che phủ vết bỏng bằng gạc vô trùng hoặc khăn sạch, băng lỏng tay.
        * **Cảnh báo nguy hại:**
            * **Không** chườm đá lạnh trực tiếp lên vết bỏng (gây co mạch đột ngột, hoại tử mô).
            * **Không** bôi kem đánh răng, mỡ trăn, nước mắm hay lòng đỏ trứng gà lên vết bỏng (nguy cơ nhiễm trùng máu nặng).
            * **Không** tự ý chọc vỡ các bọng nước phồng rộp.
        """)

    # 7. XỬ TRÍ XUẤT HUYẾT / CHẢY MÁU NGOẠI TỬ
    with st.expander("🩸 7. Sơ cứu Vết thương Chảy máu nhiều & Băng ép cầm máu"):
        st.markdown("""
        * **Bước 1 (Ép trực tiếp):** Dùng miếng gạc sạch hoặc vải dày ấn mạnh trực tiếp lên miệng vết thương để chặn dòng máu.
        * **Bước 2 (Nâng cao chi):** Nâng phần chi bị tổn thương lên cao hơn mức của tim (nếu không nghi ngờ gãy xương) để giảm áp lực máu.
        * **Bước 3 (Băng ép):** Dùng cuộn băng quấn chặt vừa phải đè lên miếng gạc.
        * **Lưu ý dị vật găm sâu:** Nếu có mảnh kính, dao găm trong vết thương, **tuyệt đối không tự ý rút ra** vì dị vật đang đóng vai trò nút chặn cầm máu; hãy chèn gạc xung quanh dị vật rồi cố định lại và chuyển viện ngay.
        """)
    # 8. SƠ CỨU ĐIỆN GIẬT
    with st.expander("⚡ 8. Sơ cứu Nạn nhân Bị Điện giật [NGUY HIỂM TÍNH MẠNG]"):
        st.markdown("""
        * **Bước 1: ĐẢM BẢO AN TOÀN CHO NGƯỜI CỨU HỘ (ƯU TIÊN SỐ 1)**
            * **TUYỆT ĐỐI KHÔNG** chạm trực tiếp vào người nạn nhân khi chưa ngắt nguồn điện.
            * Nhanh chóng ngắt cầu dao tổng, rút phích cắm hoặc dập aptomat nguồn điện.
            * Nếu không thể ngắt điện ngay: Đứng trên vật khô cách điện (ván gỗ khô, tấm cao su, chồng sách báo), dùng vật liệu không dẫn điện (gậy gỗ khô, chổi cán nhựa, thanh tre khô) gạt dây điện hoặc đẩy nạn nhân tách rời khỏi nguồn điện.
        * **Bước 2: ĐÁNH GIÁ TÌNH TRẠNG NẠN NHÂN**
            * Gọi ngay cấp cứu **115**.
            * **Nếu nạn nhân ngừng thở, ngừng tim (bất tỉnh, không thấy lồng ngực phập phồng):** Tiến hành ép tim ngoài lồng ngực (**CPR**) ngay lập tức (xem lại Mục 1) mà không được chậm trễ dù chỉ 1 phút.
            * **Nếu nạn nhân còn tỉnh:** Đặt nằm nghỉ ngơi ở nơi thoáng mát, trấn an tinh thần và nới lỏng quần áo.
            * **Nếu nạn nhân hôn mê nhưng vẫn thở:** Đặt nằm ở tư thế nghiêng an toàn để tránh tụt lưỡi và hít sặc đờm nhớt.
        * **Bước 3: XỬ TRÍ TỔN THƯƠNG KÈM THEO**
            * Kiểm tra và sơ cứu vết bỏng điện tại điểm vào/ra của dòng điện: Băng che vết bỏng bằng gạc sạch hoặc vải khô sạch (không chườm đá hay bôi thuốc mỡ).
            * Hạn chế di chuyển mạnh vùng cổ/lưng nếu nạn nhân bị ngã từ trên cao xuống vì có nguy cơ chấn thương cột sống.
        * **LƯU Ý Y KHOA QUAN TRỌNG:**
            * Dòng điện chạy qua cơ thể có thể gây rối loạn nhịp tim muộn (rung thất) sau đó nhiều giờ. Do đó, **kể cả khi nạn nhân đã tỉnh táo và có vẻ bình thường, vẫn bắt buộc phải đưa đến cơ sở y tế để đo điện tim (ECG) và theo dõi.**
        """)    


# ==============================================================================
# PHÂN HỆ 4: THỰC PHẨM CHỨC NĂNG
# ==============================================================================
elif st.session_state.main_navigation == "🌿 TPCN phòng ngừa ung thư":
    st.markdown("<div style='font-size:20px; font-weight:800; color:#0C3861; margin-bottom:4px;'>🌿 DANH MỤC THỰC PHẨM CHỨC NĂNG HỖ TRỢ PHÒNG NGỪA UNG THƯ</div>", unsafe_allow_html=True)
    st.markdown("<div style='font-size:12px; color:#64748B; margin-bottom:14px;'>👨‍⚕️ Phụ trách chuyên môn: DR. Nguyễn Tiến Toàn | ✉️ Email: toanbvtimhn@gmail.com</div>", unsafe_allow_html=True)

    available_icons = ["🌿", "💊", "🫀", "🧠", "🦴", "👁️", "🩸", "🛡️", "🧬"]

    if IS_SUPER_ADMIN:
        with st.expander("➕ [DÀNH CHO SUPER ADMIN] THÊM SẢN PHẨM MỚI LÊN RENDER", expanded=False):
            with st.form("add_product_form_render"):
                p_icon = st.selectbox("Chọn Icon đại diện:", available_icons)
                p_name = st.text_input("Tên sản phẩm TPCN:")
                p_orig_price = st.number_input("Giá gốc niêm yết (VNĐ):", min_value=10000, value=500000, step=10000)
                p_discount = st.slider("Mức khuyến mãi giảm giá (%):", min_value=0, max_value=80, value=10)
                p_usage = st.text_area("Công dụng & Thành phần:")
                btn_add_prod = st.form_submit_button("✅ Lưu Sản Phẩm Lên Render", type="primary")

                if btn_add_prod and p_name.strip():
                    calc_final = int(p_orig_price * (1 - p_discount / 100))
                    with db_cursor() as cur_add_p:
                        cur_add_p.execute("""
                            INSERT INTO supplements_catalog (icon, name, usage, original_price, discount_percent, final_price)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (p_icon, p_name.strip(), p_usage.strip(), int(p_orig_price), p_discount, calc_final))

                    get_cached_supplements.clear()
                    st.toast("🎉 Đã thêm sản phẩm lên Render!")
                    st.rerun()

    products = get_cached_supplements()

    col_list, col_cart = st.columns([1.8, 1.2])
    with col_list:
        for prod in products:
            with st.container():
                st.markdown(f"""
                <div style="background:#FFFFFF; border:1px solid #E2ECF5; border-radius:14px; padding:14px; margin-bottom:10px; box-shadow:0 2px 8px rgba(15,60,100,0.03);">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <b style="font-size:15px; color:#0C3861;">{prod['icon']} {prod['name']}</b>
                        <span style="background-color:#DC2626; color:white; padding:3px 8px; border-radius:6px; font-weight:bold; font-size:12px;">GIẢM {prod['discount_percent']}%</span>
                    </div>
                    <p style="font-size:12.5px; color:#475569; margin:6px 0;"><b>Công dụng:</b> {prod['usage']}</p>
                    <p style="margin:4px 0;">
                        <span style="text-decoration: line-through; color: #94A3B8; font-size: 13px;">{prod['original_price']:,} đ</span> 
                        <span style="color:#DC2626; font-weight:800; font-size: 16px; margin-left:8px;">{prod['final_price']:,} đ</span>
                    </p>
                </div>
                """, unsafe_allow_html=True)

                if IS_ANY_ADMIN:
                    c_qty, c_buy, c_del_tpcn = st.columns([1, 1.2, 0.8])
                else:
                    c_qty, c_buy = st.columns([1, 1.5])
                    c_del_tpcn = None

                with c_qty:
                    qty = st.number_input("Số lượng:", min_value=1, max_value=50, value=1, key=f"qty_{prod['id']}")
                with c_buy:
                    if st.button("🛒 Thêm giỏ", key=f"btn_buy_{prod['id']}", width="stretch"):
                        st.session_state.cart_items[prod['id']] = st.session_state.cart_items.get(prod['id'], 0) + qty
                        st.toast(f"Đã thêm {qty} hộp {prod['name']} vào giỏ hàng!")
                        st.rerun()

                if c_del_tpcn:
                    with c_del_tpcn:
                        if st.button("🗑️ Xóa", key=f"btn_del_prod_{prod['id']}", type="secondary", width="stretch"):
                            with db_cursor() as cur_del_p:
                                cur_del_p.execute("DELETE FROM supplements_catalog WHERE id = %s", (prod['id'],))
                            get_cached_supplements.clear()
                            st.toast(f"Đã xóa sản phẩm '{prod['name']}' khỏi danh mục!")
                            st.rerun()

    with col_cart:
        st.markdown("### 🛍️ Giỏ Hàng & Thanh Toán")
        valid_cart_items = {pid: count for pid, count in st.session_state.cart_items.items() if count > 0}

        if not valid_cart_items:
            st.caption("Chưa có sản phẩm nào trong giỏ hàng.")
            if "last_order_info" in st.session_state:
                st.markdown("---")
                last_ord = st.session_state.last_order_info
                st.success(f"🎉 **Đơn hàng #{last_ord['order_id']} đã ghi nhận thành công!**")

                st.markdown(f"""
                    <div style="background:#F0FDF4; border:1.5px solid #86EFAC; border-radius:12px; padding:14px; margin-top:8px;">
                        <div style="font-size:13.5px; font-weight:800; color:#166534; margin-bottom:8px;">
                            💳 THÔNG TIN THANH TOÁN CHUYỂN KHOẢN
                        </div>
                        <div style="font-size:12.5px; color:#1E293B; line-height:1.6;">
                            🏦 Ngân hàng: <b>MBBank (Ngân hàng Quân Đội)</b><br>
                            🔢 Số tài khoản: <b>0988888888</b><br>
                            👤 Chủ tài khoản: <b>NGUYEN TIEN TOAN</b><br>
                            💰 Số tiền: <b style="color:#DC2626;">{last_ord['total']:,} VNĐ</b><br>
                            🏷️ Mã nội dung CK (Code): <span style="background:#FEF08A; padding:2px 8px; border-radius:4px; font-weight:800; color:#854D0E; font-family:monospace;">{last_ord['code']}</span>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                vietqr_url = f"https://img.vietqr.io/image/BIDV-1155102-compact2.png?amount={last_ord['total']}&addInfo={last_ord['code']}&accountName=NGUYEN%20TIEN%20TOAN"
                st.image(vietqr_url, caption="Quét mã VietQR để thanh toán tự động", width="stretch")

                if st.button("🔄 Đặt đơn hàng mới", width="stretch"):
                    del st.session_state["last_order_info"]
                    st.rerun()
        else:
            total_payment = 0
            items_summary = []
            for pid, count in list(valid_cart_items.items()):
                p_obj = next((p for p in products if p['id'] == pid), None)
                if p_obj:
                    subtotal = p_obj['final_price'] * count
                    total_payment += subtotal
                    items_summary.append(f"{p_obj['name']} (x{count})")
                    st.write(f"- **{p_obj['icon']} {p_obj['name']}** x {count} = `{subtotal:,} đ`")

            st.markdown(f"#### 💰 Tổng tiền: <span style='color:#DC2626; font-weight:bold;'>{total_payment:,} đ</span>", unsafe_allow_html=True)

            if "temp_order_code" not in st.session_state:
                st.session_state.temp_order_code = f"MED{random.randint(100000, 999999)}"
            order_code = st.session_state.temp_order_code

            st.markdown(f"""
                <div style="background: #F8FAFC; border: 1px dashed #0284C7; border-radius: 10px; padding: 10px 12px; margin: 10px 0;">
                    <div style="font-size: 11.5px; font-weight: 700; color: #0284C7;">🏦 THÔNG TIN NGÂN HÀNG THỤ HƯỞNG</div>
                    <div style="font-size: 11.5px; color: #334155; margin-top: 4px;">
                        • BIDV: <b>1155102</b> (NGUYEN TIEN TOAN)<br>
                        • Mã giao dịch (Code): <b style="color: #D97706;">{order_code}</b>
                    </div>
                </div>
            """, unsafe_allow_html=True)

            deliv_addr = st.text_input("Địa chỉ giao hàng tận nơi:", value="Giao về địa chỉ theo hồ sơ")

            if st.button("🚀 Xác Nhận Đặt Mua & Lấy Mã Thanh Toán", type="primary", width="stretch"):
                formatted_items = f"[{order_code}] " + "; ".join(items_summary)
                order_id = None
                with db_cursor() as cur_ord:
                    cur_ord.execute("""
                        INSERT INTO product_orders (user_id, customer_name, customer_phone, items, total_amount, address)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                    """, (USER_ID, current_prof['full_name'], current_prof.get('phone', ''), formatted_items, total_payment, deliv_addr))
                    order_id = cur_ord.fetchone()[0]

                get_system_notifications.clear()

                st.session_state.last_order_info = {
                    "order_id": order_id,
                    "code": order_code,
                    "total": total_payment,
                    "items": formatted_items
                }

                st.session_state.cart_items = {}
                del st.session_state["temp_order_code"]
                st.toast(f"🎉 Đã tạo đơn hàng #{order_id} thành công!")
                st.rerun()


# ==============================================================================
# PHÂN HỆ 5: QUẢN TRỊ HỆ THỐNG
# ==============================================================================
elif st.session_state.main_navigation == "⚙️ Quản Trị Hệ Thống & Phân Quyền" and IS_ANY_ADMIN:
    st.markdown("<div style='font-size:20px; font-weight:800; color:#0C3861; margin-bottom:4px;'>⚙️ TRUNG TÂM ĐIỀU HÀNH & PHÂN QUYỀN QUẢN TRỊ</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:12px; color:#64748B; margin-bottom:14px;'>👨‍⚕️ Cấp bậc tài khoản: **{role_label}**</div>", unsafe_allow_html=True)

    tab_orders, tab_doc_admin, tab_med_registrations, tab_med_catalog, tab_users, tab_history_admin = st.tabs([
        "📦 1. Đơn Hàng TPCN",
        "📅 2. Lịch Hẹn Bác Sĩ VIP",
        "🧪 3. Đăng Ký Gói Khám & Chỉ Định",
        "📋 4. Bảng Giá & Gói Medlatec",
        "👥 5. Danh Sách Người Bệnh & Đơn Thuốc",
        "📑 6. Lịch Sử Khám Bệnh",
    ])

    with tab_orders:
        st.subheader("📦 Danh Sách Đơn Đặt Mua TPCN Từ Các Máy")
        all_orders = []
        try:
            with db_cursor() as cur_o:
                cur_o.execute("SELECT id, customer_name, customer_phone, items, total_amount, address, created_at FROM product_orders ORDER BY id DESC")
                all_orders = cur_o.fetchall()
        except Exception:
            all_orders = []

        if not all_orders:
            st.info("💡 Chưa có đơn hàng nào trên Render.")
        else:
            for ord_item in all_orders:
                with st.expander(f"📦 Đơn #{ord_item[0]} | {ord_item[1]} - 📞 {ord_item[2]} ({str(ord_item[6])[:16]})", expanded=True):
                    st.write(f"- **Sản phẩm:** `{ord_item[3]}` | **Tổng tiền:** `{ord_item[4]:,} đ`")
                    st.write(f"- **Địa chỉ:** {ord_item[5]}")
                    if st.button("🗑️ Xóa Đơn Hàng", key=f"btn_del_ord_{ord_item[0]}", type="primary"):
                        with db_cursor() as cur_del_o:
                            cur_del_o.execute("DELETE FROM product_orders WHERE id = %s", (ord_item[0],))
                        get_system_notifications.clear()
                        st.toast(f"Đã xóa đơn #{ord_item[0]} trên Render!")
                        st.rerun()

    with tab_doc_admin:
        st.subheader("📅 Quản Lý Lịch Hẹn Gặp Bác Sĩ VIP")
        doc_list = []
        try:
            with db_cursor() as cur_a:
                cur_a.execute("SELECT id, patient_name, patient_phone, date, time, type, notes FROM doctor_appointments ORDER BY id DESC")
                doc_list = cur_a.fetchall()
        except Exception:
            doc_list = []

        if not doc_list:
            st.info("💡 Chưa có lịch hẹn gặp Bác sĩ nào trên Render.")
        else:
            for app in doc_list:
                with st.expander(f"📅 Ca Hẹn #{app[0]} | {app[1]} - 📞 {app[2]} ({app[3]} {app[4]})", expanded=True):
                    st.write(f"- 📍 **Hình thức:** {app[5]} | 📝 **Ghi chú:** *{app[6]}*")
                    if st.button("🗑️ Xóa Lịch Hẹn", key=f"btn_del_doc_app_{app[0]}", type="primary"):
                        with db_cursor() as cur_d:
                            cur_d.execute("DELETE FROM doctor_appointments WHERE id = %s", (app[0],))
                        get_system_notifications.clear()
                        st.toast(f"Đã xóa lịch hẹn #{app[0]} trên Render!")
                        st.rerun()

    with tab_med_registrations:
        st.subheader("🧪 Quản Lý Đăng Ký Gói Tầm Soát & Chỉ Định Riêng")
        med_list = []
        try:
            with db_cursor() as cur_m:
                cur_m.execute("SELECT id, patient_name, patient_phone, package, final_price, doctor_indications, indications_extra_price FROM medlatec_registrations ORDER BY id DESC")
                med_list = cur_m.fetchall()
        except Exception:
            med_list = []

        if not med_list:
            st.info("💡 Chưa có ca đăng ký xét nghiệm nào trên Render.")
        else:
            for m in med_list:
                with st.expander(f"🧪 Ca #{m[0]} | {m[1]} ({m[3]})", expanded=True):
                    st.write(f"- **Gói:** `{m[3]}` | **Giá gói:** {m[4]:,} đ")
                    if m[5]:
                        st.info(f"📋 **Chỉ định Bác sĩ:**\n{m[5]}\n\n💰 Phụ phí: {m[6]:,} đ")
                    if st.button("🗑️ Xóa Ca Xét Nghiệm", key=f"btn_del_med_reg_{m[0]}", type="primary"):
                        with db_cursor() as cur_del_m:
                            cur_del_m.execute("DELETE FROM medlatec_registrations WHERE id = %s", (m[0],))
                        st.toast(f"Đã xóa ca #{m[0]} trên Render!")
                        st.rerun()

    with tab_med_catalog:
        st.subheader("📋 Bảng Giá & Danh Mục Gói Medlatec")
        all_t = get_cached_tests_catalog()
        for t in all_t:
            st.write(f"- **{t[1]}** (`{t[2]}`): **{t[3]:,} đ**")

    with tab_users:
        st.subheader("👥 Quản Lý Hồ Sơ Người Bệnh, Kê Đơn & Chỉ Định Xét Nghiệm")
        all_tests_catalog_tab5 = get_cached_tests_catalog()
        all_profiles = list(st.session_state.profiles_dict.items())

        for uid, prof in all_profiles:
            user_is_vip = prof.get("is_vip", False)

            with st.expander(f"👤 ID: `{uid}` | **{prof['full_name']}** | 📞 {prof.get('phone', 'N/A')} {'[⭐ VIP]' if user_is_vip else ''}"):
                c_top_info, c_top_action = st.columns([2.2, 1])
                with c_top_info:
                    with st.form(f"form_edit_patient_{uid}"):
                        e_name = st.text_input("Họ và tên:", value=prof['full_name'], key=f"e_name_{uid}")
                        e_phone = st.text_input("SĐT:", value=prof.get('phone', ''), key=f"e_phone_{uid}")
                        e_age = st.number_input("Tuổi:", min_value=1, max_value=120, value=int(prof.get('age', 30)), key=f"e_age_{uid}")
                        if st.form_submit_button("💾 Lưu Thông Tin", type="primary"):
                            with db_cursor() as cur_up_u:
                                cur_up_u.execute("UPDATE app_users SET full_name = %s, phone = %s, age = %s WHERE user_id = %s", (e_name.strip(), e_phone.strip(), e_age, uid))

                            get_cached_users_from_render.clear()
                            st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()
                            st.toast("Đã cập nhật lên Render!")
                            st.rerun()

                with c_top_action:
                    vip_status_label = "🔒 Khóa VIP" if user_is_vip else "🔓 Mở Khóa VIP"
                    if st.button(vip_status_label, key=f"btn_toggle_vip_tab5_{uid}", width="stretch"):
                        with db_cursor() as cur_vip:
                            cur_vip.execute("UPDATE app_users SET is_vip = %s WHERE user_id = %s", (0 if user_is_vip else 1, uid))

                        get_cached_users_from_render.clear()
                        st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()
                        st.toast("Đã cập nhật VIP trên Render!")
                        st.rerun()

                    if IS_SUPER_ADMIN and uid != 1:
                        if st.button("🗑️ Xóa Bệnh Nhân", key=f"del_pat_{uid}", type="primary", width="stretch"):
                            with db_cursor() as cur_del_u:
                                cur_del_u.execute("DELETE FROM app_users WHERE user_id = %s", (uid,))
                                cur_del_u.execute("DELETE FROM user_prescriptions WHERE user_id = %s", (uid,))

                            get_cached_users_from_render.clear()
                            st.session_state.users_db, st.session_state.profiles_dict = get_all_users_from_db()
                            st.toast("Đã xóa bệnh nhân trên Render!")
                            st.rerun()

                st.markdown("---")
                col_left_med, col_right_test = st.columns(2)

                with col_left_med:
                    st.markdown("#### 💊 1. Kê Đơn Thuốc Trực Tiếp")
                    with st.form(f"form_doc_prescribe_{uid}"):
                        med_n = st.text_input("Tên thuốc & hàm lượng:", placeholder="VD: Augmentin 1g...", key=f"med_n_{uid}")
                        med_d = st.text_input("Liều dùng:", value="1 viên", key=f"med_d_{uid}")
                        med_t = st.text_input("Giờ uống:", value="08:00, 18:00", key=f"med_t_{uid}")
                        med_ins = st.text_input("Hướng dẫn:", value="Uống sau ăn no 30 phút", key=f"med_ins_{uid}")
                        if st.form_submit_button("➕ Thêm Thuốc (Lưu Render)", type="primary", width="stretch"):
                            if med_n.strip():
                                with db_cursor() as cur_med_in:
                                    cur_med_in.execute("""
                                        INSERT INTO user_prescriptions (user_id, medicine_name, dosage, alarm_time, total_quantity, start_date, instructions, source)
                                        VALUES (%s, %s, %s, %s, '10 viên', %s, %s, 'Bác sĩ kê')
                                    """, (uid, med_n.strip(), med_d.strip(), med_t.strip(), str(datetime.date.today()), med_ins.strip()))
                                get_system_notifications.clear()
                                st.toast("Đã thêm thuốc lên Render!")
                                st.rerun()

                with col_right_test:
                    st.markdown("#### 🧪 2. Chỉ Định Xét Nghiệm Riêng")
                    with st.form(f"form_direct_test_indication_{uid}"):
                        test_opts = {f"{t[1]} ({t[3]:,} đ)": (t[1], t[3]) for t in all_tests_catalog_tab5}
                        ms_sel = st.multiselect("Chọn xét nghiệm:", options=list(test_opts.keys()), key=f"ms_tests_tab5_{uid}")
                        doc_note = st.text_area("Ghi chú y lệnh:", placeholder="VD: Nhịn ăn sáng...", key=f"doc_note_tab5_{uid}")
                        if st.form_submit_button("🚀 Gửi Chỉ Định (Lưu Render)", type="primary", width="stretch"):
                            sel_lines = []
                            total_calc = 0
                            for k in ms_sel:
                                tn, tp = test_opts[k]
                                sel_lines.append(f"- {tn}: {tp:,} đ")
                                total_calc += tp
                            if doc_note.strip():
                                sel_lines.append(f"- Ghi chú: {doc_note.strip()}")
                            final_ind_str = "\n".join(sel_lines)

                            with db_cursor() as cur_save_ind:
                                cur_save_ind.execute("""
                                    INSERT INTO medlatec_registrations (user_id, patient_name, patient_phone, package, final_price, sample_date, location_type, address, doctor_indications, indications_extra_price)
                                    VALUES (%s, %s, %s, 'Chỉ định riêng từ Bác sĩ', 0, %s, 'Tận nhà', 'Theo hồ sơ', %s, %s)
                                """, (uid, prof['full_name'], prof.get('phone', ''), str(datetime.date.today()), final_ind_str, total_calc))
                            st.toast("Đã gửi chỉ định xét nghiệm lên Render!")
                            st.rerun()

    with tab_history_admin:
        st.subheader("📋 Quản Lý Lịch Sử Khám Toàn Hệ Thống (Render)")

        all_sessions = []
        try:
            with db_cursor() as cur_all_s:
                cur_all_s.execute("""
                    SELECT cs.session_id, cs.user_id, cs.initial_symptoms, 
                           cs.followup_answers, cs.final_assessment, cs.top_icd10, 
                           cs.created_at, COALESCE(u.full_name, 'Không xác định')
                    FROM consultation_sessions cs
                    LEFT JOIN app_users u ON cs.user_id = u.user_id
                    ORDER BY cs.session_id DESC
                """)
                all_sessions = cur_all_s.fetchall()
        except Exception as e:
            st.error(f"Lỗi truy vấn lịch sử khám: {e}")
            all_sessions = []

        if not all_sessions:
            st.info("💡 Hệ thống hiện chưa ghi nhận ca khám bệnh nào.")
        else:
            st.markdown("##### 🔍 Lọc & Quản lý lịch sử theo người bệnh")

            unique_patients = {}
            for s in all_sessions:
                s_uid = s[1]
                s_pname = s[7]
                if s_uid not in unique_patients:
                    unique_patients[s_uid] = f"ID #{s_uid}: {s_pname}"

            col_filter_p, col_btn_group_del = st.columns([2, 1.2])
            with col_filter_p:
                filter_patient_options = ["Tất cả người bệnh"] + [f"{name}" for uid, name in unique_patients.items()]
                selected_patient_filter = st.selectbox(
                    "Chọn người bệnh cần tra cứu hoặc xóa lịch sử:",
                    options=filter_patient_options,
                    label_visibility="collapsed"
                )

            if selected_patient_filter != "Tất cả người bệnh":
                selected_uid = int(selected_patient_filter.split(":")[0].replace("ID #", ""))
                filtered_sessions = [s for s in all_sessions if s[1] == selected_uid]
            else:
                selected_uid = None
                filtered_sessions = all_sessions

            with col_btn_group_del:
                if selected_uid is not None:
                    if st.button(f"🗑️ Xóa hết lịch sử của ID #{selected_uid}", type="primary", width="stretch"):
                        with db_cursor() as cur_del_grp:
                            cur_del_grp.execute("DELETE FROM consultation_sessions WHERE user_id = %s", (selected_uid,))
                        st.toast(f"✅ Đã xóa toàn bộ ca khám của bệnh nhân ID #{selected_uid}!")
                        st.rerun()
                else:
                    if st.button("⚠️ Reset Sạch Toàn Bộ Lịch Sử", type="primary", width="stretch"):
                        with db_cursor() as cur_res:
                            cur_res.execute("DELETE FROM consultation_sessions")
                        st.toast("Đã Reset sạch toàn bộ lịch sử trên hệ thống!")
                        st.rerun()

            st.caption(f"Hiển thị **{len(filtered_sessions)}** ca khám:")

            for s in filtered_sessions:
                s_id, s_uid, s_sym, s_fol, s_fin, s_icd, s_date, p_name = s
                with st.expander(f"🩺 Ca #{s_id} | Bệnh nhân: **{p_name}** (ID #{s_uid}) | ICD: `{s_icd}` - {str(s_date)[:19]}"):
                    st.write(f"- **Triệu chứng tiếp nhận:** {s_sym}")
                    if s_fol:
                        st.write(f"- **Dữ liệu sàng lọc:** {s_fol}")
                    if s_fin:
                        st.markdown(f"**Kết luận & Phác đồ điều trị:**\n\n{s_fin}")

                    if st.button(f"🗑️ Xóa ca #{s_id}", key=f"del_sess_admin_{s_id}"):
                        with db_cursor() as cur_del_s:
                            cur_del_s.execute("DELETE FROM consultation_sessions WHERE session_id = %s", (s_id,))
                        st.toast(f"Đã xóa ca #{s_id} trên Render!")
                        st.rerun()


# ==============================================================================
# 2 NÚT NỔI CỐ ĐỊNH GÓC DƯỚI BÊN PHẢI: MESSENGER + ZALO
# ==============================================================================
st.markdown("""
<style>
div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) {
    position: fixed !important;
    right: 24px !important;
    bottom: 125px !important;
    z-index: 999999 !important;
    width: 66px !important;
    height: 66px !important;
    margin: 0 !important;
    padding: 0 !important;
}

div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) {
    position: fixed !important;
    right: 24px !important;
    bottom: 38px !important;
    z-index: 999999 !important;
    width: 66px !important;
    height: 66px !important;
    margin: 0 !important;
    padding: 0 !important;
}

div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) a,
div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) a {
    position: relative !important;
    width: 62px !important;
    height: 62px !important;
    min-width: 62px !important;
    min-height: 62px !important;
    max-width: 62px !important;
    max-height: 62px !important;
    padding: 0 !important;
    margin: 0 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    border-radius: 50% !important;
    border: 2px solid #FFFFFF !important;
    box-sizing: border-box !important;
    text-decoration: none !important;
    overflow: visible !important;
    box-shadow: 0 5px 18px rgba(0,0,0,0.28), 0 2px 6px rgba(0,0,0,0.15) !important;
    transition: transform 0.2s ease, box-shadow 0.2s ease !important;
}

div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) a {
    background: linear-gradient(135deg, #00B2FF 0%, #006AFF 100%) !important;
    color: #FFFFFF !important;
}

div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) a {
    background: linear-gradient(135deg, #008FE5 0%, #0068D9 100%) !important;
    color: #FFFFFF !important;
}

div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) a p,
div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) a p {
    font-size: 0 !important;
    line-height: 0 !important;
    width: 0 !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}

div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) a::before {
    content: "🎧";
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -53%);
    font-size: 28px !important;
    line-height: 1 !important;
    color: #FFFFFF !important;
    z-index: 5;
    pointer-events: none;
}

div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) a::before {
    content: "Z";
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -54%);
    width: 34px;
    height: 27px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #FFFFFF !important;
    font-family: Arial, sans-serif;
    font-size: 25px !important;
    font-weight: 900 !important;
    line-height: 1 !important;
    border: 3px solid #FFFFFF;
    border-radius: 7px;
    z-index: 5;
    pointer-events: none;
}

div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) a::after {
    content: "messenger";
    position: absolute;
    left: 50%;
    bottom: -10px;
    transform: translateX(-50%);
    height: 20px;
    min-width: 48px;
    padding: 0 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #0284C7;
    color: #FFFFFF !important;
    font-size: 11px !important;
    font-weight: 800 !important;
    line-height: 20px !important;
    white-space: nowrap;
    border-radius: 12px;
    border: 1.5px solid #FFFFFF;
    box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    z-index: 20;
    pointer-events: none;
}

div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) a::after {
    content: "ZALO";
    position: absolute;
    left: 50%;
    bottom: -10px;
    transform: translateX(-50%);
    height: 20px;
    min-width: 36px;
    padding: 0 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #0068D9;
    color: #FFFFFF !important;
    font-size: 11px !important;
    font-weight: 800 !important;
    line-height: 20px !important;
    white-space: nowrap;
    border-radius: 12px;
    border: 1.5px solid #FFFFFF;
    box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    z-index: 20;
    pointer-events: none;
}

div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) a:hover,
div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) a:hover {
    transform: scale(1.10) !important;
    box-shadow: 0 8px 24px rgba(0,0,0,0.34), 0 3px 8px rgba(0,0,0,0.18) !important;
}

@media (max-width: 768px) {
    div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) {
        right: 16px !important;
        bottom: 110px !important;
    }
    div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) {
        right: 16px !important;
        bottom: 28px !important;
    }
    div[data-testid="stLinkButton"]:has(a[href="https://m.me/toanbvtimhn"]) a,
    div[data-testid="stLinkButton"]:has(a[href="https://zalo.me/0973173759"]) a {
        width: 58px !important;
        height: 58px !important;
        min-width: 58px !important;
        min-height: 58px !important;
        max-width: 58px !important;
        max-height: 58px !important;
    }
}
</style>
""", unsafe_allow_html=True)

st.link_button("messenger", "https://m.me/toanbvtimhn", type="secondary")
st.link_button("Zalo", "https://zalo.me/0973173759", type="secondary")

st.markdown("<div class='slogan-footer'>🌟 TẤT CẢ VÌ SỨC KHỎE CỘNG ĐỒNG 🌟</div>", unsafe_allow_html=True)
