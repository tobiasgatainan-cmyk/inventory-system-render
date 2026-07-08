from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user, AnonymousUserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timezone, timedelta, date as date_type
import os
import json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')

TZ_TAIPEI = timezone(timedelta(hours=8))
def now_tw():
    return datetime.now(TZ_TAIPEI).replace(tzinfo=None)

# ── Database ──────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///inventory.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Render 免費版 PostgreSQL 同時連線數很少，這裡限制連線池大小並在
# 連線失效時自動回收，避免多分頁/多請求同時發生時把連線佔滿導致
# /api/... 這類次要查詢逾時或失敗。
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 3,
    'max_overflow': 2,
    'pool_recycle': 280,
    'pool_pre_ping': True,
    'pool_timeout': 10,
}

db = SQLAlchemy(app)

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '請先登入'
login_manager.login_message_category = 'info'

@login_manager.unauthorized_handler
def unauthorized():
    from flask import request as req
    if req.path.startswith('/admin') or req.path.startswith('/stock'):
        return redirect(url_for('login', next=req.path))
    return redirect(url_for('login'))

# ── Models ────────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password      = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20), default='viewer')
    notify_email  = db.Column(db.String(120), nullable=True)   # 通知信箱
    notify_on     = db.Column(db.Boolean, default=False)       # 是否接收通知
    is_active     = db.Column(db.Boolean, default=True)        # 是否啟用（停用後無法登入）
    created_at    = db.Column(db.DateTime, default=now_tw)

    def is_admin(self):  return self.role == 'admin'
    def can_edit(self):  return self.role in ('admin', 'editor')


class AnonymousUser(AnonymousUserMixin):
    def is_admin(self):  return False
    def can_edit(self):  return False
    username = ''; role = ''

login_manager.anonymous_user = AnonymousUser

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Category(db.Model):
    __tablename__ = 'categories'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(50), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    items      = db.relationship('Item', backref='category', lazy=True)


class Item(db.Model):
    __tablename__ = 'items'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    unit        = db.Column(db.String(20))
    supplier    = db.Column(db.String(100))
    sort_order  = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    created_at  = db.Column(db.DateTime, default=now_tw)
    brands      = db.relationship('Brand', backref='item', lazy=True, cascade='all, delete-orphan')

    @property
    def total_qty(self):
        return sum(b.total_qty for b in self.brands)

    @property
    def status(self):
        """ok / low / out"""
        total = self.total_qty
        safe  = sum(b.safe_qty for b in self.brands)
        if total == 0: return 'out'
        if total <= safe: return 'low'
        return 'ok'


class Brand(db.Model):
    __tablename__ = 'brands'
    id         = db.Column(db.Integer, primary_key=True)
    item_id    = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    name       = db.Column(db.String(100), nullable=False)
    safe_qty   = db.Column(db.Integer, default=0)
    sort_order = db.Column(db.Integer, default=0)
    specs      = db.relationship('Spec', backref='brand', lazy=True, cascade='all, delete-orphan')

    @property
    def total_qty(self):
        return sum(s.total_qty for s in self.specs)


class Spec(db.Model):
    __tablename__ = 'specs'
    id         = db.Column(db.Integer, primary_key=True)
    brand_id   = db.Column(db.Integer, db.ForeignKey('brands.id'), nullable=False)
    name       = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0)
    batches    = db.relationship('Batch', backref='spec', lazy=True, cascade='all, delete-orphan')

    @property
    def total_qty(self):
        return sum(b.qty for b in self.batches)

    @property
    def status(self):
        total = self.total_qty
        safe  = self.brand.safe_qty if self.brand else 0
        if total == 0: return 'out'
        if total <= safe: return 'low'
        return 'ok'


class Batch(db.Model):
    __tablename__ = 'batches'
    id          = db.Column(db.Integer, primary_key=True)
    spec_id     = db.Column(db.Integer, db.ForeignKey('specs.id'), nullable=False)
    qty         = db.Column(db.Integer, default=0)
    expiry_date = db.Column(db.Date, nullable=True)
    cost_price  = db.Column(db.Numeric(10, 2), nullable=True)
    supplier    = db.Column(db.String(100), nullable=True)
    note        = db.Column(db.String(200), nullable=True)
    created_at  = db.Column(db.DateTime, default=now_tw)

    @property
    def reserved_qty(self):
        """圈存數量：pending 申請單中已預留的數量"""
        return db.session.query(
            db.func.coalesce(db.func.sum(OrderItem.qty_request), 0)
        ).join(Order).filter(
            OrderItem.batch_id == self.id,
            Order.status == 'pending'
        ).scalar()

    @property
    def available_qty(self):
        return max(0, self.qty - self.reserved_qty)


class StockLog(db.Model):
    __tablename__ = 'stock_logs'
    id         = db.Column(db.Integer, primary_key=True)
    batch_id   = db.Column(db.Integer, db.ForeignKey('batches.id'))
    change     = db.Column(db.Integer)
    applicant  = db.Column(db.String(80))    # 申請人
    reason     = db.Column(db.String(200))   # 原因
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=now_tw)
    user       = db.relationship('User', backref='logs')
    batch      = db.relationship('Batch', backref='logs')


class ShortageRequest(db.Model):
    """前台申請人回報「缺貨」或「找不到需要的品項」"""
    __tablename__ = 'shortage_requests'
    id         = db.Column(db.Integer, primary_key=True)
    item_name  = db.Column(db.String(120), nullable=False)   # 想要的品項名稱
    note       = db.Column(db.String(300), nullable=True)    # 說明／用途
    applicant  = db.Column(db.String(80), nullable=False)    # 申請人
    resolved   = db.Column(db.Boolean, default=False)        # 是否已處理
    handle_note = db.Column(db.String(300), nullable=True)   # 處理備註（如何處理）
    created_at = db.Column(db.DateTime, default=now_tw)


