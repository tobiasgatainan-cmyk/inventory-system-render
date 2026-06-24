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
    except gspread.WorksheetNotFound: return sh.add_worksheet(title=tab, rows=1000, cols=20)


# ── Full sync ─────────────────────────────────────────────
def full_sync():
    from main import Item, Brand, Spec, Batch, Category
    ws = _sheet('庫存')

    # 讀取現有資料，記錄最後同步時間（只有數量變化才更新）
    existing = {}
    try:
        for r in ws.get_all_records():
            key = (str(r.get('品項','')), str(r.get('品牌','')),
                   str(r.get('規格','')), str(r.get('批次ID','')))
            existing[key] = {'qty': r.get('數量',''), 'last_sync': r.get('最後同步','')}
    except Exception: pass

    now  = now_tw().strftime('%Y-%m-%d %H:%M')
    rows = [['品項','品牌','規格','批次ID','數量','到期日','進價','安全庫存','供應商','分類','最後同步']]

    for item in Item.query.join(Category, Item.category_id == Category.id, isouter=True)\
                          .order_by(Category.sort_order, Item.sort_order, Item.name).all():
        for brand in item.brands:
            for spec in brand.specs:
                for batch in spec.batches:
                    key = (item.name, brand.name, spec.name, str(batch.id))
                    old = existing.get(key, {})
                    try: old_qty = int(old.get('qty', -999))
                    except (ValueError, TypeError): old_qty = -999
                    last_sync = now if old_qty != batch.qty else (old.get('last_sync') or now)
                    rows.append([
                        item.name, brand.name, spec.name, batch.id,
                        batch.qty,
                        batch.expiry_date.isoformat() if batch.expiry_date else '',
                        float(batch.cost_price) if batch.cost_price else '',
                        brand.safe_qty,
                        item.supplier or '',
                        item.category.name if item.category else '',
                        last_sync,
                    ])
    ws.clear(); ws.update('A1', rows)
    return f'已寫入 {len(rows)-1} 筆'


# ── Append log ────────────────────────────────────────────
def append_log_row(batch, change, reason, username):
    ws  = _sheet('異動紀錄')
    now = now_tw().strftime('%Y-%m-%d %H:%M:%S')
    spec  = batch.spec
    brand = spec.brand
    item  = brand.item
    if ws.row_count < 1 or ws.cell(1,1).value != '時間':
        ws.insert_row(['時間','品項','品牌','規格','批次到期日','異動','理由','進價','操作人'], index=1)
    ws.append_row([
        now, item.name, brand.name, spec.name,
        batch.expiry_date.isoformat() if batch.expiry_date else '',
        f'+{change}' if change > 0 else str(change),
        reason,
        float(batch.cost_price) if batch.cost_price else '',
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
