import http.server
import socketserver
import json
import os
import time
import hashlib
import secrets
import urllib.parse
import urllib.request
import urllib.error
from http import cookies
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from fpdf import FPDF

# Load environment variables
load_dotenv()

PORT = int(os.getenv("PORT", 8000))
DATA_DIR = "data"

# Configuration from .env
PG_DB = os.getenv("PG_DB", "coutrallam_db")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "sasi24")
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")

# Supabase Storage configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

def upload_to_supabase(bucket, file_bytes, filename, content_type="application/octet-stream"):
    """Upload file bytes directly to Supabase Storage and return the permanent public URL."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise Exception("Supabase Storage not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{filename}"
    req = urllib.request.Request(
        upload_url,
        data=file_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": content_type,
            "x-upsert": "true"
        }
    )
    try:
        with urllib.request.urlopen(req) as response:
            pass
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        raise Exception(f"Supabase upload failed ({e.code}): {err_body}")
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{filename}"
    return public_url

def delete_from_supabase(bucket, file_url):
    """Delete a file from Supabase Storage given its public URL. Non-fatal on failure."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not file_url:
        return
    try:
        prefix = f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/"
        if not file_url.startswith(prefix):
            return
        path = file_url[len(prefix):]
        delete_url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
        req = urllib.request.Request(
            delete_url,
            method="DELETE",
            headers={"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        )
        with urllib.request.urlopen(req):
            pass
    except Exception as e:
        print(f"[Supabase] Delete failed (non-critical): {e}")

# Auto-redirect direct Supabase host to IPv4 pooler to prevent IPv6 unreachable error on Render
if PG_HOST == "db.gywikppuosljysbomblu.supabase.co":
    PG_HOST = "aws-1-ap-south-1.pooler.supabase.com"
    PG_PORT = "6543"
    PG_USER = "postgres.gywikppuosljysbomblu"

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "Courtallam Holidays by Tourver <no-reply@courtallamholidays.com>")
MOCK_EMAIL = os.getenv("MOCK_EMAIL", "True").lower() == "true"

# In-memory session store (session_token -> username)
sessions = {}

# IP-based brute-force rate limiting: { ip -> {count, blocked_until} }
ip_rate_limit = {}
IP_MAX_ATTEMPTS = 5       # max failed logins per IP
IP_BLOCK_MINUTES = 30     # how long to block the IP

def check_ip_blocked(ip):
    """Returns (is_blocked, seconds_remaining)."""
    entry = ip_rate_limit.get(ip)
    if not entry:
        return False, 0
    blocked_until = entry.get('blocked_until')
    if blocked_until and datetime.now() < blocked_until:
        remaining = int((blocked_until - datetime.now()).total_seconds())
        return True, remaining
    return False, 0

def record_ip_failure(ip):
    """Increment failure counter for an IP; block if threshold exceeded."""
    entry = ip_rate_limit.setdefault(ip, {'count': 0, 'blocked_until': None})
    # Reset counter if previous block has expired
    if entry.get('blocked_until') and datetime.now() >= entry['blocked_until']:
        entry['count'] = 0
        entry['blocked_until'] = None
    entry['count'] += 1
    if entry['count'] >= IP_MAX_ATTEMPTS:
        entry['blocked_until'] = datetime.now() + timedelta(minutes=IP_BLOCK_MINUTES)
        print(f"[SECURITY] IP {ip} blocked for {IP_BLOCK_MINUTES} minutes after {entry['count']} failed attempts.")

def reset_ip_counter(ip):
    """Clear the failure counter for an IP after a successful login."""
    ip_rate_limit.pop(ip, None)

def generate_invoice_pdf(booking):
    import tempfile
    pdf_filename = f"invoice_{booking['unique_booking_id']}.pdf"
    pdf_path = os.path.join(tempfile.gettempdir(), pdf_filename)
    
    check_in_time = None
    check_out_time = None
    if booking.get('item_type', '').lower() in ['room', 'rooms']:
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT check_in_time, check_out_time FROM rooms WHERE id = %s", (booking['item_id'],))
            room_row = c.fetchone()
            if room_row:
                check_in_time = room_row['check_in_time']
                check_out_time = room_row['check_out_time']
            conn.close()
        except Exception as e:
            print("Error retrieving room check times:", e)
    
    # Retrieve template settings from db
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM settings")
        rows = c.fetchall()
        settings = {row['key']: row['value'] for row in rows}
        conn.close()
    except Exception:
        settings = {}
        
    company_name = settings.get('invoice_company_name', 'COURTALLAM HOLIDAYS').upper()
    company_address = settings.get('invoice_company_address', '123, Main Road, Courtallam, Tamil Nadu - 627802')
    company_gstin = settings.get('invoice_company_gstin', '33AAAAA0000A1Z1')
    accent_hex = settings.get('invoice_color_accent', '#22705d')
    terms_text = settings.get('invoice_terms', '1. All bookings are subject to availability.\n2. Please carry a valid Govt photo ID card during check-in.\n3. 100% advance payment required to confirm room bookings.')
    footer_text = settings.get('invoice_footer', 'Thank you for choosing Courtallam Holidays. Have a pleasant trip!')
    show_watermark = settings.get('invoice_watermark', 'false').lower() == 'true'
    watermark_val = settings.get('invoice_watermark_text', 'DUPLICATE COPY')
    
    def hex_to_rgb(hex_str):
        hex_str = hex_str.lstrip('#')
        if len(hex_str) == 3:
            hex_str = ''.join([c*2 for c in hex_str])
        try:
            return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
        except ValueError:
            return (34, 112, 93) # fallback default
            
    accent_rgb = hex_to_rgb(accent_hex)
    
    pdf = FPDF()
    pdf.add_page()
    
    # Watermark text (drawn diagonally in background)
    if show_watermark or booking.get('is_duplicate'):
        txt = "DUPLICATE COPY" if booking.get('is_duplicate') else watermark_val
        pdf.set_font("Helvetica", "B", 42)
        pdf.set_text_color(240, 205, 205) # soft red-grey
        # Draw watermarks diagonally
        pdf.text(35, 120, txt)
        pdf.text(35, 200, txt)
        
    # Title Banner
    logo_url = settings.get('invoice_logo_url')
    left_offset = 10
    if logo_url:
        try:
            import tempfile, urllib.request as _req
            logo_filename = logo_url.split('/')[-1]
            logo_tmp_path = os.path.join(tempfile.gettempdir(), logo_filename)
            _req.urlretrieve(logo_url, logo_tmp_path)
            pdf.image(logo_tmp_path, x=10, y=10, h=22)
            from PIL import Image
            with Image.open(logo_tmp_path) as img:
                orig_w, orig_h = img.size
                scaled_w = (orig_w / orig_h) * 22
                left_offset = 10 + scaled_w + 5
        except Exception as e:
            print("Logo Error PIL/FPDF:", e)

    pdf.set_x(left_offset)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(accent_rgb[0], accent_rgb[1], accent_rgb[2])
    pdf.cell(120 - (left_offset - 10), 8, company_name, ln=False)

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(70, 8, "INVOICE / RECEIPT", ln=True, align="R")

    # Brand Accent Line
    pdf.set_x(left_offset)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(245, 166, 35) # Sunset orange/yellow (#f5a623)
    pdf.cell(120 - (left_offset - 10), 5, "BY TOURVER", ln=False)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(70, 5, f"Date: {datetime.now().strftime('%Y-%m-%d')}", ln=True, align="R")

    # Row 3: Invoice No on right, nothing on left
    pdf.set_x(left_offset)
    pdf.cell(120 - (left_offset - 10), 5, "", ln=False)
    invoice_num = booking.get('invoice_number') or f"INV-{booking['unique_booking_id'] or booking['id']}"
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(70, 5, f"Invoice No: {invoice_num}", ln=True, align="R")
    
    if pdf.get_y() < 30:
        pdf.set_y(30)
    
    pdf.ln(8)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    # Main grid - Two columns: Billing Details & Booking Reference
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(95, 7, "BOOKING REFERENCE", ln=False)
    pdf.cell(95, 7, "CUSTOMER DETAILS", ln=True)
    
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    
    # Row 1
    pdf.cell(95, 6, f"Booking ID: {booking['unique_booking_id'] or 'N/A'}", ln=False)
    pdf.cell(95, 6, f"Name: {booking['customer_name']}", ln=True)
    
    # Row 2
    check_out_str = f" to {booking['check_out']}" if booking['check_out'] else ""
    pdf.cell(95, 6, f"Item: {booking['item_type'].title()} ({booking['item_id']})", ln=False)
    pdf.cell(95, 6, f"Phone: {booking['customer_phone']}", ln=True)
    
    # Row 3
    pdf.cell(95, 6, f"Dates: {booking['check_in']}{check_out_str}", ln=False)
    pdf.cell(95, 6, f"Email: {booking['customer_email'] or 'N/A'}", ln=True)
    
    # Row 4 (Timing)
    if check_in_time or check_out_time:
        time_parts = []
        if check_in_time:
            time_parts.append(f"In: {check_in_time}")
        if check_out_time:
            time_parts.append(f"Out: {check_out_time}")
        pdf.cell(95, 6, f"Timing: {', '.join(time_parts)}", ln=False)
        pdf.cell(95, 6, "", ln=True)
    
    # Row 5
    pdf.cell(95, 6, f"Guests: {booking['guests'] or 'N/A'}", ln=False)
    pdf.cell(95, 6, "", ln=True)
    
    pdf.ln(5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    # Provider Details
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(190, 7, "SERVICE PROVIDER DETAILS", ln=True)
    
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(95, 6, f"Provider Name: {booking['provider_name'] or 'N/A'}", ln=False)
    pdf.cell(95, 6, f"Provider Phone: {booking['provider_phone'] or 'N/A'}", ln=True)
    
    pdf.ln(6)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    # Total Box table
    pdf.set_fill_color(245, 245, 245)
    pdf.rect(10, pdf.get_y(), 190, 24, "F")
    
    pdf.set_y(pdf.get_y() + 3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(80, 80, 80)
    
    pdf.cell(10, 6, "", ln=False)
    pdf.cell(50, 6, "TOTAL AMOUNT", ln=False, align="C")
    pdf.cell(50, 6, "ADVANCE PAID", ln=False, align="C")
    pdf.cell(70, 6, "BALANCE PENDING", ln=True, align="C")
    
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(accent_rgb[0], accent_rgb[1], accent_rgb[2])
    pdf.cell(10, 8, "", ln=False)
    pdf.cell(50, 8, f"Rs. {booking['amount']:,}", ln=False, align="C")
    pdf.cell(50, 8, f"Rs. {booking.get('advance_amount', 0):,}", ln=False, align="C")
    
    if booking.get('balance_amount', 0) > 0:
        pdf.set_text_color(200, 50, 50) # Highlight outstanding balance in red
    pdf.cell(70, 8, f"Rs. {booking.get('balance_amount', 0):,}", ln=True, align="C")
    
    pdf.ln(10)
    
    # Terms
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(190, 5, "Terms & Conditions", ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(130, 130, 130)
    pdf.multi_cell(190, 4, terms_text)
    
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(190, 5, footer_text, ln=True, align="C")
    
    pdf.output(pdf_path)
    try:
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        public_url = upload_to_supabase("gallery", pdf_bytes, f"invoices/{pdf_filename}", "application/pdf")
        return public_url
    except Exception as e:
        print("Supabase PDF Upload Error:", e)
    return pdf_path

def send_invoice_email(booking, pdf_path):
    if not booking['customer_email']:
        print("No customer email provided, skipping email.")
        return
        
    subject = f"Booking Confirmation & Invoice - {booking['unique_booking_id']}"
    body_text = f"""Dear {booking['customer_name']},
    
Thank you for booking with Courtallam Holidays!
Your booking for {booking['item_type'].title()} has been confirmed.

Booking ID: {booking['unique_booking_id']}
Dates: {booking['check_in']} {f"to {booking['check_out']}" if booking['check_out'] else ""}
Amount Paid: Rs. {booking['amount']}

Please find your attached invoice PDF.

Warm regards,
Courtallam Holidays Team
"""

    body_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <h2 style="color: #22705d;">Booking Confirmed!</h2>
        <p>Dear <strong>{booking['customer_name']}</strong>,</p>
        <p>Thank you for choosing Courtallam Holidays. We are pleased to confirm your booking.</p>
        
        <table style="border-collapse: collapse; width: 100%; max-width: 500px; margin: 20px 0;">
          <tr style="background-color: #f2f2f2;">
            <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Booking ID</th>
            <td style="padding: 10px; border: 1px solid #ddd;">{booking['unique_booking_id']}</td>
          </tr>
          <tr>
            <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Service</th>
            <td style="padding: 10px; border: 1px solid #ddd;">{booking['item_type'].title()}</td>
          </tr>
          <tr style="background-color: #f2f2f2;">
            <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Dates</th>
            <td style="padding: 10px; border: 1px solid #ddd;">{booking['check_in']}{f" to {booking['check_out']}" if booking['check_out'] else ""}</td>
          </tr>
          <tr>
            <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Amount Paid</th>
            <td style="padding: 10px; border: 1px solid #ddd;">Rs. {booking['amount']}</td>
          </tr>
        </table>
        
        <p>Your invoice/receipt is attached to this email.</p>
        <p>Warm regards,<br><strong>Courtallam Holidays Team</strong></p>
      </body>
    </html>
    """

    if MOCK_EMAIL:
        os.makedirs(DATA_DIR, exist_ok=True)
        mock_file_path = os.path.join(DATA_DIR, f"mock_invoice_email_{booking['unique_booking_id']}.txt")
        with open(mock_file_path, "w", encoding="utf-8") as f:
            f.write(f"To: {booking['customer_email']}\n")
            f.write(f"Subject: {subject}\n")
            f.write(f"Attachment: {pdf_path}\n\n")
            f.write(body_text)
        print(f"Mock email invoice saved to {mock_file_path}")
        return True

    # Real SMTP send
    try:
        from email.mime.application import MIMEApplication
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = EMAIL_FROM
        msg['To'] = booking['customer_email']
        
        msg.attach(MIMEText(body_text, 'plain'))
        msg.attach(MIMEText(body_html, 'html'))
        
        # Attach PDF
        with open(pdf_path, 'rb') as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(pdf_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(pdf_path)}"'
        msg.attach(part)
        
        # Connect & send
        context = ssl.create_default_context()
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(EMAIL_FROM, booking['customer_email'], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(EMAIL_FROM, booking['customer_email'], msg.as_string())
        print(f"Invoice email sent successfully to {booking['customer_email']}")
        return True
    except Exception as e:
        print(f"Failed to send real SMTP invoice: {e}")
        return False

def send_invoice_whatsapp(booking, pdf_url):
    phone = booking['customer_phone']
    cleaned_phone = "".join(filter(str.isdigit, phone))
    
    unique_id = booking['unique_booking_id']
    msg_text = f"Hi {booking['customer_name']}! Your booking is confirmed. Booking ID: {unique_id}. Click here to download your invoice: http://localhost:{PORT}{pdf_url}"
    
    n8n_url = os.getenv("N8N_WEBHOOK_URL")
    evo_url = os.getenv("EVOLUTION_API_URL")
    
    payload = {
        "phone": cleaned_phone,
        "message": msg_text,
        "pdf_url": f"http://localhost:{PORT}{pdf_url}",
        "booking_id": unique_id,
        "customer_name": booking['customer_name']
    }

    sent = False
    
    if n8n_url:
        try:
            import urllib.request
            req = urllib.request.Request(
                n8n_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status in [200, 201]:
                    print("WhatsApp notification sent via n8n successfully.")
                    sent = True
        except Exception as e:
            print(f"Failed to trigger n8n WhatsApp webhook: {e}")

    if evo_url and not sent:
        evo_key = os.getenv("EVOLUTION_API_KEY", "")
        instance = os.getenv("EVOLUTION_INSTANCE", "")
        try:
            import urllib.request
            media_endpoint = f"{evo_url}/message/sendMedia/{instance}"
            evo_payload = {
                "number": cleaned_phone,
                "media": f"http://localhost:{PORT}{pdf_url}",
                "mediaType": "document",
                "fileName": f"invoice_{unique_id}.pdf",
                "caption": msg_text
            }
            req = urllib.request.Request(
                media_endpoint,
                data=json.dumps(evo_payload).encode('utf-8'),
                headers={
                    'Content-Type': 'application/json',
                    'apikey': evo_key
                }
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status in [200, 201]:
                    print("WhatsApp notification sent via Evolution API successfully.")
                    sent = True
        except Exception as e:
            print(f"Failed to trigger Evolution API: {e}")

    # Fallback/mock log file creation
    os.makedirs(DATA_DIR, exist_ok=True)
    mock_whatsapp_path = os.path.join(DATA_DIR, f"mock_whatsapp_message_{unique_id}.txt")
    with open(mock_whatsapp_path, "w", encoding="utf-8") as f:
        f.write(f"To (Phone): {cleaned_phone}\n")
        f.write(f"Message: {msg_text}\n")
        f.write(f"Attachment URL: http://localhost:{PORT}{pdf_url}\n")
        f.write(f"n8n Triggered: {'Yes' if n8n_url else 'No'}\n")
        f.write(f"Evolution API Triggered: {'Yes' if evo_url else 'No'}\n")
    print(f"Mock WhatsApp message saved to {mock_whatsapp_path}")
    return True


def get_db_connection():
    conn = psycopg2.connect(
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        host=PG_HOST,
        port=PG_PORT,
        cursor_factory=psycopg2.extras.DictCursor
    )
    return conn

def init_db():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        email TEXT PRIMARY KEY,
                        password_hash TEXT
                     )''')
        for col, col_type in [('role', "TEXT DEFAULT 'admin'"), ('name', "TEXT DEFAULT 'Admin'"), 
                              ('failed_login_attempts', "INTEGER DEFAULT 0"), ('locked_until', "TIMESTAMP DEFAULT NULL")]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type}")
            except Exception:
                conn.rollback()

        c.execute('''CREATE TABLE IF NOT EXISTS admin_invites (
                        token TEXT PRIMARY KEY,
                        role TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP,
                        is_used INTEGER DEFAULT 0
                     )''')

        c.execute('''CREATE TABLE IF NOT EXISTS login_history (
                        id SERIAL PRIMARY KEY,
                        email TEXT,
                        ip_address TEXT,
                        status TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''')
        for col, col_type in [('user_agent', "TEXT"), ('session_token', "TEXT")]:
            try:
                c.execute(f"ALTER TABLE login_history ADD COLUMN IF NOT EXISTS {col} {col_type}")
            except Exception:
                conn.rollback()

        c.execute('''CREATE TABLE IF NOT EXISTS packages (
                        id TEXT PRIMARY KEY,
                        title TEXT,
                        price INTEGER,
                        duration TEXT,
                        category TEXT,
                        image TEXT,
                        description TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS rooms (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        price INTEGER,
                        category TEXT,
                        image TEXT,
                        amenities TEXT,
                        description TEXT,
                        available INTEGER,
                        capacity TEXT
                     )''')

        c.execute('''CREATE TABLE IF NOT EXISTS transport (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        price INTEGER,
                        category TEXT,
                        image TEXT,
                        description TEXT,
                        capacity TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS inquiries (
                        id SERIAL PRIMARY KEY,
                        name TEXT,
                        email TEXT,
                        subject TEXT,
                        message TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS bookings (
                        id SERIAL PRIMARY KEY,
                        customer_name TEXT,
                        customer_email TEXT,
                        customer_phone TEXT,
                        item_type TEXT,
                        item_id TEXT,
                        check_in DATE,
                        guests INTEGER,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS banners (
                        id SERIAL PRIMARY KEY,
                        image_url TEXT,
                        display_order INTEGER DEFAULT 0,
                        is_active INTEGER DEFAULT 1
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS gallery (
                        id SERIAL PRIMARY KEY,
                        image_url TEXT,
                        caption TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS glimpses (
                        id SERIAL PRIMARY KEY,
                        title TEXT,
                        description TEXT,
                        image_url TEXT,
                        display_order INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS page_views (
                        id SERIAL PRIMARY KEY,
                        page TEXT NOT NULL,
                        viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''')
        
        # Ensure existing tables have the new column
        try:
            c.execute("ALTER TABLE rooms ADD COLUMN IF NOT EXISTS capacity TEXT")
            c.execute("ALTER TABLE rooms ADD COLUMN IF NOT EXISTS check_in_time TEXT DEFAULT '12:00 PM'")
            c.execute("ALTER TABLE rooms ADD COLUMN IF NOT EXISTS check_out_time TEXT DEFAULT '11:00 AM'")
        except Exception:
            conn.rollback()

        try:
            c.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS check_out DATE")
            c.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS message TEXT")
        except Exception:
            conn.rollback()

        for col, col_type in [
            ('amount', 'INTEGER'),
            ('provider_name', 'TEXT'),
            ('provider_upi', 'TEXT'),
            ('provider_phone', 'TEXT'),
            ('provider_qr_url', 'TEXT'),
            ('payment_notes', 'TEXT'),
            ('booking_documents', 'TEXT'),
            ('unique_booking_id', 'TEXT'),
            ('invoice_pdf_url', 'TEXT'),
            ('advance_amount', 'INTEGER DEFAULT 0'),
            ('balance_amount', 'INTEGER DEFAULT 0'),
            ('invoice_number', 'TEXT'),
            ('is_duplicate', 'BOOLEAN DEFAULT FALSE')
        ]:
            try:
                c.execute(f"ALTER TABLE bookings ADD COLUMN IF NOT EXISTS {col} {col_type}")
            except Exception:
                conn.rollback()
        
        for key, val in [
            ('invoice_company_name', 'Courtallam Holidays'),
            ('invoice_company_address', '123, Main Road, Courtallam, Tamil Nadu - 627802'),
            ('invoice_company_gstin', '33AAAAA0000A1Z1'),
            ('invoice_color_accent', '#22705d'),
            ('invoice_terms', '1. All bookings are subject to availability.\n2. Please carry a valid Govt photo ID card during check-in.\n3. 100% advance payment required to confirm room bookings.'),
            ('invoice_footer', 'Thank you for choosing Courtallam Holidays. Have a pleasant trip!'),
            ('invoice_watermark', 'false'),
            ('invoice_watermark_text', 'DUPLICATE COPY'),
            ('contact_phone', '+91 73392 84010'),
            ('contact_email', 'bookings@courtallamseasons.com'),
            ('whatsapp_number', '917339284010'),
            ('website_logo_url', 'images/logo.png')
        ]:
            try:
                c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (key, val))
            except Exception:
                conn.rollback()
        
        
        for table in ['packages', 'rooms', 'transport']:
            for col in ['weekday_price', 'weekday_original_price', 'weekend_price', 'weekend_original_price']:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} INTEGER")
                except Exception:
                    conn.rollback()
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS images TEXT")
                c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_offer INTEGER DEFAULT 0")
                if table == 'packages':
                    c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_popular INTEGER DEFAULT 0")
                    c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS display_order INTEGER DEFAULT 0")
                    c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS inclusions TEXT")
                    c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS itinerary TEXT")
            except Exception:
                conn.rollback()
        
        # Specific package columns
        try:
            c.execute("ALTER TABLE packages ADD COLUMN IF NOT EXISTS is_popular INTEGER DEFAULT 0")
            c.execute("ALTER TABLE packages ADD COLUMN IF NOT EXISTS display_order INTEGER DEFAULT 0")
            c.execute("ALTER TABLE packages ADD COLUMN IF NOT EXISTS inclusions TEXT")
            c.execute("ALTER TABLE packages ADD COLUMN IF NOT EXISTS itinerary TEXT")
        except Exception:
            conn.rollback()

        try:
            c.execute("ALTER TABLE packages ADD COLUMN IF NOT EXISTS is_popular INTEGER DEFAULT 0")
            c.execute("ALTER TABLE packages ADD COLUMN IF NOT EXISTS display_order INTEGER DEFAULT 0")
        except Exception:
            conn.rollback()
        
        # Master admin - password from env var (never hardcode in source)
        master_password = os.getenv('MASTER_ADMIN_PASSWORD', '')
        if not master_password:
            print("WARNING: MASTER_ADMIN_PASSWORD is not set in .env - master account will not be auto-created.")
        else:
            c.execute("""
                INSERT INTO users (email, password_hash, role, name) 
                VALUES (%s, %s, %s, %s) 
                ON CONFLICT (email) DO UPDATE SET 
                    password_hash = EXCLUDED.password_hash,
                    role = EXCLUDED.role,
                    name = EXCLUDED.name
            """, ("admin@courtallamholidays.com", hash_password(master_password), "super_admin", "Master Admin"))
            print("Verified master account from MASTER_ADMIN_PASSWORD env var.")
                
        conn.commit()
        conn.close()
        print(f"Successfully connected to PostgreSQL database: {PG_DB}")
    except Exception as e:
        print(f"DATABASE INITIALIZATION FAILED: {e}")
        print(f"Please ensure PostgreSQL is running and the database '{PG_DB}' exists.")

def hash_password(password, salt=None):
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return f"{salt}${hashed}"

def verify_password(password, stored_hash):
    if '$' not in stored_hash: return False
    salt, hashed = stored_hash.split('$', 1)
    return hash_password(password, salt) == stored_hash

def log_login_attempt(email, status, ip_address, user_agent=None, session_token=None):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO login_history (email, ip_address, status, user_agent, session_token) VALUES (%s, %s, %s, %s, %s)", 
                  (email, ip_address, status, user_agent, session_token))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Failed to write to DB:", e)
    
    # Also log to file
    log_file = os.path.join(DATA_DIR, "login_history.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] IP: {ip_address} | Email: {email} | Status: {status} | Agent: {user_agent} | Token: {session_token}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_entry)



class AdminHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def get_session_token(self):
        cookie_header = self.headers.get('Cookie')
        if not cookie_header:
            return None
        
        # Simple manual parsing as fallback to SimpleCookie
        try:
            cookies_list = cookie_header.split(';')
            for c in cookies_list:
                if '=' in c:
                    name, value = c.strip().split('=', 1)
                    if name == 'session_token':
                        return value
        except:
            pass
            
        try:
            C = cookies.SimpleCookie(cookie_header)
            if "session_token" in C:
                return C["session_token"].value
        except:
            pass
        return None

    def is_authenticated(self):
        token = self.get_session_token()
        if not token or token not in sessions:
            return False
            
        session_data = sessions[token]
        if isinstance(session_data, dict):
            # 15 minutes inactivity check
            remember = session_data.get('remember_device', False)
            last_act = session_data.get('last_activity', 0)
            if not remember and (time.time() - last_act > 900):
                print(f"Session auto-logged out due to 15-minute inactivity: {session_data.get('email')}")
                del sessions[token]
                return False
            
            session_data['last_activity'] = time.time()
            return True
        return True

    def do_redirect(self, target_url):
        self.send_response(302)
        self.send_header('Location', target_url)
        self.end_headers()

    def _send_security_headers(self):
        """Attach security headers to every response."""
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-XSS-Protection', '1; mode=block')
        self.send_header('Referrer-Policy', 'no-referrer')

    PUBLIC_PAGES = {'/', '/index.html', '/packages.html', '/rooms.html', '/transport.html', '/about.html', '/contact.html', '/package-detail.html', '/room-detail.html'}

    def _record_page_view(self, page):
        """Increment view count for a public page (non-blocking)."""
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO page_views (page) VALUES (%s)", (page,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"View count error: {e}")

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)

        # Track public page views (skip bots/admin/API/assets)
        if parsed_path.path in self.PUBLIC_PAGES:
            self._record_page_view(parsed_path.path)

        # Protected HTML pages - Hide existence by returning 404 if not authenticated
        if parsed_path.path == '/admin.html':
            if not self.is_authenticated():
                self.send_error(404, "File Not Found")
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            with open('admin.html', 'rb') as f:
                self.wfile.write(f.read())
            return
            
        # Hide standard login/signup paths to prevent discovery
        if parsed_path.path in ['/login.html', '/signup.html']:
            self.send_error(404, "File Not Found")
            return

        # Serve the secret access portal
        if parsed_path.path == '/tourver-admin-access':
            if self.is_authenticated():
                self.do_redirect('/admin.html')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            with open('tourver-admin-access.html', 'rb') as f:
                self.wfile.write(f.read())
            return

        # Serve the secret signup portal (invite registration)
        if parsed_path.path == '/tourver-admin-signup':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            with open('tourver-admin-signup.html', 'rb') as f:
                self.wfile.write(f.read())
            return

        # /uploads/ paths are no longer used - images are served from Supabase CDN

        if parsed_path.path == '/api/logout':
            token = self.get_session_token()
            if token and token in sessions:
                del sessions[token]
            self.send_response(200)
            self.send_header('Set-Cookie', 'session_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/')
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode())
            return

        if parsed_path.path == '/api/view-count':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM page_views")
                total = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM page_views WHERE viewed_at >= CURRENT_DATE")
                today = c.fetchone()[0]
                c.execute("SELECT page, COUNT(*) as cnt FROM page_views GROUP BY page ORDER BY cnt DESC")
                by_page = [{"page": r[0], "count": r[1]} for r in c.fetchall()]
                conn.close()
                self.wfile.write(json.dumps({"total": total, "today": today, "by_page": by_page}).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"total": 0, "today": 0, "by_page": []}).encode())
            return

        if parsed_path.path in ['/api/inquiries', '/api/bookings']:
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            
            table = parsed_path.path.split('/')[-1]
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute(f"SELECT * FROM {table} ORDER BY created_at DESC")
                rows = c.fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    if 'created_at' in item and item['created_at']:
                        item['created_at'] = item['created_at'].isoformat()
                    if 'check_in' in item and item['check_in']:
                        item['check_in'] = item['check_in'].isoformat()
                    if 'check_out' in item and item['check_out']:
                        item['check_out'] = item['check_out'].isoformat()
                    items.append(item)
                conn.close()
                self.wfile.write(json.dumps(items).encode('utf-8'))
            except Exception as e:
                self.wfile.write(b"[]")
                print("DB Error:", e)
            return
        if parsed_path.path == '/api/check-availability':
            query = urllib.parse.parse_qs(parsed_path.query)
            item_id = query.get('item_id', [''])[0]
            checkin = query.get('checkin', [''])[0]
            checkout = query.get('checkout', [''])[0]
            
            if not (item_id and checkin and checkout):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "Missing parameters"}')
                return
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            
            try:
                conn = get_db_connection()
                c = conn.cursor()
                # A booking overlaps if it starts before requested checkout AND ends after requested checkin
                c.execute("""
                    SELECT COUNT(*) FROM bookings 
                    WHERE item_type = 'room' AND item_id = %s 
                    AND status != 'Cancelled' 
                    AND check_in < %s AND check_out > %s
                """, (item_id, checkout, checkin))
                overlap_count = c.fetchone()[0]
                conn.close()
                self.wfile.write(json.dumps({"available": overlap_count == 0}).encode('utf-8'))
            except Exception as e:
                print("Availability check error:", e)
                self.wfile.write(json.dumps({"available": False}).encode('utf-8'))
            return

        if parsed_path.path in ['/api/packages', '/api/rooms', '/api/transport']:
            table = parsed_path.path.split('/')[-1]
            query = urllib.parse.parse_qs(parsed_path.query)
            req_checkin = query.get('checkin', [''])[0]
            req_checkout = query.get('checkout', [''])[0]
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute(f"SELECT * FROM {table}")
                rows = c.fetchall()
                
                # Fetch overlapping room bookings if dates are provided
                booked_room_ids = set()
                if table == 'rooms' and req_checkin and req_checkout:
                    c.execute("""
                        SELECT item_id FROM bookings 
                        WHERE item_type = 'room' AND status != 'Cancelled'
                        AND check_in < %s AND check_out > %s
                    """, (req_checkout, req_checkin))
                    booked_room_ids = {str(r[0]) for r in c.fetchall()}

                items = []
                for row in rows:
                    item = dict(row)
                    # Helper to safely load JSON
                    def safe_load(val):
                        if not val: return []
                        try:
                            return json.loads(val) if isinstance(val, str) else val
                        except: return []

                    if 'amenities' in item: item['amenities'] = safe_load(item['amenities'])
                    if 'inclusions' in item: item['inclusions'] = safe_load(item['inclusions'])
                    if 'itinerary' in item: item['itinerary'] = safe_load(item['itinerary'])
                    if 'images' in item: item['images'] = safe_load(item['images'])
                    
                    if 'available' in item: 
                        base_available = bool(item['available'])
                        if table == 'rooms' and str(item.get('id')) in booked_room_ids:
                            item['available'] = False
                        else:
                            item['available'] = base_available

                    if 'is_offer' in item: item['is_offer'] = bool(item['is_offer'])
                    if 'is_popular' in item: item['is_popular'] = bool(item['is_popular'])
                    items.append(item)
                conn.close()
                self.wfile.write(json.dumps(items).encode('utf-8'))
            except Exception as e:
                self.wfile.write(b"[]")
                print("DB Error:", e)
            return
            
        if parsed_path.path == '/api/banners':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM banners ORDER BY display_order ASC")
                rows = c.fetchall()
                items = [dict(row) for row in rows]
                conn.close()
                self.wfile.write(json.dumps(items).encode('utf-8'))
            except Exception as e:
                self.wfile.write(b"[]")
            return

        if parsed_path.path == '/api/settings':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM settings")
                rows = c.fetchall()
                settings = {row['key']: row['value'] for row in rows}
                conn.close()
                self.wfile.write(json.dumps(settings).encode('utf-8'))
            except Exception as e:
                self.wfile.write(b"{}")
            return

        if parsed_path.path == '/api/gallery':
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM gallery ORDER BY created_at DESC")
                items = c.fetchall()
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                # Convert datetime objects to string for JSON serialization
                json_items = []
                for item in items:
                    item_dict = dict(item)
                    if item_dict.get('created_at'):
                        item_dict['created_at'] = item_dict['created_at'].isoformat()
                    json_items.append(item_dict)
                self.wfile.write(json.dumps(json_items).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))
            return

        if parsed_path.path == '/api/glimpses':
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM glimpses ORDER BY display_order ASC, created_at DESC")
                items = c.fetchall()
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.end_headers()
                json_items = []
                for item in items:
                    item_dict = dict(item)
                    if item_dict.get('created_at'):
                        item_dict['created_at'] = item_dict['created_at'].isoformat()
                    json_items.append(item_dict)
                self.wfile.write(json.dumps(json_items).encode('utf-8'))
            except Exception as e:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b"[]")
            return
            
        if parsed_path.path == '/api/logout':
            token = self.get_session_token()
            if token in sessions:
                del sessions[token]
            
            self.send_response(200)
            c = cookies.SimpleCookie()
            c["session_token"] = ""
            c["session_token"]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
            c["session_token"]["path"] = "/"
            self.send_header("Set-Cookie", c.output(header='', sep='').strip())
            self.end_headers()
            self.wfile.write(b"Logged out")
            return

        if parsed_path.path == '/api/invites/validate':
            params = urllib.parse.parse_qs(parsed_path.query)
            token = params.get('token', [None])[0]
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            if not token:
                self.wfile.write(json.dumps({"status": "error", "message": "Missing token"}).encode('utf-8'))
                return
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT role, expires_at, is_used FROM admin_invites WHERE token = %s", (token,))
                row = c.fetchone()
                conn.close()
                if not row:
                    self.wfile.write(json.dumps({"status": "error", "message": "Invalid invite link."}).encode('utf-8'))
                    return
                if row['is_used'] == 1:
                    self.wfile.write(json.dumps({"status": "error", "message": "This invite link has already been used."}).encode('utf-8'))
                    return
                if row['expires_at'] and row['expires_at'] < datetime.now():
                    self.wfile.write(json.dumps({"status": "error", "message": "This invite link has expired."}).encode('utf-8'))
                    return
                self.wfile.write(json.dumps({"status": "success", "role": row['role']}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({"status": "error", "message": f"Database error: {e}"}).encode('utf-8'))
            return

        if parsed_path.path == '/api/admins':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict) or sessions[token].get('role') != 'super_admin':
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden: Super Admin access required")
                return
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT email, name, role FROM users ORDER BY name ASC")
                rows = c.fetchall()
                admins = [dict(row) for row in rows]
                conn.close()
                self.wfile.write(json.dumps(admins).encode('utf-8'))
            except Exception as e:
                self.wfile.write(b"[]")
            return

        if parsed_path.path == '/api/sessions':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            
            user_session = sessions[token]
            email = user_session['email']
            role = user_session['role']
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            active_list = []
            for t, s in list(sessions.items()):
                if not isinstance(s, dict): continue
                if role == 'super_admin' or s['email'] == email:
                    active_list.append({
                        "session_token": t,
                        "email": s['email'],
                        "name": s.get('name', 'Admin'),
                        "role": s.get('role', 'admin'),
                        "ip_address": s.get('ip_address', 'Unknown'),
                        "user_agent": s.get('user_agent', 'Unknown'),
                        "last_activity": s.get('last_activity', 0),
                        "remember_device": s.get('remember_device', False),
                        "is_current": (t == token)
                    })
            self.wfile.write(json.dumps(active_list).encode('utf-8'))
            return

        # --- DYNAMIC LOGO SERVING ---
        if parsed_path.path == '/images/logo.png':
            # Serve the permanent static website logo with a long-term cache to prevent visual flicker on reload/navigation
            if os.path.exists('images/logo.png'):
                self.send_response(200)
                self.send_header('Cache-Control', 'public, max-age=31536000')
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                with open('images/logo.png', 'rb') as f:
                    self.wfile.write(f.read())
                return

        super().do_GET()

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        client_ip = self.client_address[0]
        
        # --- SIGNUP FLOW (DISABLED) ---
        if parsed_path.path == '/api/signup':
            self.send_response(404)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": "Signup endpoint is disabled."}).encode('utf-8'))
            return

        # --- LOGIN FLOW (WITH RATE LIMIT & BRUTE FORCE PROTECTION) ---
        if parsed_path.path == '/api/login':
            # --- IP rate limit check ---
            is_blocked, secs_left = check_ip_blocked(client_ip)
            if is_blocked:
                self.send_response(429)
                self.send_header('Content-type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error",
                    "message": f"Too many login attempts from your IP. Try again in {secs_left // 60 + 1} minutes."
                }).encode('utf-8'))
                return

            # Safely read body
            try:
                content_length = int(self.headers.get('Content-Length', 0))
            except (ValueError, TypeError):
                content_length = 0
            if content_length <= 0 or content_length > 4096:
                self.send_response(400)
                self.end_headers()
                return

            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            email = data.get('email', '').strip().lower()
            password = data.get('password', '')
            remember_device = data.get('remember_device', False)

            # Generic error message — same whether email exists or not (prevents enumeration)
            GENERIC_ERROR = "Invalid credentials."
            
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT password_hash, role, name, failed_login_attempts, locked_until FROM users WHERE email = %s", (email,))
                user_row = c.fetchone()
                
                # Check account-level lockout
                if user_row:
                    locked_until = user_row["locked_until"]
                    if locked_until and locked_until > datetime.now():
                        lock_remaining = int((locked_until - datetime.now()).total_seconds())
                        self.send_response(403)
                        self.send_header('Content-type', 'application/json')
                        self.send_header('Cache-Control', 'no-store')
                        self._send_security_headers()
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": f"Account locked. Try again in {lock_remaining // 60 + 1} minutes."
                        }).encode('utf-8'))
                        conn.close()
                        return
                
                if user_row and verify_password(password, user_row["password_hash"]):
                    # ✅ Successful login — reset all counters
                    c.execute("UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE email = %s", (email,))
                    conn.commit()
                    conn.close()
                    reset_ip_counter(client_ip)
                    
                    token = secrets.token_hex(32)
                    sessions[token] = {
                        "email": email,
                        "role": user_row["role"],
                        "name": user_row["name"],
                        "last_activity": time.time(),
                        "remember_device": remember_device,
                        "ip_address": client_ip,
                        "user_agent": self.headers.get("User-Agent", "Unknown")
                    }
                    log_login_attempt(email, "SUCCESS (Logged In)", client_ip, self.headers.get("User-Agent"), token)
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Cache-Control', 'no-store')
                    self._send_security_headers()
                    cookie = cookies.SimpleCookie()
                    cookie["session_token"] = token
                    cookie["session_token"]["path"] = "/"
                    cookie["session_token"]["httponly"] = True
                    cookie["session_token"]["samesite"] = "Lax"
                    if remember_device:
                        cookie["session_token"]["max-age"] = 2592000  # 30 days
                    self.send_header("Set-Cookie", cookie.output(header='', sep='').strip())
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "role": user_row["role"], "name": user_row["name"]}).encode('utf-8'))
                else:
                    # ❌ Failed login — record failure against both account and IP
                    record_ip_failure(client_ip)
                    attempts = 1
                    if user_row:
                        attempts = user_row["failed_login_attempts"] + 1
                        if attempts >= 5:
                            lockout_time = datetime.now() + timedelta(minutes=15)
                            c.execute("UPDATE users SET failed_login_attempts = %s, locked_until = %s WHERE email = %s", (attempts, lockout_time, email))
                        else:
                            c.execute("UPDATE users SET failed_login_attempts = %s WHERE email = %s", (attempts, email))
                        conn.commit()
                    conn.close()
                    log_login_attempt(email, f"FAILED (Attempt {attempts})", client_ip, self.headers.get("User-Agent"))
                    
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Cache-Control', 'no-store')
                    self._send_security_headers()
                    self.end_headers()
                    # Always same message — never reveal if account exists
                    is_ip_now_blocked, _ = check_ip_blocked(client_ip)
                    if is_ip_now_blocked:
                        self.wfile.write(json.dumps({"status": "error", "message": f"Too many failed attempts. Your IP is blocked for {IP_BLOCK_MINUTES} minutes."}).encode('utf-8'))
                    elif user_row and attempts >= 5:
                        self.wfile.write(json.dumps({"status": "error", "message": "Too many failed attempts. Account locked for 15 minutes."}).encode('utf-8'))
                    else:
                        self.wfile.write(json.dumps({"status": "error", "message": GENERIC_ERROR}).encode('utf-8'))
            except Exception as e:
                print("DB error on login:", e)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self._send_security_headers()
                self.end_headers()
                # Never expose internal error details to the browser
                self.wfile.write(json.dumps({"status": "error", "message": "Login failed. Please try again."}).encode('utf-8'))
            return

        # --- INVITE GENERATE (Super Admin only) ---
        if parsed_path.path == '/api/invites/generate':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict) or sessions[token].get('role') != 'super_admin':
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            role = data.get('role', 'admin')
            
            invite_token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(hours=24)
            
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO admin_invites (token, role, expires_at) VALUES (%s, %s, %s)", 
                          (invite_token, role, expires_at))
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "invite_token": invite_token,
                    "invite_url": f"/tourver-admin-signup?token={invite_token}"
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Database error: {e}"}).encode('utf-8'))
            return

        # --- REGISTER VIA INVITE ---
        if parsed_path.path == '/api/invites/register':
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            invite_token = data.get('token')
            email = data.get('email')
            password = data.get('password')
            name = data.get('name', 'Admin')
            
            if not invite_token or not email or not password:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Missing required fields"}).encode('utf-8'))
                return
                

                
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT role, expires_at, is_used FROM admin_invites WHERE token = %s", (invite_token,))
                invite_row = c.fetchone()
                if not invite_row:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Invalid invite token"}).encode('utf-8'))
                    conn.close()
                    return
                
                if invite_row['is_used'] == 1:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Invite link already used"}).encode('utf-8'))
                    conn.close()
                    return
                    
                if invite_row['expires_at'] and invite_row['expires_at'] < datetime.now():
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Invite link expired"}).encode('utf-8'))
                    conn.close()
                    return
                
                c.execute("SELECT email FROM users WHERE email = %s", (email,))
                if c.fetchone():
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Email already registered"}).encode('utf-8'))
                    conn.close()
                    return
                
                c.execute("INSERT INTO users (email, password_hash, role, name) VALUES (%s, %s, %s, %s)", 
                          (email, hash_password(password), invite_row['role'], name))
                c.execute("UPDATE admin_invites SET is_used = 1 WHERE token = %s", (invite_token,))
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Admin registration successful"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Database error: {e}"}).encode('utf-8'))
            return

        # --- ADMIN CREATION & CONTROLS ---
        if parsed_path.path == '/api/admins/create':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict) or sessions[token].get('role') != 'super_admin':
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            email = data.get('email')
            password = data.get('password')
            name = data.get('name', 'Admin')
            role = data.get('role', 'admin')
            
            if not email or not password:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Missing email or password"}).encode('utf-8'))
                return
                

                
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT email FROM users WHERE email = %s", (email,))
                if c.fetchone():
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Email already exists"}).encode('utf-8'))
                    conn.close()
                    return
                c.execute("INSERT INTO users (email, password_hash, role, name) VALUES (%s, %s, %s, %s)", 
                          (email, hash_password(password), role, name))
                conn.commit()
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Admin user created successfully"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Database error: {e}"}).encode('utf-8'))
            return

        if parsed_path.path == '/api/admins/delete':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict) or sessions[token].get('role') != 'super_admin':
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            email = data.get('email')
            
            if email == 'admin@courtallamholidays.com':
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Cannot delete master admin account"}).encode('utf-8'))
                return
                
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("DELETE FROM users WHERE email = %s", (email,))
                conn.commit()
                conn.close()
                
                for t, s in list(sessions.items()):
                    if isinstance(s, dict) and s['email'] == email:
                        del sessions[t]
                        
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Admin deleted successfully"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Database error: {e}"}).encode('utf-8'))
            return

        if parsed_path.path == '/api/admins/update-role':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict) or sessions[token].get('role') != 'super_admin':
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            email = data.get('email')
            role = data.get('role')
            
            if email == 'admin@courtallamholidays.com':
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Cannot change role of master admin"}).encode('utf-8'))
                return
                
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE users SET role = %s WHERE email = %s", (role, email))
                conn.commit()
                conn.close()
                
                for t, s in list(sessions.items()):
                    if isinstance(s, dict) and s['email'] == email:
                        s['role'] = role
                        
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Role updated successfully"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Database error: {e}"}).encode('utf-8'))
            return

        if parsed_path.path == '/api/admins/reset-password':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict) or sessions[token].get('role') != 'super_admin':
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            email = data.get('email')
            new_password = data.get('password')
            
            if not email or not new_password:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Missing email or password"}).encode('utf-8'))
                return
                
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hash_password(new_password), email))
                conn.commit()
                conn.close()
                
                for t, s in list(sessions.items()):
                    if isinstance(s, dict) and s['email'] == email:
                        del sessions[t]
                        
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Password reset successfully"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Database error: {e}"}).encode('utf-8'))
            return

        # --- SESSION REVOCATION API ---
        if parsed_path.path == '/api/sessions/revoke':
            token = self.get_session_token()
            if not token or token not in sessions or not isinstance(sessions[token], dict):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            target_token = data.get('session_token')
            
            if not target_token:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Missing session token"}).encode('utf-8'))
                return
                
            user_session = sessions[token]
            email = user_session['email']
            role = user_session['role']
            
            if target_token not in sessions:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Session not found"}).encode('utf-8'))
                return
                
            target_session = sessions[target_token]
            if not isinstance(target_session, dict):
                self.send_response(400)
                self.end_headers()
                return
                
            if role == 'super_admin' or target_session['email'] == email:
                del sessions[target_token]
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Session revoked"}).encode('utf-8'))
            else:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Permission denied"}).encode('utf-8'))
            return
            
        # --- IMAGE / FILE UPLOAD → Supabase Storage ---
        if parsed_path.path == '/api/upload':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                return
            
            try:
                content_type = self.headers.get('Content-Type', '')
                if 'multipart/form-data' not in content_type:
                    raise Exception("Expected multipart/form-data")
                
                boundary = content_type.split("boundary=")[1].encode()
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                
                # Parse multipart body for file and optional bucket field
                file_bytes = None
                orig_filename = None
                bucket = "gallery"  # default bucket
                
                parts = body.split(boundary)
                for part in parts:
                    if b'\r\n\r\n' not in part:
                        continue
                    header_block, content = part.split(b'\r\n\r\n', 1)
                    content = content.rsplit(b'\r\n', 1)[0]  # strip trailing boundary marker
                    header_str = header_block.decode(errors='replace')
                    
                    if 'filename="' in header_str:
                        orig_filename = header_str.split('filename="')[1].split('"')[0]
                        file_bytes = content
                    elif 'name="bucket"' in header_str:
                        bucket = content.decode(errors='replace').strip()
                
                if not file_bytes or not orig_filename:
                    raise Exception("No file found in request")
                
                ext = os.path.splitext(orig_filename)[1].lower()
                allowed_exts = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.txt']
                if ext not in allowed_exts:
                    raise Exception(f"File type '{ext}' not allowed")
                
                # Map extension to MIME type
                mime_map = {
                    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                    '.webp': 'image/webp', '.gif': 'image/gif',
                    '.pdf': 'application/pdf',
                    '.doc': 'application/msword',
                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.xls': 'application/vnd.ms-excel',
                    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    '.txt': 'text/plain'
                }
                mime_type = mime_map.get(ext, 'application/octet-stream')
                
                prefix = "img_" if ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif'] else "doc_"
                new_filename = f"{prefix}{secrets.token_hex(8)}{ext}"
                
                public_url = upload_to_supabase(bucket, file_bytes, new_filename, mime_type)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "url": public_url}).encode('utf-8'))
            except Exception as e:
                print(f"[Upload Error] {e}")
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return
            
        if parsed_path.path in ['/api/inquiries', '/api/bookings']:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            table = parsed_path.path.split('/')[-1]
            try:
                new_item = json.loads(post_data.decode('utf-8'))
                conn = get_db_connection()
                c = conn.cursor()
                
                if table == 'inquiries':
                    c.execute("INSERT INTO inquiries (name, email, subject, message) VALUES (%s, %s, %s, %s)", 
                              (new_item.get('name'), new_item.get('email'), new_item.get('subject'), new_item.get('message')))
                elif table == 'bookings':
                    item_type = new_item.get('item_type')
                    item_id = new_item.get('item_id')
                    guests = int(new_item.get('guests', 1))
                    
                    # Calculate default amount
                    amount = 0
                    if item_type == 'package':
                        c.execute("SELECT price, weekday_price, weekend_price FROM packages WHERE id = %s", (item_id,))
                        row = c.fetchone()
                        if row:
                            price = row['weekday_price'] if row['weekday_price'] else (row['price'] if row['price'] else 0)
                            amount = price * guests
                    elif item_type == 'room':
                        c.execute("SELECT price, weekday_price, weekend_price FROM rooms WHERE id = %s", (item_id,))
                        row = c.fetchone()
                        if row:
                            price = row['weekday_price'] if row['weekday_price'] else (row['price'] if row['price'] else 0)
                            try:
                                check_in_dt = datetime.strptime(new_item.get('check_in'), "%Y-%m-%d")
                                check_out_dt = datetime.strptime(new_item.get('check_out'), "%Y-%m-%d")
                                nights = max((check_out_dt - check_in_dt).days, 1)
                            except Exception:
                                nights = 1
                            amount = price * nights
                    elif item_type == 'transport':
                        c.execute("SELECT price, weekday_price, weekend_price FROM transport WHERE id = %s", (item_id,))
                        row = c.fetchone()
                        if row:
                            price = row['weekday_price'] if row['weekday_price'] else (row['price'] if row['price'] else 0)
                            amount = price

                    c.execute("""INSERT INTO bookings (customer_name, customer_email, customer_phone, item_type, item_id, 
                                                     check_in, check_out, guests, message, amount, status) 
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                              (new_item.get('customer_name'), new_item.get('customer_email'), new_item.get('customer_phone'), 
                               item_type, item_id, new_item.get('check_in'), new_item.get('check_out'), guests, 
                               new_item.get('message'), amount, 'New Request'))
                
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return

        if parsed_path.path == '/api/bookings/update':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                booking_id = int(data.get('id'))
                new_status = data.get('status')
                
                conn = get_db_connection()
                c = conn.cursor()
                
                # Fetch current booking status and details
                c.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
                booking = c.fetchone()
                if not booking:
                    raise Exception("Booking not found")
                
                old_status = booking['status']
                booking_id_generated = booking['unique_booking_id']
                
                # Generate unique booking ID and invoice number if transitioning to confirmed status
                if new_status in ['Payment Confirmed', 'Booking Confirmed']:
                    if not booking_id_generated:
                        check_in_clean = booking['check_in'].strftime('%Y%m%d') if hasattr(booking['check_in'], 'strftime') else str(booking['check_in']).replace('-', '')
                        booking_id_generated = f"CH-{check_in_clean}-{booking_id}"
                
                invoice_num = data.get('invoice_number')
                if invoice_num is None:
                    invoice_num = booking['invoice_number']
                if not invoice_num and new_status in ['Payment Confirmed', 'Booking Confirmed']:
                    invoice_num = f"INV-{booking_id_generated or booking_id}"
                
                # Update booking details
                c.execute("""UPDATE bookings SET status = %s, amount = %s, provider_name = %s, provider_upi = %s, provider_phone = %s, provider_qr_url = %s, payment_notes = %s, booking_documents = %s, unique_booking_id = %s, advance_amount = %s, balance_amount = %s, invoice_number = %s, is_duplicate = %s WHERE id = %s""", (
                    new_status,
                    int(data.get('amount', booking['amount'] or 0)),
                    data.get('provider_name'),
                    data.get('provider_upi'),
                    data.get('provider_phone'),
                    data.get('provider_qr_url'),
                    data.get('payment_notes'),
                    data.get('booking_documents'),
                    booking_id_generated,
                    int(data.get('advance_amount', booking['advance_amount'] or 0)),
                    int(data.get('balance_amount', booking['balance_amount'] or 0)),
                    invoice_num,
                    bool(data.get('is_duplicate', booking['is_duplicate'] or False)),
                    booking_id
                ))
                
                conn.commit()
                
                # Refetch to get all updated fields
                c.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
                updated_booking = dict(c.fetchone())
                
                # Convert date fields
                if updated_booking['check_in'] and hasattr(updated_booking['check_in'], 'isoformat'):
                    updated_booking['check_in'] = updated_booking['check_in'].isoformat()
                if updated_booking['check_out'] and hasattr(updated_booking['check_out'], 'isoformat'):
                    updated_booking['check_out'] = updated_booking['check_out'].isoformat()
                
                # Trigger automation if status became Payment Confirmed
                should_trigger = new_status in ['Payment Confirmed', 'Booking Confirmed']
                was_already_triggered = old_status in ['Payment Confirmed', 'Booking Confirmed']
                if should_trigger and (not was_already_triggered or not booking['invoice_pdf_url']):
                    pdf_url = generate_invoice_pdf(updated_booking)
                    
                    c.execute("UPDATE bookings SET invoice_pdf_url = %s WHERE id = %s", (pdf_url, booking_id))
                    conn.commit()
                    
                    updated_booking['invoice_pdf_url'] = pdf_url
                    
                    import tempfile
                    pdf_local_path = os.path.join(tempfile.gettempdir(), f"invoice_{booking_id_generated}.pdf")
                    send_invoice_email(updated_booking, pdf_local_path)
                    send_invoice_whatsapp(updated_booking, pdf_url)
                
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "booking_id": booking_id_generated}).encode())
                
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return
            
        if parsed_path.path == '/api/bookings/regenerate':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                booking_id = int(data.get('id'))
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
                booking = dict(c.fetchone())
                
                # Auto generate reference ID and invoice number if not set
                if not booking['unique_booking_id']:
                    check_in_clean = booking['check_in'].strftime('%Y%m%d') if hasattr(booking['check_in'], 'strftime') else str(booking['check_in']).replace('-', '')
                    booking['unique_booking_id'] = f"CH-{check_in_clean}-{booking_id}"
                    c.execute("UPDATE bookings SET unique_booking_id = %s WHERE id = %s", (booking['unique_booking_id'], booking_id))
                    conn.commit()
                if not booking['invoice_number']:
                    booking['invoice_number'] = f"INV-{booking['unique_booking_id']}"
                    c.execute("UPDATE bookings SET invoice_number = %s WHERE id = %s", (booking['invoice_number'], booking_id))
                    conn.commit()
                
                if booking['check_in'] and hasattr(booking['check_in'], 'isoformat'):
                    booking['check_in'] = booking['check_in'].isoformat()
                if booking['check_out'] and hasattr(booking['check_out'], 'isoformat'):
                    booking['check_out'] = booking['check_out'].isoformat()
                
                # Regenerate invoice PDF
                pdf_url = generate_invoice_pdf(booking)
                c.execute("UPDATE bookings SET invoice_pdf_url = %s WHERE id = %s", (pdf_url, booking_id))
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "url": pdf_url}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return

        if parsed_path.path == '/api/bookings/send-email':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                booking_id = int(data.get('id'))
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
                booking = dict(c.fetchone())
                
                # Auto generate reference ID and invoice number if not set
                if not booking['unique_booking_id']:
                    check_in_clean = booking['check_in'].strftime('%Y%m%d') if hasattr(booking['check_in'], 'strftime') else str(booking['check_in']).replace('-', '')
                    booking['unique_booking_id'] = f"CH-{check_in_clean}-{booking_id}"
                    c.execute("UPDATE bookings SET unique_booking_id = %s WHERE id = %s", (booking['unique_booking_id'], booking_id))
                    conn.commit()
                if not booking['invoice_number']:
                    booking['invoice_number'] = f"INV-{booking['unique_booking_id']}"
                    c.execute("UPDATE bookings SET invoice_number = %s WHERE id = %s", (booking['invoice_number'], booking_id))
                    conn.commit()
                
                if booking['check_in'] and hasattr(booking['check_in'], 'isoformat'):
                    booking['check_in'] = booking['check_in'].isoformat()
                if booking['check_out'] and hasattr(booking['check_out'], 'isoformat'):
                    booking['check_out'] = booking['check_out'].isoformat()
                
                # Regenerate invoice PDF
                pdf_url = generate_invoice_pdf(booking)
                c.execute("UPDATE bookings SET invoice_pdf_url = %s WHERE id = %s", (pdf_url, booking_id))
                conn.commit()
                booking['invoice_pdf_url'] = pdf_url
                conn.close()
                
                import tempfile
                pdf_local_path = os.path.join(tempfile.gettempdir(), f"invoice_{booking['unique_booking_id']}.pdf")
                send_invoice_email(booking, pdf_local_path)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Email sent successfully"}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return

        if parsed_path.path == '/api/bookings/send-whatsapp':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                booking_id = int(data.get('id'))
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
                booking = dict(c.fetchone())
                
                # Auto generate reference ID and invoice number if not set
                if not booking['unique_booking_id']:
                    check_in_clean = booking['check_in'].strftime('%Y%m%d') if hasattr(booking['check_in'], 'strftime') else str(booking['check_in']).replace('-', '')
                    booking['unique_booking_id'] = f"CH-{check_in_clean}-{booking_id}"
                    c.execute("UPDATE bookings SET unique_booking_id = %s WHERE id = %s", (booking['unique_booking_id'], booking_id))
                    conn.commit()
                if not booking['invoice_number']:
                    booking['invoice_number'] = f"INV-{booking['unique_booking_id']}"
                    c.execute("UPDATE bookings SET invoice_number = %s WHERE id = %s", (booking['invoice_number'], booking_id))
                    conn.commit()
                
                if booking['check_in'] and hasattr(booking['check_in'], 'isoformat'):
                    booking['check_in'] = booking['check_in'].isoformat()
                if booking['check_out'] and hasattr(booking['check_out'], 'isoformat'):
                    booking['check_out'] = booking['check_out'].isoformat()
                
                # Regenerate invoice PDF
                pdf_url = generate_invoice_pdf(booking)
                c.execute("UPDATE bookings SET invoice_pdf_url = %s WHERE id = %s", (pdf_url, booking_id))
                conn.commit()
                booking['invoice_pdf_url'] = pdf_url
                conn.close()
                
                send_invoice_whatsapp(booking, pdf_url)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "WhatsApp sent successfully"}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return
            
        # Generic API POST for tables
        if parsed_path.path in ['/api/packages', '/api/rooms', '/api/transport']:
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            table = parsed_path.path.split('/')[-1]
            try:
                new_item = json.loads(post_data.decode('utf-8'))
                conn = get_db_connection()
                c = conn.cursor()
                
                if table == 'packages':
                    columns = ['id', 'title', 'price', 'weekday_price', 'weekday_original_price', 'weekend_price', 'weekend_original_price', 'is_offer', 'is_popular', 'display_order', 'duration', 'category', 'image', 'images', 'description', 'inclusions', 'itinerary']
                    new_item['is_offer'] = 1 if new_item.get('is_offer') else 0
                    new_item['is_popular'] = 1 if new_item.get('is_popular') else 0
                    new_item['inclusions'] = json.dumps(new_item.get('inclusions', []))
                    new_item['itinerary'] = json.dumps(new_item.get('itinerary', []))
                elif table == 'rooms':
                    columns = ['id', 'name', 'price', 'weekday_price', 'weekday_original_price', 'weekend_price', 'weekend_original_price', 'is_offer', 'category', 'image', 'images', 'description', 'amenities', 'available', 'capacity', 'check_in_time', 'check_out_time']
                    new_item['amenities'] = json.dumps(new_item.get('amenities', []))
                    new_item['available'] = 1 if new_item.get('available', True) else 0
                    new_item['is_offer'] = 1 if new_item.get('is_offer') else 0

                elif table == 'transport':
                    columns = ['id', 'name', 'price', 'weekday_price', 'weekday_original_price', 'weekend_price', 'weekend_original_price', 'is_offer', 'category', 'image', 'images', 'description', 'capacity']
                    new_item['is_offer'] = 1 if new_item.get('is_offer') else 0

                fields = [col for col in columns if col in new_item]
                values = [new_item[col] for col in fields]
                
                placeholders = ', '.join(['%s'] * len(fields))
                field_names = ', '.join(fields)
                update_clause = ', '.join([f"{f} = EXCLUDED.{f}" for f in fields if f != 'id'])
                
                query = f"""INSERT INTO {table} ({field_names}) VALUES ({placeholders}) 
                            ON CONFLICT (id) DO UPDATE SET {update_clause}"""
                
                c.execute(query, values)
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "item": new_item}).encode())
                
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return

        if parsed_path.path == '/api/banners':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                return
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            try:
                conn = get_db_connection()
                c = conn.cursor()
                if data.get('id'):
                    c.execute("UPDATE banners SET image_url=%s, display_order=%s, is_active=%s WHERE id=%s",
                             (data['image_url'], data['display_order'], 1 if data.get('is_active', True) else 0, int(data['id'])))
                else:
                    c.execute("INSERT INTO banners (image_url, display_order, is_active) VALUES (%s, %s, %s)",
                             (data['image_url'], data['display_order'], 1 if data.get('is_active', True) else 0))
                conn.commit()
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode())
            return

        if parsed_path.path == '/api/settings':
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                return
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            try:
                conn = get_db_connection()
                c = conn.cursor()
                for key, value in data.items():
                    c.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, str(value)))
                conn.commit()
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode())
            return

        if parsed_path.path == '/api/gallery':
            if not self.is_authenticated():
                self.send_error(401, "Unauthorized")
                return
            try:
                content_type = self.headers.get('Content-Type', '')
                if 'multipart/form-data' not in content_type:
                    raise Exception("Expected multipart/form-data")
                boundary = content_type.split("boundary=")[1].encode()
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                
                file_bytes = None
                orig_filename = None
                caption = ''
                
                parts = body.split(boundary)
                for part in parts:
                    if b'\r\n\r\n' not in part:
                        continue
                    header_block, content = part.split(b'\r\n\r\n', 1)
                    content = content.rsplit(b'\r\n', 1)[0]
                    header_str = header_block.decode(errors='replace')
                    if 'filename="' in header_str:
                        orig_filename = header_str.split('filename="')[1].split('"')[0]
                        file_bytes = content
                    elif 'name="caption"' in header_str:
                        caption = content.decode(errors='replace').strip()
                
                if not file_bytes or not orig_filename:
                    raise Exception("No image file found in request")
                
                ext = os.path.splitext(orig_filename)[1].lower() or '.jpg'
                new_filename = f"gallery_{secrets.token_hex(8)}{ext}"
                mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}
                mime_type = mime_map.get(ext, 'image/jpeg')
                
                image_url = upload_to_supabase("gallery", file_bytes, new_filename, mime_type)
                
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO gallery (image_url, caption) VALUES (%s, %s)", (image_url, caption))
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "url": image_url}).encode('utf-8'))
            except Exception as e:
                print(f"[Gallery Upload Error] {e}")
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if parsed_path.path == '/api/glimpses':
            if not self.is_authenticated():
                self.send_error(401, "Unauthorized")
                return
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
            try:
                conn = get_db_connection()
                c = conn.cursor()
                if data.get('id'):
                    c.execute("""UPDATE glimpses SET title=%s, description=%s, image_url=%s, display_order=%s WHERE id=%s""",
                             (data.get('title', ''), data.get('description', ''), data.get('image_url', ''), data.get('display_order', 0), int(data['id'])))
                else:
                    c.execute("""INSERT INTO glimpses (title, description, image_url, display_order) VALUES (%s, %s, %s, %s)""",
                             (data.get('title', ''), data.get('description', ''), data.get('image_url', ''), data.get('display_order', 0)))
                conn.commit()
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
            except Exception as e:
                print(f"POST Error: {e}")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed_path = urllib.parse.urlparse(self.path)
        
        for table in ['packages', 'rooms', 'transport', 'inquiries', 'bookings', 'banners', 'gallery', 'glimpses']:
            if parsed_path.path.startswith(f'/api/{table}/'):
                if not self.is_authenticated():
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b"Unauthorized")
                    return
                
                item_id = parsed_path.path.split('/')[-1]
                try:
                    conn = get_db_connection()
                    c = conn.cursor()
                    
                    # Attempt to delete old image from Supabase Storage
                    bucket_map = {'rooms': 'rooms', 'packages': 'packages', 'transport': 'transport', 'gallery': 'gallery', 'banners': 'gallery'}
                    if table in bucket_map:
                        try:
                            if table == 'gallery':
                                c.execute("SELECT image_url FROM gallery WHERE id = %s", (item_id,))
                            elif table == 'banners':
                                c.execute("SELECT image_url FROM banners WHERE id = %s", (item_id,))
                            elif table in ['rooms', 'packages', 'transport']:
                                c.execute(f"SELECT image FROM {table} WHERE id = %s", (item_id,))
                            row = c.fetchone()
                            if row and row[0]:
                                delete_from_supabase(bucket_map[table], row[0])
                        except Exception as e:
                            print(f"[Supabase pre-delete lookup failed]: {e}")
                    
                    c.execute(f"DELETE FROM {table} WHERE id = %s", (item_id,))
                    conn.commit()
                    conn.close()
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode())
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
                return
            
        self.send_response(404)
        self.end_headers()

Handler = AdminHTTPRequestHandler
handler = AdminHTTPRequestHandler  # Vercel Python runtime compatibility

class ThreadingSimpleServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    pass

if __name__ == '__main__':
    init_db()
        
    with ThreadingSimpleServer(("", PORT), Handler) as httpd:
        print(f"Serving at port {PORT}")
        print(f"Admin API ready at http://localhost:{PORT}")
        httpd.serve_forever()