class Order(db.Model):
    __tablename__ = 'orders'
    id           = db.Column(db.Integer, primary_key=True)
    order_no     = db.Column(db.String(20), unique=True, nullable=False)
    applicant    = db.Column(db.String(80), nullable=False)   # 申請人姓名
    status       = db.Column(db.String(20), default='pending')
    # pending=待處理 / confirmed=已出貨 / cancelled=已取消
    note         = db.Column(db.String(300), nullable=True)
    admin_note   = db.Column(db.String(300), nullable=True)   # 出貨調整說明／取消原因
    created_at   = db.Column(db.DateTime, default=now_tw)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    confirmed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    items        = db.relationship('OrderItem', backref='order', lazy=True,
                                   cascade='all, delete-orphan')
    confirmer    = db.relationship('User', foreign_keys=[confirmed_by])


class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id          = db.Column(db.Integer, primary_key=True)
    order_id    = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    batch_id    = db.Column(db.Integer, db.ForeignKey('batches.id'), nullable=False)
    qty_request = db.Column(db.Integer, nullable=False)   # 申請數量
    qty_actual  = db.Column(db.Integer, nullable=True)    # 實際出貨數量（後台可調整）
    batch       = db.relationship('Batch', foreign_keys=[batch_id], backref='order_items')

    @property
    def item_name(self):
        return self.batch.spec.brand.item.name if self.batch else '—'

    @property
    def brand_name(self):
        return self.batch.spec.brand.name if self.batch else '—'

    @property
    def spec_name(self):
        return self.batch.spec.name if self.batch else '—'

    @property
    def expiry_str(self):
        return self.batch.expiry_date.isoformat() if self.batch and self.batch.expiry_date else '—'


class OrderItemSplit(db.Model):
    """單一批次庫存不夠時，可從多個批次補足差額，一個申請品項可以有多筆補足批次"""
    __tablename__ = 'order_item_splits'
    id            = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey('order_items.id'), nullable=False)
    batch_id      = db.Column(db.Integer, db.ForeignKey('batches.id'), nullable=False)
    qty           = db.Column(db.Integer, nullable=False)
    order_item    = db.relationship('OrderItem', backref=db.backref('splits', cascade='all, delete-orphan'))
    batch         = db.relationship('Batch', foreign_keys=[batch_id])


