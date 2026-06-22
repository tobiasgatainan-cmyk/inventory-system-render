from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user, AnonymousUserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timezone, timedelta
TZ_TAIPEI = timezone(timedelta(hours=8))

def now_tw():
    return datetime.now(TZ_TAIPEI).replace(tzinfo=None)
import os
import json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')

# ── Database ──────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///inventory.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '請先登入'
login_manager.login_message_category = 'info'

# Allow anonymous access — only routes with @login_required are protected
@login_manager.unauthorized_handler
def unauthorized():
    from flask import request as req
    # Only redirect to login for admin routes
    if req.path.startswith('/admin') or req.path.startswith('/stock'):
        return redirect(url_for('login', next=req.path))
    return redirect(url_for('login'))

# ── Models ────────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email    = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    role     = db.Column(db.String(20), default='viewer')  # admin / editor / viewer
    created_at = db.Column(db.DateTime, default=now_tw)

    def is_admin(self):
        return self.role == 'admin'

    def can_edit(self):
        return self.role in ('admin', 'editor')


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
    specs       = db.relationship('Spec', backref='item', lazy=True, cascade='all, delete-orphan')


class Spec(db.Model):
    __tablename__ = 'specs'
    id         = db.Column(db.Integer, primary_key=True)
    item_id    = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    name       = db.Column(db.String(100))
    qty        = db.Column(db.Integer, default=0)
    safe_qty   = db.Column(db.Integer, default=0)


class StockLog(db.Model):
    __tablename__ = 'stock_logs'
    id         = db.Column(db.Integer, primary_key=True)
    spec_id    = db.Column(db.Integer, db.ForeignKey('specs.id'))
    change     = db.Column(db.Integer)          # +入庫 / -出庫
    reason     = db.Column(db.String(200))
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=now_tw)
    user       = db.relationship('User', backref='logs')
    spec       = db.relationship('Spec', backref='logs')


# ── Helpers ───────────────────────────────────────────────
class AnonymousUser(AnonymousUserMixin):
    def is_admin(self):
        return False
    def can_edit(self):
        return False
    username = ''
    role = ''

login_manager.anonymous_user = AnonymousUser

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
    """Insert sample data if DB is empty."""
    if User.query.first():
        return
    admin = User(username='admin', email='admin@example.com',
                 password=generate_password_hash('admin1234'), role='admin')
    db.session.add(admin)

    cats = ['文具', '清潔', '電腦設備', '茶水間']
    cat_objs = {}
    for c in cats:
        obj = Category(name=c)
        db.session.add(obj)
        cat_objs[c] = obj
    db.session.flush()

    sample = [
        ('A4 影印紙', '包', 'ABC紙業', '文具',  [('白色 80g', 24, 5)]),
        ('Kokuyo 膠帶', '捲', 'OO批發', '文具',  [('透明', 3, 5)]),
        ('黑色原子筆', '盒', 'XYZ文具', '文具',  [('0.5mm', 0, 3)]),
        ('75% 酒精噴劑', '瓶', '清潔用品廠', '清潔', [('500ml', 8, 4)]),
        ('濕紙巾', '包', '日用品店', '清潔', [('一般型', 12, 3)]),
        ('無線滑鼠', '個', '電子產品城', '電腦設備', [('黑色', 2, 1)]),
        ('USB-C 集線器', '個', '電子商城', '電腦設備', [('4 port', 0, 2)]),
        ('咖啡膠囊', '顆', '咖啡商', '茶水間', [('深焙', 45, 20)]),
        ('紙杯', '包', '飲料用品', '茶水間', [('標準', 2, 5)]),
        ('訂書針', '盒', '文具批發', '文具', [('10號', 7, 2)]),
    ]
    for name, unit, supplier, cat, specs in sample:
        item = Item(name=name, unit=unit, supplier=supplier,
                    category=cat_objs[cat])
        db.session.add(item)
        db.session.flush()
        for sname, qty, safe in specs:
            db.session.add(Spec(item_id=item.id, name=sname, qty=qty, safe_qty=safe))
    db.session.commit()


