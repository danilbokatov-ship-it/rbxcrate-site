import os
import uuid
import requests
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///rbxcrate.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

RBXCRATE_API_KEY = os.environ.get('RBXCRATE_API_KEY', '')
RBXCRATE_BASE_URL = os.environ.get('RBXCRATE_BASE_URL', 'https://rbxcrate.com')
RBXCRATE_ORDER_ENDPOINT = '/api/orders/gamepass'
RBXCRATE_INFO_ENDPOINT = '/api/orders/info'

ROBLOX_USERS_API = "https://users.roblox.com/v1/usernames/users"
ROBLOX_GAMES_LIST_API = "https://games.roblox.com/v2/users/{user_id}/games"
ROBLOX_GAMES_INFO_API = "https://games.roblox.com/v1/games"

SKIN_LINKS = [
    "https://www.roblox.com/catalog/81873605841533/Dashie-Y2K-Snow-Goggles-White-Gray",
    "https://www.roblox.com/catalog/108960428758609/vkei",
    "https://www.roblox.com/catalog/88270342307378/vkei"
]

PASS_REWARD = 30
SKIN_REWARD = 10
RESERVE_TIMEOUT = timedelta(minutes=30)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_approved = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    balance = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    roblox_username = db.Column(db.String(80), nullable=False)
    roblox_password = db.Column(db.String(120), nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    used_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    reserved_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reserved_at = db.Column(db.DateTime, nullable=True)
    rbxcrate_completed_at = db.Column(db.DateTime, nullable=True)
    skin_done = db.Column(db.Boolean, default=False)
    skin_done_at = db.Column(db.DateTime, nullable=True)
    skin_done_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    skin_reserved_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    skin_reserved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product = db.relationship('Product', backref='orders', lazy=True)
    roblox_username = db.Column(db.String(80), nullable=False)
    place_id = db.Column(db.String(20), nullable=False)
    rbxcrate_order_id = db.Column(db.String(100), nullable=True)
    amount = db.Column(db.Integer, default=108)
    status = db.Column(db.String(20), default='pending')
    rbxcrate_status = db.Column(db.String(50), nullable=True)
    error_msg = db.Column(db.Text, nullable=True)
    reward_paid = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class RobloxAPIError(Exception):
    pass

class RobloxUserNotFoundError(Exception):
    pass


def get_user_id(username: str) -> int:
    resp = requests.post(
        ROBLOX_USERS_API,
        json={"usernames": [username], "excludeBannedUsers": False},
        timeout=10
    )
    if resp.status_code != 200:
        raise RobloxAPIError(f"HTTP {resp.status_code}")
    data = resp.json().get("data", [])
    if not data:
        raise RobloxUserNotFoundError(username)
    return int(data[0]["id"])


def get_user_games(user_id: int) -> list:
    games = []
    cursor = ""
    while True:
        resp = requests.get(
            ROBLOX_GAMES_LIST_API.format(user_id=user_id),
            params={
                "accessFilter": "Public",
                "limit": 50,
                "sortOrder": "Asc",
                "cursor": cursor or None,
            },
            timeout=10
        )
        if resp.status_code != 200:
            raise RobloxAPIError(f"HTTP {resp.status_code}")
        payload = resp.json()
        for item in payload.get("data", []):
            uid = item.get("id")
            if uid:
                games.append({"universe_id": int(uid), "name": item.get("name", "Без названия")})
        cursor = payload.get("nextPageCursor")
        if not cursor or len(games) >= 20:
            break
    return games[:20]


def get_root_place(universe_id: int):
    resp = requests.get(
        ROBLOX_GAMES_INFO_API,
        params={"universeIds": universe_id},
        timeout=10
    )
    if resp.status_code != 200:
        raise RobloxAPIError(f"HTTP {resp.status_code}")
    data = resp.json().get("data", [])
    if not data:
        return None
    rpid = data[0].get("rootPlaceId")
    return str(rpid) if rpid else None


def rbxcrate_headers():
    return {
        "api-key": RBXCRATE_API_KEY,
        "Content-Type": "application/json",
    }


def create_rbxcrate_order(roblox_username: str, place_id: str, robux_amount: int, order_id: str):
    url = RBXCRATE_BASE_URL + RBXCRATE_ORDER_ENDPOINT
    payload = {
        "robloxUsername": roblox_username,
        "orderId": order_id,
        "robuxAmount": robux_amount,
        "placeId": int(place_id),
        "isPreOrder": True,
        "checkOwnership": False,
    }
    resp = requests.post(url, headers=rbxcrate_headers(), json=payload, timeout=20)

    if resp.status_code == 201:
        return resp.json()["data"]["orderId"]

    error_messages = {
        400: "Ошибка разбора данных гейм-пасса или гейм-пасс не совпадает с продавцом",
        402: "На RbxCrate сейчас нет робуксов в наличии на нужную сумму",
        403: "Недостаточно средств на балансе RbxCrate",
        404: "Гейм-пасс не найден — проверьте цену гейм-пасса и никнейм",
        409: "Заказ с таким ID уже существует",
        429: "Достигнут дневной лимит покупки робуксов для этого аккаунта Roblox",
    }
    raise RuntimeError(error_messages.get(resp.status_code, f"HTTP {resp.status_code}: {resp.text[:200]}"))


def check_rbxcrate_status(order_id: str):
    url = RBXCRATE_BASE_URL + RBXCRATE_INFO_ENDPOINT
    resp = requests.post(url, headers=rbxcrate_headers(), json={"orderId": order_id}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("status", "Pending"), data.get("error")


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def create_admin():
    if not hasattr(app, '_admin_created'):
        db.create_all()
        admin_user = os.environ.get('ADMIN_USERNAME')
        admin_pass = os.environ.get('ADMIN_PASSWORD')
        if admin_user and admin_pass:
            existing = User.query.filter_by(username=admin_user).first()
            if not existing:
                u = User(username=admin_user, is_admin=True, is_approved=True, balance=0)
                u.set_password(admin_pass)
                db.session.add(u)
                db.session.commit()
        app._admin_created = True


@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin_panel'))
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.is_approved and not user.is_admin:
                flash('Ваша регистрация ещё не одобрена администратором.', 'warning')
                return redirect(url_for('login'))
            login_user(user)
            return redirect(url_for('index'))
        flash('Неверный логин или пароль.', 'danger')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Заполните все поля.', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Такой пользователь уже существует.', 'danger')
            return redirect(url_for('register'))
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Регистрация успешна! Ожидайте одобрения администратора.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        return redirect(url_for('admin_panel'))
    if not current_user.is_approved:
        flash('Ваш аккаунт ожидает одобрения.', 'warning')
        return redirect(url_for('login'))

    timeout = datetime.utcnow() - RESERVE_TIMEOUT

    available_pass = Product.query.filter(
        Product.is_used == False,
        db.or_(
            Product.reserved_by_user_id == None,
            Product.reserved_at < timeout
        )
    ).count()

    available_skin = Product.query.filter(
        Product.is_used == True,
        Product.skin_done == False,
        Product.rbxcrate_completed_at != None,
        Product.rbxcrate_completed_at <= datetime.utcnow() - timedelta(days=5),
        db.or_(
            Product.skin_reserved_by_user_id == None,
            Product.skin_reserved_at < timeout
        )
    ).count()

    my_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    my_withdrawals = Withdrawal.query.filter_by(user_id=current_user.id).order_by(Withdrawal.created_at.desc()).all()
    return render_template('dashboard.html', available_pass=available_pass, available_skin=available_skin,
                           orders=my_orders, balance=current_user.balance, withdrawals=my_withdrawals)


@app.route('/create_pass', methods=['GET', 'POST'])
@login_required
def create_pass():
    if current_user.is_admin:
        return redirect(url_for('admin_panel'))
    if not current_user.is_approved:
        flash('Ваш аккаунт ожидает одобрения.', 'warning')
        return redirect(url_for('login'))

    active_order = Order.query.filter_by(user_id=current_user.id, status='pending').first()
    if active_order and request.method == 'GET':
        flash('У вас уже есть активный заказ. Завершите его или дождитесь обработки.', 'info')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        product_id = request.form.get('product_id', type=int)
        product = Product.query.get(product_id)
        if not product or product.is_used or product.reserved_by_user_id != current_user.id:
            flash('Ошибка: товар не найден, уже использован или зарезервирован другим пользователем.', 'danger')
            return redirect(url_for('dashboard'))

        username = product.roblox_username

        try:
            user_id = get_user_id(username)
            games = get_user_games(user_id)
            if not games:
                flash('На этом аккаунте Roblox не найдено опубликованных игр.', 'danger')
                return redirect(url_for('dashboard'))
            place_id = get_root_place(games[0]['universe_id'])
            if not place_id:
                flash('Не удалось определить Place ID игры.', 'danger')
                return redirect(url_for('dashboard'))

            internal_order_id = str(uuid.uuid4())
            rbxcrate_id = create_rbxcrate_order(
                roblox_username=username,
                place_id=place_id,
                robux_amount=108,
                order_id=internal_order_id
            )

            order = Order(
                user_id=current_user.id,
                product_id=product.id,
                roblox_username=username,
                place_id=place_id,
                rbxcrate_order_id=rbxcrate_id,
                amount=108,
                status='pending'
            )
            product.is_used = True
            product.used_at = datetime.utcnow()
            product.used_by_user_id = current_user.id

            db.session.add(order)
            db.session.commit()

            flash('Заказ успешно создан и отправлен в очередь RbxCrate! Робуксы поступят в течение дня.', 'success')
            return redirect(url_for('dashboard'))

        except RobloxUserNotFoundError:
            flash(f'Аккаунт Roblox с ником «{username}» не найден.', 'danger')
        except RobloxAPIError as e:
            flash(f'Ошибка Roblox API: {e}', 'danger')
        except RuntimeError as e:
            flash(f'Ошибка RbxCrate: {e}', 'danger')
        except Exception as e:
            flash(f'Неожиданная ошибка: {e}', 'danger')

        return redirect(url_for('dashboard'))

    timeout = datetime.utcnow() - RESERVE_TIMEOUT

    product = Product.query.filter_by(is_used=False, reserved_by_user_id=None).with_for_update().first()
    if not product:
        product = Product.query.filter(
            Product.is_used == False,
            Product.reserved_by_user_id != None,
            Product.reserved_at < timeout
        ).with_for_update().first()

    if not product:
        db.session.rollback()
        flash('К сожалению, товары закончились. Приходите позже!', 'warning')
        return redirect(url_for('dashboard'))

    product.reserved_by_user_id = current_user.id
    product.reserved_at = datetime.utcnow()
    db.session.commit()

    return render_template('create_pass.html', product=product)


@app.route('/create_skin', methods=['GET', 'POST'])
@login_required
def create_skin():
    if current_user.is_admin:
        return redirect(url_for('admin_panel'))
    if not current_user.is_approved:
        flash('Ваш аккаунт ожидает одобрения.', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        product_id = request.form.get('product_id', type=int)
        product = Product.query.get(product_id)
        if not product or product.skin_done or product.skin_reserved_by_user_id != current_user.id:
            flash('Ошибка: аккаунт не найден, скин уже сделан или занят другим пользователем.', 'danger')
            return redirect(url_for('dashboard'))

        product.skin_done = True
        product.skin_done_at = datetime.utcnow()
        product.skin_done_by_user_id = current_user.id
        product.skin_reserved_by_user_id = None

        user = User.query.get(current_user.id)
        user.balance += SKIN_REWARD

        db.session.commit()
        flash(f'Скин создан! На ваш баланс начислено {SKIN_REWARD} рублей.', 'success')
        return redirect(url_for('dashboard'))

    timeout = datetime.utcnow() - RESERVE_TIMEOUT

    product = Product.query.filter(
        Product.is_used == True,
        Product.skin_done == False,
        Product.rbxcrate_completed_at != None,
        Product.rbxcrate_completed_at <= datetime.utcnow() - timedelta(days=5),
        Product.skin_reserved_by_user_id == None
    ).with_for_update().first()

    if not product:
        product = Product.query.filter(
            Product.is_used == True,
            Product.skin_done == False,
            Product.rbxcrate_completed_at != None,
            Product.rbxcrate_completed_at <= datetime.utcnow() - timedelta(days=5),
            Product.skin_reserved_by_user_id != None,
            Product.skin_reserved_at < timeout
        ).with_for_update().first()

    if not product:
        db.session.rollback()
        flash('Сейчас нет доступных аккаунтов для создания скина.', 'warning')
        return redirect(url_for('dashboard'))

    product.skin_reserved_by_user_id = current_user.id
    product.skin_reserved_at = datetime.utcnow()
    db.session.commit()

    return render_template('create_skin.html', product=product, links=SKIN_LINKS)


@app.route('/withdraw', methods=['GET', 'POST'])
@login_required
def withdraw():
    if current_user.is_admin:
        return redirect(url_for('admin_panel'))
    if not current_user.is_approved:
        flash('Ваш аккаунт ожидает одобрения.', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        amount_str = request.form.get('amount', '').strip()
        withdraw_all = request.form.get('withdraw_all')

        if not phone:
            flash('Введите номер телефона (СБП).', 'danger')
            return redirect(url_for('withdraw'))

        if withdraw_all:
            amount = current_user.balance
        else:
            try:
                amount = int(amount_str)
            except ValueError:
                flash('Введите корректную сумму.', 'danger')
                return redirect(url_for('withdraw'))

        if amount <= 0:
            flash('Сумма должна быть больше 0.', 'danger')
            return redirect(url_for('withdraw'))

        if amount > current_user.balance:
            flash('Недостаточно средств на балансе.', 'danger')
            return redirect(url_for('withdraw'))

        w = Withdrawal(user_id=current_user.id, amount=amount, phone=phone)
        db.session.add(w)
        db.session.commit()

        flash('Заявка на вывод создана! Ожидайте подтверждения администратора.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('withdraw.html', balance=current_user.balance)


@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    users = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all()
    products = Product.query.order_by(Product.created_at.desc()).limit(100).all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(100).all()
    withdrawals = Withdrawal.query.order_by(Withdrawal.created_at.desc()).limit(100).all()
    storage = Product.query.filter_by(skin_done=True).order_by(Product.skin_done_at.desc()).all()

    stats = {
        'total_products': Product.query.count(),
        'available_products': Product.query.filter_by(is_used=False).count(),
        'used_products': Product.query.filter_by(is_used=True).count(),
        'total_users': User.query.filter_by(is_admin=False).count(),
        'approved_users': User.query.filter_by(is_admin=False, is_approved=True).count(),
        'pending_users': User.query.filter_by(is_admin=False, is_approved=False).count(),
        'total_orders': Order.query.count(),
        'pending_withdrawals': Withdrawal.query.filter_by(status='pending').count(),
        'storage_count': Product.query.filter_by(skin_done=True).count(),
    }
    return render_template('admin.html', users=users, products=products, orders=orders,
                           stats=stats, withdrawals=withdrawals, storage=storage)


@app.route('/admin/upload', methods=['POST'])
@login_required
@admin_required
def admin_upload():
    data = request.form.get('products', '').strip()
    if not data:
        flash('Введите данные товаров.', 'warning')
        return redirect(url_for('admin_panel'))

    lines = [line.strip() for line in data.splitlines() if line.strip()]
    added = 0
    for line in lines:
        if ':' not in line:
            continue
        parts = line.split(':', 1)
        login_part = parts[0].strip()
        pass_part = parts[1].strip()
        if not login_part or not pass_part:
            continue
        p = Product(roblox_username=login_part, roblox_password=pass_part)
        db.session.add(p)
        added += 1

    db.session.commit()
    flash(f'Загружено {added} товаров.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/approve/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_approve(user_id):
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    db.session.commit()
    flash(f'Пользователь {user.username} одобрен.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/reject/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_reject(user_id):
    user = User.query.get_or_404(user_id)
    user.is_approved = False
    db.session.commit()
    flash(f'Пользователь {user.username} отклонён.', 'info')
    return redirect(url_for('admin_panel'))


@app.route('/admin/delete_product/<int:product_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_product(product_id):
    p = Product.query.get_or_404(product_id)
    db.session.delete(p)
    db.session.commit()
    flash('Товар удалён.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/check_order/<int:order_id>', methods=['POST'])
@login_required
@admin_required
def admin_check_order(order_id):
    order = Order.query.get_or_404(order_id)
    if not order.rbxcrate_order_id:
        flash('У заказа нет ID RbxCrate.', 'warning')
        return redirect(url_for('admin_panel'))

    try:
        status, error = check_rbxcrate_status(order.rbxcrate_order_id)
        order.rbxcrate_status = status
        s = str(status).lower()
        if s in ('completed', 'success', 'done', 'delivered'):
            order.status = 'completed'
            order.product.rbxcrate_completed_at = datetime.utcnow()
            if not order.reward_paid:
                user = User.query.get(order.user_id)
                user.balance += PASS_REWARD
                order.reward_paid = True
                flash(f'Заказ выполнен! Пользователю {user.username} начислено {PASS_REWARD} руб.', 'success')
        elif s in ('failed', 'error', 'cancelled', 'canceled'):
            order.status = 'failed'
            order.error_msg = str(error) if error else 'Unknown error'
            flash(f'Заказ завершился с ошибкой.', 'danger')
        else:
            flash(f'Текущий статус: {status}', 'info')
        db.session.commit()
    except Exception as e:
        flash(f'Ошибка проверки: {e}', 'danger')

    return redirect(url_for('admin_panel'))


@app.route('/admin/confirm_withdrawal/<int:withdrawal_id>', methods=['POST'])
@login_required
@admin_required
def admin_confirm_withdrawal(withdrawal_id):
    w = Withdrawal.query.get_or_404(withdrawal_id)
    if w.status != 'pending':
        flash('Эта заявка уже обработана.', 'warning')
        return redirect(url_for('admin_panel'))

    user = User.query.get(w.user_id)
    if user.balance < w.amount:
        flash('У пользователя недостаточно средств (баланс изменился).', 'danger')
        return redirect(url_for('admin_panel'))

    user.balance -= w.amount
    w.status = 'completed'
    w.processed_at = datetime.utcnow()
    db.session.commit()

    flash(f'Вывод {w.amount} руб. для {user.username} подтверждён.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/reject_withdrawal/<int:withdrawal_id>', methods=['POST'])
@login_required
@admin_required
def admin_reject_withdrawal(withdrawal_id):
    w = Withdrawal.query.get_or_404(withdrawal_id)
    w.status = 'rejected'
    w.processed_at = datetime.utcnow()
    db.session.commit()
    flash('Заявка на вывод отклонена.', 'info')
    return redirect(url_for('admin_panel'))


if __name__ == '__main__':
    app.run(debug=False)