# ── Helpers ───────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('需要管理員權限', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def editor_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_edit():
            flash('需要編輯權限', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def seed_data():
    if User.query.first(): return
    admin = User(username='admin', email='admin@example.com',
                 password=generate_password_hash('admin1234'), role='admin')
    db.session.add(admin)

    cats_data = ['文具', '清潔', '電腦設備', '茶水間']
    cat_objs = {}
    for i, c in enumerate(cats_data):
        obj = Category(name=c, sort_order=i)
        db.session.add(obj); cat_objs[c] = obj
    db.session.flush()

    sample = [
        ('A4 影印紙',    '包', 'ABC紙業',   '文具',   [('ABC紙業', [('白色 80g', 24)], 5)]),
        ('Kokuyo 膠帶',  '捲', 'OO批發',    '文具',   [('Kokuyo',  [('透明',    3 )], 5)]),
        ('黑色原子筆',   '盒', 'XYZ文具',   '文具',   [('斑馬',    [('0.5mm',   0 )], 3)]),
        ('75% 酒精噴劑', '瓶', '清潔用品廠', '清潔',  [('金門',    [('500ml',   8 )], 4)]),
        ('濕紙巾',       '包', '日用品店',  '清潔',   [('舒潔',    [('一般型',  12)], 3)]),
        ('無線滑鼠',     '個', '電子產品城', '電腦設備',[('羅技',   [('黑色',    2 )], 1)]),
        ('USB-C 集線器', '個', '電子商城',  '電腦設備',[('Anker',   [('4 port',  0 )], 2)]),
        ('咖啡膠囊',     '顆', '咖啡商',    '茶水間', [('Nespresso',[('深焙',   45)], 20)]),
        ('紙杯',         '包', '飲料用品',  '茶水間', [('大林',    [('標準',    2 )], 5)]),
        ('訂書針',       '盒', '文具批發',  '文具',   [('美克司',  [('10號',    7 )], 2)]),
    ]
    for name, unit, supplier, cat, brands in sample:
        item = Item(name=name, unit=unit, supplier=supplier, category=cat_objs[cat])
        db.session.add(item); db.session.flush()
        for bi, (bname, specs, safe) in enumerate(brands):
            brand = Brand(item_id=item.id, name=bname, safe_qty=safe, sort_order=bi)
            db.session.add(brand); db.session.flush()
            for si, (sname, qty) in enumerate(specs):
                spec = Spec(brand_id=brand.id, name=sname, sort_order=si)
                db.session.add(spec); db.session.flush()
                batch = Batch(spec_id=spec.id, qty=qty)
                db.session.add(batch)
    db.session.commit()


# ── Auth ──────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and not user.is_active:
            flash('此帳號已被停用，請聯絡管理員', 'danger')
        elif user and check_password_hash(user.password, request.form['password']):
            login_user(user, remember=True)
            return redirect(request.args.get('next') or url_for('index'))
        else:
            flash('帳號或密碼錯誤', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ── Main inventory view ───────────────────────────────────
@app.route('/')
def index():
    q    = request.args.get('q', '')
    cat  = request.args.get('cat', '')
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    query = Item.query.join(Category, Item.category_id == Category.id, isouter=True)
    if q:
        query = query.filter(Item.name.ilike(f'%{q}%'))
    if cat:
        c = Category.query.filter_by(name=cat).first()
        if c:
            query = query.filter(Item.category_id == c.id)
    items = query.order_by(Category.sort_order, Category.name, Item.sort_order, Item.name).all()
    today    = now_tw().date()
    today_30 = today + timedelta(days=30)
    return render_template('index.html', items=items, cats=cats, q=q,
                           selected_cat=cat, today=today, today_30=today_30)


# ── API: item detail for card expand ─────────────────────
@app.route('/api/item/<int:iid>')
def api_item_detail(iid):
    try:
        item  = Item.query.get_or_404(iid)
        today = now_tw().date()
        result = []
        for brand in item.brands:
            for spec in brand.specs:
                for batch in spec.batches:
                    exp = batch.expiry_date.isoformat() if batch.expiry_date else None
                    days_left = (batch.expiry_date - today).days if batch.expiry_date else None
                    result.append({
                        'batch_id':    batch.id,
                        'brand':       brand.name,
                        'spec':        spec.name,
                        'qty':         batch.qty,
                        'expiry_date': exp,
                        'days_left':   days_left,
                        'note':        batch.note or '',
                        'unit':        item.unit,
                    })
        return jsonify({'item': item.name, 'unit': item.unit, 'batches': result})
    except Exception as e:
        db.session.rollback()
        app.logger.exception(f'api_item_detail failed for iid={iid}')
        return jsonify({'error': str(e)}), 500


# ── Admin: Users ──────────────────────────────────────────
@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_user():
    if request.method == 'POST':
        if User.query.filter_by(username=request.form['username']).first():
            flash('帳號已存在', 'danger')
        else:
            u = User(username=request.form['username'], email=request.form['email'],
                     password=generate_password_hash(request.form['password']),
                     role=request.form['role'])
            db.session.add(u); db.session.commit()
            flash('新增成功', 'success')
            return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', user=None)

@app.route('/admin/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_user(uid):
    u = User.query.get_or_404(uid)
    if request.method == 'POST':
        u.email = request.form['email']
        u.role  = request.form['role']
        if request.form.get('password'):
            u.password = generate_password_hash(request.form['password'])
        db.session.commit(); flash('更新成功', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', user=u)

@app.route('/admin/users/<int:uid>/toggle-active', methods=['POST'])
@login_required
@admin_required
def admin_toggle_user_active(uid):
    u = User.query.get_or_404(uid)
    if u.id == current_user.id:
        flash('無法停用自己', 'danger')
    else:
        u.is_active = not u.is_active
        db.session.commit()
        flash(f'已{"啟用" if u.is_active else "停用"}「{u.username}」', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(uid):
    u = User.query.get_or_404(uid)
    if u.id == current_user.id:
        flash('無法刪除自己', 'danger')
    else:
        try:
            # 刪除前先解除歷史紀錄的關聯（改成 NULL），避免外鍵限制擋下刪除
            StockLog.query.filter_by(user_id=u.id).update({'user_id': None})
            Order.query.filter_by(confirmed_by=u.id).update({'confirmed_by': None})
            db.session.delete(u)
            db.session.commit()
            flash('已刪除', 'success')
        except IntegrityError:
            db.session.rollback()
            flash(f'無法刪除「{u.username}」，請稍後再試或聯絡系統管理員。', 'danger')
    return redirect(url_for('admin_users'))


# ── Admin: Items ──────────────────────────────────────────
@app.route('/admin/items')
@login_required
@editor_required
def admin_items():
    items       = Item.query.join(Category, Item.category_id == Category.id, isouter=True)\
                            .order_by(Category.sort_order, Category.name, Item.sort_order, Item.name).all()
    cats        = Category.query.order_by(Category.sort_order, Category.name).all()
    recent_logs = StockLog.query.order_by(StockLog.created_at.desc()).limit(8).all()
    today       = now_tw().date()
    today_30    = today + timedelta(days=30)

    # 依「品牌名稱＋規格名稱」分組（避免同名品牌被誤判為不同項），
    # 同組內只要有任何一筆還有庫存，就只顯示有庫存的批次；
    # 若整組都是 0，才保留一筆當作預留紀錄。
    item_display_rows = {}
    for item in items:
        groups = {}      # key -> list of {'brand','spec','batch'}
        order  = []      # 保留第一次出現的順序
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
        item_display_rows[item.id] = rows

    return render_template('admin/items.html', items=items, cats=cats,
                           recent_logs=recent_logs,
                           today=today, today_30=today_30,
                           item_display_rows=item_display_rows)

@app.route('/admin/items/add', methods=['GET', 'POST'])
@login_required
@editor_required
def admin_add_item():
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    if request.method == 'POST':
        name = request.form['name'].strip()
        # 檢查是否已有同名品項，若有則沿用（避免重複建立）
        existing_item = Item.query.filter_by(name=name).first()
        if existing_item:
            item = existing_item
            if request.form.get('unit'):
                item.unit = request.form['unit']
            if request.form.get('category_id'):
                item.category_id = int(request.form['category_id'])
            flash_msg = f'「{name}」已存在，新增的品牌已加入該品項'
        else:
            item = Item(name=name, unit=request.form['unit'],
                        category_id=int(request.form['category_id']) if request.form['category_id'] else None)
            db.session.add(item); db.session.flush()
            flash_msg = '品項新增成功'
        _save_brands(item.id, request.form, is_edit=False)
        db.session.commit(); flash(flash_msg, 'success')
        return redirect(url_for('admin_items'))
    return render_template('admin/item_form.html', item=None, cats=cats,
                           all_item_names=[i.name for i in Item.query.all()])

@app.route('/admin/items/<int:iid>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
def admin_edit_item(iid):
    item = Item.query.get_or_404(iid)
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    if request.method == 'POST':
        item.name        = request.form['name']
        item.unit        = request.form['unit']
        item.category_id = int(request.form['category_id']) if request.form['category_id'] else None

        brand_ids     = request.form.getlist('brand_id[]')
        brand_names   = request.form.getlist('brand_name[]')
        brand_safes   = request.form.getlist('brand_safe[]')
        spec_ids      = request.form.getlist('spec_id[]')
        spec_names    = request.form.getlist('spec_name[]')
        brand_indices = request.form.getlist('spec_brand_index[]')

        # 品牌：有帶 brand_id 就原地更新名稱／安全庫存；沒有的話，
        # 同品項底下若已有同名品牌就沿用，否則才新增，避免產生重複資料
        brands_by_idx  = {}
        kept_brand_ids = set()
        for bi, (bid, bname, bsafe) in enumerate(zip(brand_ids, brand_names, brand_safes)):
            if not bname.strip(): continue
            bname = bname.strip()
            brand = Brand.query.get(int(bid)) if bid.strip() else None
            if brand and brand.item_id != item.id:
                brand = None  # 防呆：id 對不到這個品項就當作新的
            if not brand:
                brand = Brand.query.filter_by(item_id=item.id, name=bname).first()
            if brand:
                brand.name      = bname
                brand.safe_qty  = int(bsafe or 0)
                brand.sort_order = bi
            else:
                brand = Brand(item_id=item.id, name=bname, safe_qty=int(bsafe or 0), sort_order=bi)
                db.session.add(brand); db.session.flush()
            brands_by_idx[bi] = brand
            kept_brand_ids.add(brand.id)

        # 規格：邏輯相同，原地更新優先，其次同名沿用，最後才新增
        kept_spec_ids = set()
        for si, (sid, sname, bidx) in enumerate(zip(spec_ids, spec_names, brand_indices)):
            if not sname.strip(): continue
            sname = sname.strip()
            try: bidx = int(bidx)
            except (ValueError, TypeError): bidx = 0
            brand = brands_by_idx.get(bidx)
            if not brand: continue
            spec = Spec.query.get(int(sid)) if sid.strip() else None
            if spec and spec.brand_id not in kept_brand_ids:
                spec = None
            if not spec:
                spec = Spec.query.filter_by(brand_id=brand.id, name=sname).first()
            if spec:
                spec.name       = sname
                spec.brand_id   = brand.id
                spec.sort_order = si
            else:
                spec = Spec(brand_id=brand.id, name=sname, sort_order=si)
                db.session.add(spec); db.session.flush()
                db.session.add(Batch(spec_id=spec.id, qty=0))  # 新規格先給一筆空批次，等待入庫
            kept_spec_ids.add(spec.id)

        # 刪除使用者真的移除的品牌／規格；若底下還有批次紀錄（例如曾被申請單引用），
        # 為避免違反資料庫關聯限制，不刪除，改為提示
        blocked = []
        for brand in list(item.brands):
            if brand.id in kept_brand_ids:
                continue
            has_batches = any(spec.batches for spec in brand.specs)
            if has_batches:
                blocked.append(f'品牌「{brand.name}」')
            else:
                db.session.delete(brand)
        db.session.flush()

        for brand in item.brands:
            if brand.id not in kept_brand_ids:
                continue
            for spec in list(brand.specs):
                if spec.id in kept_spec_ids:
                    continue
                if spec.batches:
                    blocked.append(f'規格「{brand.name}／{spec.name}」')
                else:
                    db.session.delete(spec)

        db.session.commit()
        if blocked:
            flash('已更新，但以下項目仍有庫存批次紀錄（可能曾被申請單引用），未刪除：' + '、'.join(blocked), 'warning')
        else:
            flash('更新成功', 'success')
        return redirect(url_for('admin_items'))
    return render_template('admin/item_form.html', item=item, cats=cats)

def _save_brands(item_id, form, is_edit=False):
    brand_names    = form.getlist('brand_name[]')
    brand_safes    = form.getlist('brand_safe[]')
    spec_names     = form.getlist('spec_name[]')
    spec_qtys      = form.getlist('spec_qty[]')
    spec_expiries  = form.getlist('spec_expiry[]')
    spec_costs     = form.getlist('spec_cost[]')
    spec_suppliers = form.getlist('spec_supplier[]')
    spec_notes     = form.getlist('spec_note[]')
    brand_indices  = form.getlist('spec_brand_index[]')

    # 同一品項底下，同名品牌一律重複使用既有的（不管是這次表單裡重複輸入，
    # 還是資料庫裡本來就已經存在），避免產生重複品牌
    brands_created  = []
    brand_name_map  = {}
    for bi, (bname, bsafe) in enumerate(zip(brand_names, brand_safes)):
        if not bname.strip(): continue
        key = bname.strip()
        if key in brand_name_map:
            brand = brand_name_map[key]
        else:
            brand = Brand.query.filter_by(item_id=item_id, name=key).first()
            if not brand:
                brand = Brand(item_id=item_id, name=key,
                              safe_qty=int(bsafe or 0), sort_order=bi)
                db.session.add(brand); db.session.flush()
            brand_name_map[key] = brand
        brands_created.append(brand)

    # 同一品牌底下，同名規格也一律重複使用既有的，道理相同
    spec_name_map = {}
    for si, (sname, sqty, sexp, scost, ssup, snote, bidx) in enumerate(
            zip(spec_names, spec_qtys, spec_expiries, spec_costs, spec_suppliers, spec_notes, brand_indices)):
        if not sname.strip(): continue
        try: bidx = int(bidx)
        except (ValueError, TypeError): bidx = 0
        if bidx >= len(brands_created): continue
        brand = brands_created[bidx]
        skey = (brand.id, sname.strip())
        if skey in spec_name_map:
            spec = spec_name_map[skey]
        else:
            spec = Spec.query.filter_by(brand_id=brand.id, name=sname.strip()).first()
            if not spec:
                spec = Spec(brand_id=brand.id, name=sname.strip(), sort_order=si)
                db.session.add(spec); db.session.flush()
            spec_name_map[skey] = spec

        # 編輯模式下，只建立 spec，不建立新 batch（庫存由入庫管理）
        # 新增模式下，建立初始 batch
        if not is_edit:
            exp = None
            if sexp.strip():
                try: exp = date_type.fromisoformat(sexp.strip())
                except ValueError: pass
            cost = None
            if scost.strip():
                try: cost = float(scost.strip())
                except ValueError: pass
            qty = int(sqty or 0)
            batch = Batch(spec_id=spec.id, qty=qty,
                          expiry_date=exp, cost_price=cost,
                          supplier=ssup.strip() or None,
                          note=snote.strip() or None)
            db.session.add(batch)

@app.route('/admin/items/<int:iid>/delete', methods=['POST'])
@login_required
@editor_required
def admin_delete_item(iid):
    item = Item.query.get_or_404(iid)
    db.session.delete(item); db.session.commit(); flash('已刪除', 'success')
    return redirect(url_for('admin_items'))


# ── Stock in ──────────────────────────────────────────────
@app.route('/stock/in', methods=['POST'])
@login_required
@editor_required
def stock_in():
    spec_id  = int(request.form['spec_id'])
    qty      = int(request.form['qty'])
    reason   = request.form.get('reason', '')
    exp_str  = request.form.get('expiry_date', '').strip()
    cost_str = request.form.get('cost_price', '').strip()
    supplier = request.form.get('supplier', '').strip()
    note     = request.form.get('note', '').strip()

    spec = Spec.query.get_or_404(spec_id)

    exp = None
    if exp_str:
        try: exp = date_type.fromisoformat(exp_str)
        except ValueError: pass
    cost = None
    if cost_str:
        try: cost = float(cost_str)
        except ValueError: pass

    # 五個欄位全部相同才合併：到期日、進價、供應商、備註
    existing = None
    for b in spec.batches:
        same_exp  = b.expiry_date == exp
        same_cost = str(b.cost_price or '') == str(cost or '')
        same_sup  = (b.supplier or '') == (supplier or '')
        same_note = (b.note or '') == (note or '')
        if same_exp and same_cost and same_sup and same_note:
            existing = b; break

    if existing:
        existing.qty += qty
        if cost: existing.cost_price = cost
        if supplier: existing.supplier = supplier
        if note: existing.note = note
        batch = existing
    else:
        batch = Batch(spec_id=spec_id, qty=qty, expiry_date=exp,
                      cost_price=cost, supplier=supplier or None, note=note or None)
        db.session.add(batch); db.session.flush()

    log = StockLog(batch_id=batch.id, change=qty, reason=reason, user_id=current_user.id)
    db.session.add(log); db.session.commit()

    try:
        from gsheet import append_log_row, append_purchase_record
        append_log_row(batch, qty, reason, current_user.username)
        append_purchase_record(batch, current_user.username)
    except Exception: pass
    return redirect(request.referrer or url_for('admin_items'))


# ── Stock out ─────────────────────────────────────────────
@app.route('/stock/out', methods=['POST'])
@login_required
@editor_required
def stock_out():
    batch_id  = int(request.form['batch_id'])
    qty       = int(request.form['qty'])
    applicant = request.form.get('applicant', '').strip()
    reason    = request.form.get('reason', '').strip()

    batch = Batch.query.get_or_404(batch_id)
    if qty > batch.qty:
        flash(f'出庫數量不可超過現有庫存（{batch.qty}）', 'danger')
        return redirect(request.referrer or url_for('admin_items'))

    batch.qty -= qty
    if batch.qty == 0:
        batch.expiry_date = None
        batch.cost_price  = None
        batch.note        = None
    log = StockLog(batch_id=batch_id, change=-qty, applicant=applicant, reason=reason, user_id=current_user.id)
    db.session.add(log); db.session.commit()

    try:
        from gsheet import append_log_row
        append_log_row(batch, -qty, reason, current_user.username, applicant=applicant)
    except Exception: pass

    flash(f'出庫成功（-{qty}）', 'success')
    return redirect(request.referrer or url_for('admin_items'))


# ── API for dropdowns ─────────────────────────────────────
@app.route('/api/items_by_cat/<int:cat_id>')
@login_required
def api_items_by_cat(cat_id):
    items = Item.query.filter_by(category_id=cat_id)\
                      .order_by(Item.sort_order, Item.name).all()
    return jsonify([{'id': i.id, 'name': i.name} for i in items])

@app.route('/api/brands_by_item/<int:item_id>')
@login_required
def api_brands_by_item(item_id):
    brands = Brand.query.filter_by(item_id=item_id)\
                        .order_by(Brand.sort_order, Brand.name).all()
    return jsonify([{'id': b.id, 'name': b.name} for b in brands])

@app.route('/api/specs_by_brand/<int:brand_id>')
@login_required
def api_specs_by_brand(brand_id):
    specs = Spec.query.filter_by(brand_id=brand_id)\
                      .order_by(Spec.sort_order, Spec.name).all()
    return jsonify([{'id': s.id, 'name': s.name, 'total_qty': s.total_qty} for s in specs])

@app.route('/api/batches_by_spec/<int:spec_id>')
@login_required
def api_batches_by_spec(spec_id):
    spec    = Spec.query.get_or_404(spec_id)
    today   = now_tw().date()
    batches = []
    for b in spec.batches:
        exp       = b.expiry_date.isoformat() if b.expiry_date else None
        days_left = (b.expiry_date - today).days if b.expiry_date else None
        batches.append({'id': b.id, 'qty': b.qty, 'expiry_date': exp,
                        'days_left': days_left, 'note': b.note or ''})
    return jsonify(batches)


# ── Admin: Logs ───────────────────────────────────────────
@app.route('/admin/logs')
@login_required
@editor_required
def admin_logs():
    logs = StockLog.query.order_by(StockLog.created_at.desc()).limit(200).all()
    return render_template('admin/logs.html', logs=logs)


# ── Sort order ────────────────────────────────────────────
@app.route('/admin/sort/items', methods=['POST'])
@login_required
@editor_required
def sort_items():
    for entry in request.get_json():
        item = Item.query.get(entry['id'])
        if item: item.sort_order = entry['order']
    db.session.commit(); return jsonify({'ok': True})

@app.route('/admin/sort/categories', methods=['POST'])
@login_required
@editor_required
def sort_categories():
    for entry in request.get_json():
        cat = Category.query.get(entry['id'])
        if cat: cat.sort_order = entry['order']
    db.session.commit(); return jsonify({'ok': True})


# ── Cart / Order (public) ─────────────────────────────────
@app.route('/cart')
def cart():
    cart = session.get('cart', [])
    today = now_tw().date()
    today_30 = today + timedelta(days=30)
    items_detail = []
    for entry in cart:
        batch = Batch.query.get(entry['batch_id'])
        if batch:
            items_detail.append({
                'batch_id':   batch.id,
                'item_name':  batch.spec.brand.item.name,
                'brand_name': batch.spec.brand.name,
                'spec_name':  batch.spec.name,
                'unit':       batch.spec.brand.item.unit,
                'expiry':     batch.expiry_date.isoformat() if batch.expiry_date else '—',
                'available':  batch.available_qty,
                'qty':        entry['qty'],
            })
    return render_template('cart.html', cart=items_detail, today=today, today_30=today_30)


@app.route('/report_shortage', methods=['POST'])
def report_shortage():
    data      = request.get_json(silent=True) or request.form
    item_name = (data.get('item_name') or '').strip()
    applicant = (data.get('applicant') or '').strip()
    note      = (data.get('note') or '').strip()
    if not item_name or not applicant:
        return jsonify({'ok': False, 'error': '請填寫品項名稱與申請人'}), 400

    req = ShortageRequest(item_name=item_name, applicant=applicant, note=note)
    db.session.add(req); db.session.commit()

    try:
        from notify import send_shortage_notify
        send_shortage_notify(req)
    except Exception as e:
        app.logger.warning(f'send_shortage_notify failed: {e}')

    return jsonify({'ok': True})


@app.route('/admin/shortage_requests')
@login_required
@editor_required
def admin_shortage_requests():
    return redirect(url_for('admin_orders', view='shortage'))


@app.route('/admin/shortage_requests/<int:rid>/toggle', methods=['POST'])
@login_required
@editor_required
def admin_toggle_shortage(rid):
    req = ShortageRequest.query.get_or_404(rid)
    note = request.form.get('handle_note', '').strip()
    if note:
        req.handle_note = note
    req.resolved = not req.resolved
    db.session.commit()
    return redirect(url_for('admin_orders', view='shortage',
                            sr_status='resolved' if req.resolved else 'pending'))


@app.route('/admin/shortage_requests/<int:rid>/note', methods=['POST'])
@login_required
@editor_required
def admin_shortage_note(rid):
    req = ShortageRequest.query.get_or_404(rid)
    req.handle_note = request.form.get('handle_note', '').strip() or None
    db.session.commit()
    flash('備註已儲存', 'success')
    return redirect(url_for('admin_orders', view='shortage',
                            sr_status='resolved' if req.resolved else 'pending'))


@app.route('/basket/append', methods=['POST'])
@app.route('/cart/add', methods=['POST'])  # 保留舊路徑相容，但前端已改用 /basket/append
def cart_add():
    data     = request.get_json()
    item_id  = data.get('item_id')
    qty      = int(data.get('qty', 1))
    brand_filter = data.get('brand', None)
    spec_filter  = data.get('spec', None)

    item = Item.query.get(item_id)
    if not item:
        return jsonify({'ok': False, 'msg': '品項不存在'})

    # 收集符合條件的批次，依到期日排序（FEFO）
    all_batches = []
    for brand in item.brands:
        if brand_filter and brand.name != brand_filter: continue
        for spec in brand.specs:
            if spec_filter and spec.name != spec_filter: continue
            for batch in spec.batches:
                if batch.available_qty > 0:
                    all_batches.append(batch)
    all_batches.sort(key=lambda b: (
        b.expiry_date is None,
        b.expiry_date or date_type(9999,12,31)
    ))

    if not all_batches:
        total_available = sum(
            b.available_qty for brand in item.brands
            for spec in brand.specs for b in spec.batches
        ) if not (brand_filter or spec_filter) else 0
        return jsonify({'ok': False, 'insufficient': True,
                        'available': 0, 'msg': '目前無庫存'})

    total_available = sum(b.available_qty for b in all_batches)
    if qty > total_available:
        return jsonify({'ok': False, 'insufficient': True,
                        'available': total_available,
                        'msg': f'庫存不足，目前可申請 {total_available} 個'})

    cart = session.get('cart', [])
    remaining = qty
    for batch in all_batches:
        if remaining <= 0: break
        take = min(remaining, batch.available_qty)
        found = False
        for entry in cart:
            if entry['batch_id'] == batch.id:
                entry['qty'] += take; found = True; break
        if not found:
            cart.append({'batch_id': batch.id, 'qty': take})
        remaining -= take

    session['cart'] = cart
    session.modified = True
    return jsonify({'ok': True, 'cart_count': sum(e['qty'] for e in cart)})


@app.route('/api/cart-count')
def api_cart_count():
    cart = session.get('cart', [])
    return jsonify({'count': sum(e['qty'] for e in cart)})


@app.route('/cart/update', methods=['POST'])
def cart_update():
    data     = request.get_json()
    batch_id = data.get('batch_id')
    qty      = int(data.get('qty', 0))
    cart     = session.get('cart', [])

    batch = Batch.query.get(batch_id)
    max_qty = batch.available_qty if batch else 0
    if qty > max_qty:
        qty = max_qty

    if qty <= 0:
        cart = [e for e in cart if e['batch_id'] != batch_id]
    else:
        for entry in cart:
            if entry['batch_id'] == batch_id:
                entry['qty'] = qty
    session['cart'] = cart
    session.modified = True
    return jsonify({'ok': True, 'qty': qty, 'max': max_qty})


@app.route('/cart/clear', methods=['POST'])
def cart_clear():
    session['cart'] = []
    session.modified = True
    return jsonify({'ok': True})


@app.route('/order/submit', methods=['POST'])
def order_submit():
    applicant = request.form.get('applicant', '').strip()
    note      = request.form.get('note', '').strip()
    cart      = session.get('cart', [])

    if not applicant:
        flash('請填寫申請人姓名', 'danger')
        return redirect(url_for('cart'))
    if not cart:
        flash('購物車是空的', 'danger')
        return redirect(url_for('cart'))

    # 產生申請單號
    ts       = now_tw().strftime('%Y%m%d%H%M%S')
    order_no = f'ORD-{ts}'

    order = Order(order_no=order_no, applicant=applicant, note=note)
    db.session.add(order); db.session.flush()

    for entry in cart:
        batch = Batch.query.get(entry['batch_id'])
        if not batch: continue
        oi = OrderItem(order_id=order.id, batch_id=batch.id,
                       qty_request=entry['qty'], qty_actual=entry['qty'])
        db.session.add(oi)

    db.session.commit()
    session['cart'] = []
    session.modified = True

    # 寄信通知
    try:
        from notify import send_order_notify
        send_order_notify(order)
    except Exception: pass

    return redirect(url_for('order_confirm', order_no=order_no))


@app.route('/order/confirm/<order_no>')
def order_confirm(order_no):
    order = Order.query.filter_by(order_no=order_no).first_or_404()
    return render_template('order_confirm.html', order=order)


# ── Admin: Orders ──────────────────────────────────────────
@app.route('/admin/orders')
@login_required
@editor_required
def admin_orders():
    view      = request.args.get('view', 'orders')
    status    = request.args.get('status', 'pending')
    sr_status = request.args.get('sr_status', 'pending')
    pending_count = Order.query.filter_by(status='pending').count()
    shortage_pending_count = ShortageRequest.query.filter_by(resolved=False).count()
    orders = []
    shortage_reqs = []
    if view == 'shortage':
        shortage_reqs = ShortageRequest.query.filter_by(resolved=(sr_status == 'resolved'))\
                                             .order_by(ShortageRequest.created_at.desc()).all()
    else:
        orders = Order.query.filter_by(status=status)\
                            .order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders, status=status,
                           pending_count=pending_count, view=view,
                           shortage_reqs=shortage_reqs, sr_status=sr_status,
                           shortage_pending_count=shortage_pending_count)


@app.route('/admin/orders/<int:oid>')
@login_required
@editor_required
def admin_order_detail(oid):
    order  = Order.query.get_or_404(oid)
    today  = now_tw().date()
    # 取得每個品項可選的批次清單
    for oi in order.items:
        if not oi.batch:
            oi._available_batches = []
            oi._batch_options = []
            continue
        item = oi.batch.spec.brand.item
        # 找同品項下所有批次（依到期日排序）
        batches = []
        for brand in item.brands:
            for spec in brand.specs:
                for b in spec.batches:
                    if b.qty > 0:
                        batches.append(b)
        batches.sort(key=lambda b: (
            b.expiry_date is None,
            b.expiry_date or date_type(9999,12,31)
        ))
        oi._available_batches = batches
        oi._batch_options = []
        for b in batches:
            if b.expiry_date:
                days = (b.expiry_date - today).days
                exp_label = b.expiry_date.isoformat() + (
                    '（已過期）' if days < 0 else '（今天）' if days == 0 else f'（{days}天）')
            else:
                exp_label = '無'
            oi._batch_options.append({
                'id': b.id,
                'qty': b.qty,
                'label': f'{b.spec.brand.name}／{b.spec.name}／到期：{exp_label}／庫存 {b.qty}',
            })
    return render_template('admin/order_detail.html', order=order, today=today)


@app.route('/admin/orders/<int:oid>/update', methods=['POST'])
@login_required
@editor_required
def admin_order_update(oid):
    order = Order.query.get_or_404(oid)
    if order.status != 'pending':
        flash('此申請單已處理', 'danger')
        return redirect(url_for('admin_orders'))
    for oi in order.items:
        qty_key   = f'qty_{oi.id}'
        batch_key = f'batch_{oi.id}'
        if qty_key in request.form:
            oi.qty_actual = int(request.form[qty_key] or 0)
        if batch_key in request.form:
            oi.batch_id = int(request.form[batch_key])

        # 補足批次：先清空舊的，再依表單送來的多筆資料重建
        OrderItemSplit.query.filter_by(order_item_id=oi.id).delete()
        split_batches = request.form.getlist(f'split_batch_{oi.id}[]')
        split_qtys    = request.form.getlist(f'split_qty_{oi.id}[]')
        for sb, sq in zip(split_batches, split_qtys):
            sq = int(sq or 0)
            if sb and sq > 0:
                db.session.add(OrderItemSplit(order_item_id=oi.id, batch_id=int(sb), qty=sq))
    db.session.commit()
    flash('申請單已更新', 'success')
    return redirect(url_for('admin_order_detail', oid=oid))


@app.route('/admin/orders/<int:oid>/confirm', methods=['POST'])
@login_required
@editor_required
def admin_order_confirm(oid):
    order = Order.query.get_or_404(oid)
    if order.status != 'pending':
        flash('此申請單已處理', 'danger')
        return redirect(url_for('admin_orders'))

    order_reason = f'申請單 {order.order_no}' + (f'／{order.note}' if order.note else '')

    def deduct(item_name, batch_id, qty):
        """扣單一批次庫存並記錄異動,回傳錯誤訊息（無錯誤則為 None）"""
        if qty <= 0: return None
        batch = Batch.query.get(batch_id)
        if not batch: return None
        if qty > batch.qty:
            return f'{item_name} 庫存不足（現有 {batch.qty}，需求 {qty}）'
        batch.qty -= qty
        if batch.qty == 0:
            batch.expiry_date = None
            batch.cost_price  = None
            batch.note        = None
        log = StockLog(batch_id=batch.id, change=-qty,
                       applicant=order.applicant, reason=order_reason,
                       user_id=current_user.id)
        db.session.add(log)
        try:
            from gsheet import append_log_row
            append_log_row(batch, -qty, order_reason, current_user.username, applicant=order.applicant)
        except Exception: pass
        return None

    for oi in order.items:
        err = deduct(oi.item_name, oi.batch_id, oi.qty_actual or 0)
        if err:
            flash(err, 'danger')
            return redirect(url_for('admin_order_detail', oid=oid))
        for split in oi.splits:
            err = deduct(oi.item_name, split.batch_id, split.qty)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_order_detail', oid=oid))

    order.status       = 'confirmed'
    order.confirmed_at = now_tw()
    order.confirmed_by = current_user.id
    order.admin_note   = request.form.get('admin_note', '').strip() or None
    db.session.commit()
    flash(f'申請單 {order.order_no} 已確認出貨，庫存已扣除', 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/orders/<int:oid>/cancel', methods=['POST'])
@login_required
@editor_required
def admin_order_cancel(oid):
    order = Order.query.get_or_404(oid)
    order.status     = 'cancelled'
    order.admin_note = request.form.get('admin_note', '').strip() or None
    db.session.commit()
    flash('申請單已取消', 'success')
    return redirect(url_for('admin_orders'))


# ── Admin: User notify settings ───────────────────────────
@app.route('/admin/users/<int:uid>/notify', methods=['POST'])
@login_required
@admin_required
def admin_user_notify(uid):
    u = User.query.get_or_404(uid)
    old_email  = u.notify_email
    u.notify_on    = 'notify_on' in request.form
    u.notify_email = request.form.get('notify_email', '').strip() or None
    db.session.commit()
    # 寄測試信（信箱有變更時）
    if u.notify_email and u.notify_email != old_email:
        try:
            from notify import send_test_email
            send_test_email(u.notify_email, u.username)
            flash(f'設定已儲存，測試信已寄送至 {u.notify_email}', 'success')
        except Exception as e:
            flash(f'設定已儲存，但測試信寄送失敗：{e}', 'danger')
    else:
        flash('通知設定已儲存', 'success')
    return redirect(url_for('admin_users'))


# ── Google Sheet ──────────────────────────────────────────
@app.route('/admin/gsheet/sync', methods=['POST'])
@login_required
@editor_required
def gsheet_sync():
    try:
        from gsheet import full_sync
        result = full_sync()
        flash(f'Google Sheet 同步完成：{result}', 'success')
    except Exception as e:
        flash(f'同步失敗：{e}', 'danger')
    return redirect(url_for('admin_items'))

@app.route('/admin/gsheet/import', methods=['POST'])
@login_required
@editor_required
def gsheet_import():
    try:
        from gsheet import import_from_sheet
        result = import_from_sheet()
        flash(f'從 Sheet 匯入完成：{result}', 'success')
    except Exception as e:
        flash(f'匯入失敗：{e}', 'danger')
    return redirect(url_for('admin_items'))


# ── API: low-stock ────────────────────────────────────────
@app.route('/api/low-stock')
@login_required
def api_low_stock():
    low = []
    for item in Item.query.all():
        if item.status in ('low', 'out'):
            low.append({'item': item.name, 'qty': item.total_qty, 'status': item.status})
    return jsonify(low)


def merge_duplicates():
    """開機時自動合併資料清理：
    同一品項底下若有「名稱相同」的重複品牌，或同一品牌底下有「名稱相同」的重複規格，
    保留 ID 最小（最早建立）的一筆，把其餘的規格／批次搬過去後刪除重複的品牌／規格。
    這是可重複執行的安全操作：沒有重複資料時完全不會有任何變動。
    """
    from collections import defaultdict
    merged_brands = merged_specs = 0

    for item in Item.query.all():
        by_name = defaultdict(list)
        for b in Brand.query.filter_by(item_id=item.id).order_by(Brand.id).all():
            by_name[b.name].append(b)
        for name, blist in by_name.items():
            if len(blist) <= 1:
                continue
            keeper, dups = blist[0], blist[1:]
            for dup in dups:
                for spec in list(Spec.query.filter_by(brand_id=dup.id).all()):
                    spec.brand_id = keeper.id
                db.session.flush()
                db.session.delete(dup)
                merged_brands += 1
        db.session.flush()

        # 品牌內重複規格
        for brand in Brand.query.filter_by(item_id=item.id).all():
            by_spec_name = defaultdict(list)
            for s in Spec.query.filter_by(brand_id=brand.id).order_by(Spec.id).all():
                by_spec_name[s.name].append(s)
            for name, slist in by_spec_name.items():
                if len(slist) <= 1:
                    continue
                keeper, dups = slist[0], slist[1:]
                for dup in dups:
                    for batch in list(Batch.query.filter_by(spec_id=dup.id).all()):
                        batch.spec_id = keeper.id
                    db.session.flush()
                    db.session.delete(dup)
                    merged_specs += 1

    if merged_brands or merged_specs:
        db.session.commit()
        app.logger.info(f'merge_duplicates：合併品牌 {merged_brands} 筆、規格 {merged_specs} 筆')
    else:
        db.session.rollback()


# ── Bootstrap ─────────────────────────────────────────────
with app.app_context():
    # 支援強制重建（環境變數 FORCE_DB_RESET=1）
    force_reset = os.environ.get('FORCE_DB_RESET', '0') == '1'

    # 檢查是否需要重建
    with db.engine.connect() as conn:
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        has_batches  = 'batches' in existing_tables
        has_batch_id = False
        if 'stock_logs' in existing_tables:
            cols = [c['name'] for c in inspector.get_columns('stock_logs')]
            has_batch_id = 'batch_id' in cols
        needs_rebuild = force_reset or not has_batches or not has_batch_id

    if needs_rebuild:
        db.drop_all()
        db.create_all()
    else:
        db.create_all()
        with db.engine.connect() as conn:
            for sql in [
                "ALTER TABLE categories ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
                "ALTER TABLE items      ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
                "ALTER TABLE users      ADD COLUMN IF NOT EXISTS notify_email VARCHAR(120)",
                "ALTER TABLE users      ADD COLUMN IF NOT EXISTS notify_on BOOLEAN DEFAULT FALSE",
                "ALTER TABLE users      ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
                "ALTER TABLE batches    ADD COLUMN IF NOT EXISTS supplier VARCHAR(100)",
                "ALTER TABLE stock_logs ADD COLUMN IF NOT EXISTS applicant VARCHAR(80)",
                "ALTER TABLE orders     ADD COLUMN IF NOT EXISTS admin_note VARCHAR(300)",
                "ALTER TABLE order_items ADD COLUMN IF NOT EXISTS split_batch_id INTEGER",
                "ALTER TABLE order_items ADD COLUMN IF NOT EXISTS split_qty INTEGER",
                "ALTER TABLE shortage_requests ADD COLUMN IF NOT EXISTS handle_note VARCHAR(300)",
            ]:
                try: conn.execute(text(sql)); conn.commit()
                except Exception: conn.rollback()

    seed_data()
    merge_duplicates()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
