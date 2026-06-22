"""
gsheet.py  ── Google Sheets 雙向同步
設定方式：
  1. 在 Google Cloud Console 建立 Service Account，下載 JSON key
  2. 把 JSON key 的內容存成 Render 環境變數 GOOGLE_SERVICE_ACCOUNT_JSON
  3. 把試算表 ID 存成環境變數 GOOGLE_SHEET_ID
  4. 把試算表分享給 Service Account 的 email（編輯者權限）
"""
import os
import json
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))

def now_tw():
    return datetime.now(TZ_TAIPEI)

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
]

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
SA_JSON  = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')


def _get_client():
    if not SA_JSON:
        raise EnvironmentError('缺少環境變數 GOOGLE_SERVICE_ACCOUNT_JSON')
    info = json.loads(SA_JSON)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_sheet(tab_name: str):
    gc = _get_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        return sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=tab_name, rows=1000, cols=20)


# ── Full sync: write all items to "庫存" tab ──────────────
def full_sync():
    from main import Item, Spec  # lazy import to avoid circular
    ws = _get_sheet('庫存')
    rows = [['品項', '規格', '單位', '數量', '安全庫存', '供應商', '分類', '最後同步']]
    now  = now_tw().strftime('%Y-%m-%d %H:%M')
    for item in Item.query.order_by(Item.name).all():
        for spec in item.specs:
            rows.append([
                item.name,
                spec.name,
                item.unit,
                spec.qty,
                spec.safe_qty,
                item.supplier or '',
                item.category.name if item.category else '',
                now,
            ])
    ws.clear()
    ws.update('A1', rows)
    return f'已寫入 {len(rows)-1} 筆'


# ── Append a single log row to "異動紀錄" tab ─────────────
def append_log_row(spec, change: int, reason: str, username: str):
    ws  = _get_sheet('異動紀錄')
    now = now_tw().strftime('%Y-%m-%d %H:%M:%S')
    # Ensure header
    if ws.row_count < 1 or ws.cell(1, 1).value != '時間':
        ws.insert_row(['時間', '品項', '規格', '異動', '理由', '操作人'], index=1)
    ws.append_row([
        now,
        spec.item.name,
        spec.name,
        f'+{change}' if change > 0 else str(change),
        reason,
        username,
    ])


# ── Import from Sheet back to DB ──────────────────────────
def import_from_sheet():
    """Read '庫存' tab and upsert into DB (qty + safe_qty only)."""
    from main import db, Item, Spec, Category
    ws   = _get_sheet('庫存')
    rows = ws.get_all_records()
    updated = 0
    for row in rows:
        item_name = str(row.get('品項', '')).strip()
        spec_name = str(row.get('規格', '')).strip()
        if not item_name:
            continue
        item = Item.query.filter_by(name=item_name).first()
        if not item:
            continue
        spec = Spec.query.filter_by(item_id=item.id, name=spec_name).first()
        if spec:
            try:
                spec.qty      = int(row.get('數量', spec.qty))
                spec.safe_qty = int(row.get('安全庫存', spec.safe_qty))
                updated += 1
            except ValueError:
                pass
    db.session.commit()
    return f'已更新 {updated} 筆規格'
