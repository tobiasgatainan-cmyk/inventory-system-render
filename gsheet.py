"""
gsheet.py  ── Google Sheets 雙向同步
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
# 只有數量有變化的品項才更新「最後同步」時間
def full_sync():
    from main import Item, Spec
    ws = _get_sheet('庫存')

    # 讀取現有 Sheet 資料，建立 {(品項,規格): (數量, 最後同步)} 的對照表
    existing = {}
    try:
        records = ws.get_all_records()
        for r in records:
            key = (str(r.get('品項','')).strip(), str(r.get('規格','')).strip())
            existing[key] = {
                'qty': r.get('數量', ''),
                'last_sync': r.get('最後同步', '')
            }
    except Exception:
        pass

    now = now_tw().strftime('%Y-%m-%d %H:%M')
    rows = [['品項', '規格', '單位', '數量', '安全庫存', '供應商', '分類', '最後同步']]

    for item in Item.query.join(
            __import__('main').Category, Item.category_id == __import__('main').Category.id, isouter=True
        ).order_by(__import__('main').Category.sort_order, Item.sort_order, Item.name).all():
        for spec in item.specs:
            key = (item.name, spec.name)
            old = existing.get(key, {})
            # 只有數量有變化才更新同步時間
            try:
                old_qty = int(old.get('qty', -999))
            except (ValueError, TypeError):
                old_qty = -999
            last_sync = now if old_qty != spec.qty else (old.get('last_sync') or now)
            rows.append([
                item.name,
                spec.name,
                item.unit,
                spec.qty,
                spec.safe_qty,
                item.supplier or '',
                item.category.name if item.category else '',
                last_sync,
            ])

    ws.clear()
    ws.update('A1', rows)
    return f'已寫入 {len(rows)-1} 筆'


# ── Append a single log row to "異動紀錄" tab ─────────────
def append_log_row(spec, change: int, reason: str, username: str):
    ws  = _get_sheet('異動紀錄')
    now = now_tw().strftime('%Y-%m-%d %H:%M:%S')
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
    """
    讀取「庫存」分頁，把數量和安全庫存寫回資料庫。
    只更新有變化的欄位，並記錄匯入異動紀錄。
    回傳 (updated_count, skipped_count, errors)
    """
    from main import db, Item, Spec, StockLog, now_tw as main_now_tw

    ws = _get_sheet('庫存')
    records = ws.get_all_records()

    updated = 0
    skipped = 0
    errors  = []

    for row in records:
        item_name = str(row.get('品項', '')).strip()
        spec_name = str(row.get('規格', '')).strip()
        if not item_name:
            continue

        item = Item.query.filter_by(name=item_name).first()
        if not item:
            errors.append(f'找不到品項：{item_name}')
            continue

        spec = Spec.query.filter_by(item_id=item.id, name=spec_name).first()
        if not spec:
            errors.append(f'找不到規格：{item_name} / {spec_name}')
            continue

        try:
            new_qty      = int(row.get('數量', spec.qty))
            new_safe_qty = int(row.get('安全庫存', spec.safe_qty))
        except (ValueError, TypeError):
            errors.append(f'數量格式錯誤：{item_name} / {spec_name}')
            continue

        changed = False
        if new_qty != spec.qty:
            diff = new_qty - spec.qty
            # 記錄異動
            log = StockLog(
                spec_id=spec.id,
                change=diff,
                reason='從 Google Sheet 匯入',
                user_id=None
            )
            db.session.add(log)
            spec.qty = new_qty
            changed = True

        if new_safe_qty != spec.safe_qty:
            spec.safe_qty = new_safe_qty
            changed = True

        if changed:
            updated += 1
        else:
            skipped += 1

    db.session.commit()

    msg = f'已更新 {updated} 筆'
    if skipped:
        msg += f'，{skipped} 筆無變化略過'
    if errors:
        msg += f'，{len(errors)} 筆錯誤：' + '；'.join(errors[:3])
    return msg
