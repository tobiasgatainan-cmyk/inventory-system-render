import os, json
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
def now_tw():
    return datetime.now(TZ_TAIPEI)

import gspread
from google.oauth2.service_account import Credentials

SCOPES   = ['https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.file']
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
SA_JSON  = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')

def _client():
    if not SA_JSON: raise EnvironmentError('缺少 GOOGLE_SERVICE_ACCOUNT_JSON')
    creds = Credentials.from_service_account_info(json.loads(SA_JSON), scopes=SCOPES)
    return gspread.authorize(creds)

def _sheet(tab):
    gc = _client(); sh = gc.open_by_key(SHEET_ID)
    try: return sh.worksheet(tab)
    except gspread.WorksheetNotFound: return sh.add_worksheet(title=tab, rows=2000, cols=20)

def _find_row(ws, key_value, key_col=1):
    """在該工作表第 key_col 欄（1-based）尋找等於 key_value 的列，回傳列號；找不到回傳 None"""
    try:
        col_values = ws.col_values(key_col)
    except Exception:
        return None
    for i, v in enumerate(col_values, start=1):
        if str(v) == str(key_value):
            return i
    return None

def _ensure_headers(ws, headers):
    try:
        first_row = ws.row_values(1)
    except Exception:
        first_row = []
    if first_row != headers:
        ws.clear()
        ws.append_row(headers)


# ── Order sync（找到就覆蓋原本那一列，找不到才新增）────────
def sync_order(order):
    ws = _sheet('申請單')
    HEADERS = ['申請單號','申請人','狀態','品項摘要','申請人備註','管理員備註','申請時間','處理時間']
    _ensure_headers(ws, HEADERS)

    status_map = {'pending': '待處理', 'confirmed': '已出貨', 'cancelled': '已取消'}
    items_summary = '／'.join(f'{oi.item_name}×{oi.qty_request}' for oi in order.items)
    row = [
        order.order_no,
        order.applicant,
        status_map.get(order.status, order.status),
        items_summary,
        order.note or '',
        order.admin_note or '',
        order.created_at.strftime('%Y-%m-%d %H:%M'),
        order.confirmed_at.strftime('%Y-%m-%d %H:%M') if order.confirmed_at else '',
    ]
    row_num = _find_row(ws, order.order_no, key_col=1)
    if row_num:
        ws.update(f'A{row_num}', [row])
    else:
        ws.append_row(row)


# ── Shortage request sync（同上，用回報編號當 key）──────────
def sync_shortage(req):
    ws = _sheet('缺貨回報')
    HEADERS = ['編號','品項名稱','申請人','需求說明','狀態','處理備註','回報時間']
    _ensure_headers(ws, HEADERS)

    row = [
        req.id,
        req.item_name,
        req.applicant,
        req.note or '',
        '已處理' if req.resolved else '待處理',
        req.handle_note or '',
        req.created_at.strftime('%Y-%m-%d %H:%M'),
    ]
    row_num = _find_row(ws, req.id, key_col=1)
    if row_num:
        ws.update(f'A{row_num}', [row])
    else:
        ws.append_row(row)


# ── Full sync ─────────────────────────────────────────────
def _display_rows_for_item(item):
    """依「品牌名稱＋規格名稱」分組：同組有庫存只留有庫存的批次，
    全部是 0 才保留一筆 —— 與後台管理頁面的顯示邏輯保持一致。"""
    groups = {}
    order  = []
    for brand in item.brands:
        for spec in brand.specs:
            key = (brand.name, spec.name)
            if key not in groups:
                groups[key] = []
                order.append(key)
            for batch in spec.batches:
                groups[key].append({'brand': brand, 'spec': spec, 'batch': batch})
    rows = []
    for key in order:
        grp = groups[key]
        nonzero = [r for r in grp if r['batch'].qty > 0]
        rows.extend(nonzero if nonzero else grp[:1])
    return rows