# ── Auth routes ───────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user, remember=True)
            return redirect(request.args.get('next') or url_for('index'))
        flash('帳號或密碼錯誤', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ── Main inventory view (public) ─────────────────────────
@app.route('/')
def index():
    q    = request.args.get('q', '')
    cat  = request.args.get('cat', '')
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    query = Item.query
    if q:
        query = query.filter(Item.name.ilike(f'%{q}%'))
    if cat:
        c = Category.query.filter_by(name=cat).first()
        if c:
            query = query.filter_by(category_id=c.id)
    items = query.join(Category, Item.category_id == Category.id, isouter=True)\
                 .order_by(Category.sort_order, Category.name, Item.sort_order, Item.name).all()
    return render_template('index.html', items=items, cats=cats, q=q, selected_cat=cat)


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
            u = User(username=request.form['username'],
                     email=request.form['email'],
                     password=generate_password_hash(request.form['password']),
                     role=request.form['role'])
            db.session.add(u)
            db.session.commit()
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
        db.session.commit()
        flash('更新成功', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', user=u)


@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(uid):
    u = User.query.get_or_404(uid)
    if u.id == current_user.id:
        flash('無法刪除自己', 'danger')
    else:
        db.session.delete(u)
        db.session.commit()
        flash('已刪除', 'success')
    return redirect(url_for('admin_users'))


# ── Admin: Items CRUD ─────────────────────────────────────
@app.route('/admin/items')
@login_required
@editor_required
def admin_items():
    items       = Item.query.join(Category, Item.category_id == Category.id, isouter=True)\
                            .order_by(Category.sort_order, Category.name, Item.sort_order, Item.name).all()
    cats        = Category.query.order_by(Category.sort_order, Category.name).all()
    recent_logs = StockLog.query.order_by(StockLog.created_at.desc()).limit(8).all()
    all_logs    = StockLog.query.order_by(StockLog.created_at.desc()).limit(200).all()
    return render_template('admin/items.html', items=items, cats=cats,
                           recent_logs=recent_logs, all_logs=all_logs)


@app.route('/admin/items/add', methods=['GET', 'POST'])
@login_required
@editor_required
def admin_add_item():
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    if request.method == 'POST':
        item = Item(
            name=request.form['name'],
            unit=request.form['unit'],
            supplier=request.form['supplier'],
            category_id=int(request.form['category_id']) if request.form['category_id'] else None
        )
        db.session.add(item)
        db.session.flush()
        spec_names = request.form.getlist('spec_name[]')
        spec_qtys  = request.form.getlist('spec_qty[]')
        spec_safes = request.form.getlist('spec_safe[]')
        for sn, sq, ss in zip(spec_names, spec_qtys, spec_safes):
            if sn.strip():
                db.session.add(Spec(item_id=item.id, name=sn,
                                    qty=int(sq or 0), safe_qty=int(ss or 0)))
        db.session.commit()
        flash('品項新增成功', 'success')
        return redirect(url_for('admin_items'))
    return render_template('admin/item_form.html', item=None, cats=cats)


@app.route('/admin/items/<int:iid>/edit', methods=['GET', 'POST'])
@login_required
@editor_required
def admin_edit_item(iid):
    item = Item.query.get_or_404(iid)
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    if request.method == 'POST':
        item.name        = request.form['name']
        item.unit        = request.form['unit']
        item.supplier    = request.form['supplier']
        item.category_id = int(request.form['category_id']) if request.form['category_id'] else None

        # rebuild specs
        for s in item.specs:
            db.session.delete(s)
        db.session.flush()
        spec_names = request.form.getlist('spec_name[]')
        spec_qtys  = request.form.getlist('spec_qty[]')
        spec_safes = request.form.getlist('spec_safe[]')
        for sn, sq, ss in zip(spec_names, spec_qtys, spec_safes):
            if sn.strip():
                db.session.add(Spec(item_id=item.id, name=sn,
                                    qty=int(sq or 0), safe_qty=int(ss or 0)))
        db.session.commit()
        flash('更新成功', 'success')
        return redirect(url_for('admin_items'))
    return render_template('admin/item_form.html', item=item, cats=cats)


@app.route('/admin/items/<int:iid>/delete', methods=['POST'])
@login_required
@editor_required
def admin_delete_item(iid):
    item = Item.query.get_or_404(iid)
    db.session.delete(item)
    db.session.commit()
    flash('已刪除', 'success')
    return redirect(url_for('admin_items'))


# ── Stock in/out ──────────────────────────────────────────
@app.route('/stock/adjust', methods=['POST'])
@login_required
@editor_required
def stock_adjust():
    spec_id = int(request.form['spec_id'])
    change  = int(request.form['change'])
    reason  = request.form.get('reason', '')
    spec    = Spec.query.get_or_404(spec_id)
    spec.qty += change
    log = StockLog(spec_id=spec_id, change=change,
                   reason=reason, user_id=current_user.id)
    db.session.add(log)
    db.session.commit()

    # Optional: sync to Google Sheet
    try:
        from gsheet import append_log_row
        append_log_row(spec, change, reason, current_user.username)
    except Exception:
        pass

    flash(f'庫存已更新（{"+" if change > 0 else ""}{change}）', 'success')
    return redirect(request.referrer or url_for('index'))


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
    data = request.get_json()
    for entry in data:
        item = Item.query.get(entry['id'])
        if item:
            item.sort_order = entry['order']
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/admin/sort/categories', methods=['POST'])
@login_required
@editor_required
def sort_categories():
    data = request.get_json()
    for entry in data:
        cat = Category.query.get(entry['id'])
        if cat:
            cat.sort_order = entry['order']
    db.session.commit()
    return jsonify({'ok': True})


# ── Google Sheet sync ─────────────────────────────────────
@app.route('/admin/gsheet/sync', methods=['POST'])
@login_required
@admin_required
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
@admin_required
def gsheet_import():
    try:
        from gsheet import import_from_sheet
        result = import_from_sheet()
        flash(f'從 Sheet 匯入完成：{result}', 'success')
    except Exception as e:
        flash(f'匯入失敗：{e}', 'danger')
    return redirect(url_for('admin_items'))


# ── API: low-stock check ──────────────────────────────────
@app.route('/api/low-stock')
@login_required
def api_low_stock():
    low = []
    for spec in Spec.query.all():
        if spec.qty <= spec.safe_qty:
            low.append({
                'item': spec.item.name,
                'spec': spec.name,
                'qty': spec.qty,
                'safe': spec.safe_qty
            })
    return jsonify(low)


# ── Bootstrap ─────────────────────────────────────────────
with app.app_context():
    db.create_all()
    # Migration: add sort_order columns if not exist
    with db.engine.connect() as conn:
        from sqlalchemy import text
        for sql in [
            "ALTER TABLE categories ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
            "ALTER TABLE items ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()
    seed_data()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