def full_sync():
    from main import Item, Category
    ws = _sheet('庫存')

    existing = {}
    try:
        for r in ws.get_all_records():
            key = (str(r.get('品項','')), str(r.get('品牌','')),
                   str(r.get('規格','')), str(r.get('批次ID','')))
            existing[key] = {'qty': r.get('數量',''), 'last_sync': r.get('最後同步','')}
    except Exception: pass

    now  = now_tw().strftime('%Y-%m-%d %H:%M')
    rows = [['類別','品項','品牌','規格','批次ID','數量','安全庫存','到期日','供應商','進價','備註','最後同步']]

    for item in Item.query.join(Category, Item.category_id == Category.id, isouter=True)\
                          .order_by(Category.sort_order, Item.sort_order, Item.name).all():
        for row in _display_rows_for_item(item):
            brand, spec, batch = row['brand'], row['spec'], row['batch']
            key = (item.name, brand.name, spec.name, str(batch.id))
            old = existing.get(key, {})
            try: old_qty = int(old.get('qty', -999))
            except (ValueError, TypeError): old_qty = -999
            last_sync = now if old_qty != batch.qty else (old.get('last_sync') or now)
            rows.append([
                item.category.name if item.category else '',
                item.name, brand.name, spec.name, batch.id,
                batch.qty,
                brand.safe_qty,
                batch.expiry_date.isoformat() if batch.expiry_date else '',
                batch.supplier or '',
                float(batch.cost_price) if batch.cost_price else '',
                batch.note or '',
                last_sync,
            ])
    ws.clear(); ws.update('A1', rows)
    return f'已寫入 {len(rows)-1} 筆'


# ── Append log row ────────────────────────────────────────
def append_log_row(batch, change, reason, username, applicant=''):
    """修復：每次都確認標題列，然後在最後一行後 append"""
    ws  = _sheet('異動紀錄')
    now = now_tw().strftime('%Y-%m-%d %H:%M:%S')

    HEADERS = ['時間','類別','品項','品牌','規格','異動','申請人','原因','操作人']

    # 確認第一列是標題列（如果空的或標題不對就重設）
    try:
        first_row = ws.row_values(1)
    except Exception:
        first_row = []

    if first_row != HEADERS:
        ws.clear()
        ws.append_row(HEADERS)

    spec  = batch.spec
    brand = spec.brand
    item  = brand.item
    ws.append_row([
        now,
        item.category.name if item.category else '',
        item.name,
        brand.name,
        spec.name,
        f'+{change}' if change > 0 else str(change),
        applicant,
        reason,
        username,
    ])


# ── Append purchase record ────────────────────────────────
def append_purchase_record(batch, username):
    """入庫時記錄歷史進貨資料，方便日後比價、選擇進貨管道"""
    ws  = _sheet('歷史庫存比較')
    now = now_tw().strftime('%Y-%m-%d %H:%M')

    HEADERS = ['時間','品項','品牌','規格','入庫數量','到期日','進價','供應商','備註','操作人']

    try:
        first_row = ws.row_values(1)
    except Exception:
        first_row = []

    if first_row != HEADERS:
        ws.clear()
        ws.append_row(HEADERS)

    spec  = batch.spec
    brand = spec.brand
    item  = brand.item
    ws.append_row([
        now,
        item.name,
        brand.name,
        spec.name,
        batch.qty,
        batch.expiry_date.isoformat() if batch.expiry_date else '',
        float(batch.cost_price) if batch.cost_price else '',
        batch.supplier or '',
        batch.note or '',
        username,
    ])


# ── Import from sheet ─────────────────────────────────────
def import_from_sheet():
    from main import db, Item, Brand, Spec, Batch, StockLog
    ws = _sheet('庫存'); records = ws.get_all_records()
    updated = skipped = 0; errors = []
    for row in records:
        item_name  = str(row.get('品項','')).strip()
        brand_name = str(row.get('品牌','')).strip()
        spec_name  = str(row.get('規格','')).strip()
        batch_id   = row.get('批次ID','')
        if not item_name: continue
        item = Item.query.filter_by(name=item_name).first()
        if not item: errors.append(f'找不到品項：{item_name}'); continue
        brand = Brand.query.filter_by(item_id=item.id, name=brand_name).first()
        if not brand: errors.append(f'找不到品牌：{item_name}/{brand_name}'); continue
        spec = Spec.query.filter_by(brand_id=brand.id, name=spec_name).first()
        if not spec: errors.append(f'找不到規格：{brand_name}/{spec_name}'); continue
        batch = Batch.query.get(int(batch_id)) if batch_id else None
        if not batch: errors.append(f'找不到批次ID：{batch_id}'); continue
        try: new_qty = int(row.get('數量', batch.qty))
        except (ValueError, TypeError): errors.append(f'數量格式錯誤：{item_name}'); continue
        if new_qty != batch.qty:
            diff = new_qty - batch.qty
            db.session.add(StockLog(batch_id=batch.id, change=diff,
                                    reason='從 Google Sheet 匯入', user_id=None))
            batch.qty = new_qty; updated += 1
        else: skipped += 1
    db.session.commit()
    msg = f'已更新 {updated} 筆'
    if skipped: msg += f'，{skipped} 筆無變化'
    if errors:  msg += f'，{len(errors)} 筆錯誤：' + '；'.join(errors[:3])
    return msg
