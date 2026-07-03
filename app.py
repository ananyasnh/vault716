import os
import sqlite3
import datetime
import threading
import time
import urllib.request
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vault716_secure_session_token_1984!')
app.permanent_session_lifetime = timedelta(days=30)
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vault716.db')

@app.before_request
def make_session_permanent():
    session.permanent = True

HOT_MEAL_CAP = 25
FROZEN_MEAL_CAP = 15
WHATSAPP_LINK = 'https://chat.whatsapp.com/placeholder'

# -------------------------------------------------------------
# DATABASE CONNECTION HELPERS (Supports SQLite & Postgres)
# -------------------------------------------------------------
USING_POSTGRES = False
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    try:
        import psycopg2
        import psycopg2.extras
        USING_POSTGRES = True
        print("DATABASE CONFIGURATION: Using cloud PostgreSQL database.")
    except ImportError:
        print("DATABASE CONFIGURATION: DATABASE_URL is set but psycopg2 is not installed. Falling back to local SQLite.")

def adapt_query(query):
    if USING_POSTGRES:
        # Convert ? to %s for parameters
        q = query.replace('?', '%s')
        # Adapt auto-increment syntax for tables
        q = q.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
        # Replace TEXT DEFAULT CURRENT_TIMESTAMP with TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        q = q.replace('TEXT DEFAULT CURRENT_TIMESTAMP', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        # Replace REAL with DOUBLE PRECISION
        q = q.replace('REAL', 'DOUBLE PRECISION')
        return q
    return query

class CursorWrapper:
    def __init__(self, cur):
        self.cur = cur

    def execute(self, query, args=()):
        self.cur.execute(query, args)
        return self

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def close(self):
        self.cur.close()

class DatabaseWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, args=()):
        q = adapt_query(query)
        if USING_POSTGRES:
            cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(q, args)
            return cur
        else:
            return self.conn.execute(q, args)

    def executemany(self, query, args_list):
        q = adapt_query(query)
        if USING_POSTGRES:
            cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.executemany(q, args_list)
            return cur
        else:
            return self.conn.executemany(q, args_list)

    def cursor(self):
        if USING_POSTGRES:
            return CursorWrapper(self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor))
        else:
            return self.conn.cursor()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        if USING_POSTGRES:
            conn = psycopg2.connect(DATABASE_URL)
            db = g._database = DatabaseWrapper(conn)
        else:
            conn = sqlite3.connect(DATABASE)
            conn.row_factory = sqlite3.Row
            db = g._database = DatabaseWrapper(conn)
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

# -------------------------------------------------------------
# DATABASE SCHEMA & INITIALIZATION
# -------------------------------------------------------------
def init_db():
    with app.app_context():
        db = get_db()
        # Create Users table
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                plan_tier TEXT NOT NULL, -- 'Hot' or 'Frozen'
                protein_upgrade INTEGER NOT NULL DEFAULT 0, -- 0 = Standard, 1 = Gym Upgrade
                current_status TEXT NOT NULL DEFAULT 'Active', -- 'Active' or 'Paused'
                date_created TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Migrate users if date_created column is missing
        try:
            db.execute("ALTER TABLE users ADD COLUMN date_created TEXT DEFAULT CURRENT_TIMESTAMP")
        except Exception:
            pass

        # Create Orders table
        db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_number INTEGER UNIQUE NOT NULL,
                amount_due REAL NOT NULL,
                payment_status TEXT NOT NULL DEFAULT 'Unpaid', -- 'Unpaid' or 'Paid'
                date_created TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # Create Reviews table
        db.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                review_text TEXT NOT NULL,
                date_created TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        # Create Waitlist table
        db.execute('''
            CREATE TABLE IF NOT EXISTS waitlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                plan_tier TEXT NOT NULL,
                protein_upgrade INTEGER NOT NULL DEFAULT 0,
                date_created TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.commit()

        # Seed data if database is empty
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            print("Seeding initial database data...")
            sample_users = [
                ('John Doe', 'john@vault716.com', generate_password_hash('password123'), '716-555-0101', 'Hot', 0, 'Active'),
                ('Jane Smith', 'jane@vault716.com', generate_password_hash('password123'), '716-555-0102', 'Hot', 1, 'Active'),
                ('Bob Johnson', 'bob@vault716.com', generate_password_hash('password123'), '716-555-0103', 'Frozen', 0, 'Active'),
                ('Alice Williams', 'alice@vault716.com', generate_password_hash('password123'), '716-555-0104', 'Frozen', 1, 'Active'),
                ('Charlie Brown', 'charlie@vault716.com', generate_password_hash('password123'), '716-555-0105', 'Hot', 1, 'Paused')
            ]
            db.executemany('''
                INSERT INTO users (name, email, password_hash, phone_number, plan_tier, protein_upgrade, current_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', sample_users)
            db.commit()

            # Seed initial orders
            # Get user IDs
            users_map = {row['email']: dict(row) for row in query_db("SELECT id, email, plan_tier, protein_upgrade FROM users")}
            
            # Calculate and insert orders
            # Order 1000 - John Doe (Hot, Standard -> $50.00)
            # Order 1001 - Jane Smith (Hot, Gym -> $65.00)
            # Order 1002 - Bob Johnson (Frozen, Standard -> $55.00)
            # Order 1003 - Alice Williams (Frozen, Gym -> $70.00)
            sample_orders = [
                (users_map['john@vault716.com']['id'], 1000, 50.00, 'Paid'),
                (users_map['jane@vault716.com']['id'], 1001, 65.00, 'Unpaid'),
                (users_map['bob@vault716.com']['id'], 1002, 55.00, 'Paid'),
                (users_map['alice@vault716.com']['id'], 1003, 70.00, 'Unpaid')
            ]
            db.executemany('''
                INSERT INTO orders (user_id, order_number, amount_due, payment_status)
                VALUES (?, ?, ?, ?)
            ''', sample_orders)
            db.commit()
            print("Seeding completed successfully!")

# Initialize DB on import/startup
init_db()

# -------------------------------------------------------------
# BUSINESS LOGIC HELPERS
# -------------------------------------------------------------
def get_active_hot_subscribers():
    """Counts the total number of users where plan_tier = 'Hot' and current_status = 'Active'."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM users 
        WHERE plan_tier = 'Hot' AND current_status = 'Active'
    """)
    return cursor.fetchone()[0]

def get_active_frozen_subscribers():
    """Counts the total number of users where plan_tier = 'Frozen' and current_status = 'Active'."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM users 
        WHERE plan_tier = 'Frozen' AND current_status = 'Active'
    """)
    return cursor.fetchone()[0]

def get_current_menu(simulate_week=None):
    """
    Checks the calendar week number.
    ODD weeks -> Menu A (Week 1 & 3)
    EVEN weeks -> Menu B (Week 2 & 4)
    """
    if simulate_week is not None:
        try:
            week_number = int(simulate_week)
        except ValueError:
            week_number = datetime.date.today().isocalendar()[1]
    else:
        week_number = datetime.date.today().isocalendar()[1]

    is_odd = (week_number % 2) != 0

    menu_a = {
        'name': 'Menu A',
        'week_type': 'Odd Week',
        'items': [
            {'day': 'Monday', 'meal': 'Chicken Tikka Masala'},
            {'day': 'Tuesday', 'meal': 'Red Pasta'},
            {'day': 'Wednesday', 'meal': 'Burrito bowl'},
            {'day': 'Thursday', 'meal': 'Sweet Teriyaki Glazed Chicken'},
            {'day': 'Friday', 'meal': 'Barbecue Chicken Sliders'}
        ]
    }

    menu_b = {
        'name': 'Menu B',
        'week_type': 'Even Week',
        'items': [
            {'day': 'Monday', 'meal': 'Mediterranean Bowl'},
            {'day': 'Tuesday', 'meal': 'White Pasta'},
            {'day': 'Wednesday', 'meal': 'Stir Fry Ginger-Garlic Chicken'},
            {'day': 'Thursday', 'meal': 'Crispy Chicken Tenders'},
            {'day': 'Friday', 'meal': 'Chicken Caesar Wrap'}
        ]
    }

    selected_menu = menu_a if is_odd else menu_b
    return selected_menu, week_number

def get_detailed_menus():
    """Returns the finalized 10-dish rotation with descriptions, side components, and macros."""
    week_13 = [
        {
            'day': 'Monday',
            'meal': 'Chicken Tikka Masala',
            'description': 'Chicken Tikka Masala served with white rice and cucumber-onion salad.',
            'sides': 'White rice and cucumber-onion salad.',
            'std': {'cal': 610, 'protein': 40, 'carbs': 68, 'fat': 11, 'base': '5oz chicken, standard rice portion'},
            'gym': {'cal': 700, 'protein': 66, 'carbs': 52, 'fat': 13, 'base': '8oz chicken, scaled rice portion'},
            'fallback_img': 'https://images.unsplash.com/photo-1603894584373-5ac82b2ae398?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Tuesday',
            'meal': 'Red Pasta',
            'description': 'Creamy red sauce pasta served with garlic bread.',
            'sides': 'Creamy red sauce pasta served with garlic bread.',
            'std': {'cal': 640, 'protein': 45, 'carbs': 70, 'fat': 12, 'base': '5oz chicken, standard pasta portion'},
            'gym': {'cal': 730, 'protein': 72, 'carbs': 55, 'fat': 14, 'base': '8oz chicken, high-fiber pasta modifier'},
            'fallback_img': 'https://images.unsplash.com/photo-1551183053-bf91a1d81141?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Wednesday',
            'meal': 'Burrito bowl',
            'description': 'Burrito bowl including yellow rice, grilled chicken bites, fresh salsa, sour cream, Guacamole, lettuce, and tortilla strips.',
            'sides': 'Yellow rice, grilled chicken bites, fresh salsa, sour cream, Guacamole, lettuce, and tortilla strips.',
            'std': {'cal': 620, 'protein': 42, 'carbs': 65, 'fat': 10, 'base': '5oz chicken, standard base portion'},
            'gym': {'cal': 710, 'protein': 68, 'carbs': 50, 'fat': 12, 'base': '8oz chicken, scaled base portion'},
            'fallback_img': 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Thursday',
            'meal': 'Sweet Teriyaki Glazed Chicken',
            'description': 'Sweet Teriyaki Glazed Chicken served with white rice.',
            'sides': 'White rice.',
            'std': {'cal': 590, 'protein': 41, 'carbs': 72, 'fat': 8, 'base': '5oz chicken, 1.5 cups white rice'},
            'gym': {'cal': 680, 'protein': 67, 'carbs': 56, 'fat': 9, 'base': '8oz chicken, 1 cup white rice'},
            'fallback_img': 'https://images.unsplash.com/photo-1529042410759-befb1204b468?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Friday',
            'meal': 'Barbecue Chicken Sliders',
            'description': 'Barbecue chicken sliders served with coleslaw, cheese and barbecue chicken.',
            'sides': 'Coleslaw, cheese and barbecue chicken.',
            'std': {'cal': 660, 'protein': 44, 'carbs': 75, 'fat': 14, 'base': 'Standard sliders portion'},
            'gym': {'cal': 760, 'protein': 69, 'carbs': 60, 'fat': 16, 'base': 'Extra meat allocation, double protein cheese sauce'},
            'fallback_img': 'https://images.unsplash.com/photo-1544025162-d76694265947?w=600&auto=format&fit=crop&q=60'
        }
    ]

    week_24 = [
        {
            'day': 'Monday',
            'meal': 'Mediterranean Bowl',
            'description': 'Mediterranean bowl served with grilled chicken bites, chickpea salad, hummus, and Tzatziki cucumber salad.',
            'sides': 'Grilled chicken bites, chickpea salad, hummus, and Tzatziki cucumber salad.',
            'std': {'cal': 600, 'protein': 43, 'carbs': 64, 'fat': 10, 'base': '5oz chicken, standard Mediterranean bowl'},
            'gym': {'cal': 690, 'protein': 69, 'carbs': 48, 'fat': 12, 'base': '8oz chicken, extra chickpea salad and greens'},
            'fallback_img': 'https://images.unsplash.com/photo-1512621776951-a57141f2eefd?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Tuesday',
            'meal': 'White Pasta',
            'description': 'Creamy white sauce, Garlic Alfredo pasta served with garlic bread.',
            'sides': 'Creamy white sauce, Garlic Alfredo pasta served with garlic bread.',
            'std': {'cal': 650, 'protein': 46, 'carbs': 68, 'fat': 14, 'base': '5oz chicken, standard white pasta'},
            'gym': {'cal': 740, 'protein': 73, 'carbs': 52, 'fat': 16, 'base': '8oz chicken, high-fiber pasta modifier'},
            'fallback_img': 'https://images.unsplash.com/photo-1645112411341-6c4fd023714a?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Wednesday',
            'meal': 'Stir Fry Ginger-Garlic Chicken',
            'description': 'Stir fry ginger-garlic chicken served with vegetable fried rice.',
            'sides': 'Vegetable fried rice.',
            'std': {'cal': 610, 'protein': 42, 'carbs': 68, 'fat': 11, 'base': '5oz chicken, 1.5 cups vegetable fried rice'},
            'gym': {'cal': 700, 'protein': 68, 'carbs': 52, 'fat': 13, 'base': '8oz chicken, 1 cup vegetable fried rice'},
            'fallback_img': 'https://images.unsplash.com/photo-1512058564366-18510be2db19?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Thursday',
            'meal': 'Crispy Chicken Tenders',
            'description': 'Crispy chicken tenders served with mac & cheese.',
            'sides': 'Mac & cheese.',
            'std': {'cal': 640, 'protein': 45, 'carbs': 70, 'fat': 12, 'base': '3 large tenders, standard mac & cheese'},
            'gym': {'cal': 730, 'protein': 71, 'carbs': 54, 'fat': 14, 'base': '5 large tenders, light mac & cheese'},
            'fallback_img': 'https://images.unsplash.com/photo-1562967916-eb82221dfb92?w=600&auto=format&fit=crop&q=60'
        },
        {
            'day': 'Friday',
            'meal': 'Chicken Caesar Wrap',
            'description': 'Chicken Caesar wrap served in a warm flour tortilla.',
            'sides': 'Chicken Caesar wrap.',
            'std': {'cal': 630, 'protein': 42, 'carbs': 78, 'fat': 9, 'base': 'Standard chicken wrap'},
            'gym': {'cal': 720, 'protein': 68, 'carbs': 62, 'fat': 11, 'base': 'Double chicken Caesar wrap'},
            'fallback_img': 'https://images.unsplash.com/photo-1606787366850-de6330128bfc?w=600&auto=format&fit=crop&q=60'
        }
    ]

    return week_13, week_24

def calculate_amount(plan_tier, protein_upgrade):
    """Calculates weekly subscription cost."""
    base = 50.00 if plan_tier == 'Hot' else 55.00
    upgrade = 20.00 if protein_upgrade else 0.00
    return base + upgrade

# -------------------------------------------------------------
# APPLICATION ROUTES
# -------------------------------------------------------------
@app.route('/')
def home():
    active_hot = get_active_hot_subscribers()
    spots_remaining = max(0, HOT_MEAL_CAP - active_hot)
    hot_spots_full = (active_hot >= HOT_MEAL_CAP)
    return render_template('index.html', spots_remaining=spots_remaining, hot_spots_full=hot_spots_full)

@app.route('/tier/<tier_type>')
def tier_detail(tier_type):
    if tier_type not in ['fresh-hot', 'weekly-frozen']:
        return redirect(url_for('home'))
    active_hot = get_active_hot_subscribers()
    active_frozen = get_active_frozen_subscribers()
    spots_remaining = max(0, HOT_MEAL_CAP - active_hot)
    hot_spots_full = (active_hot >= HOT_MEAL_CAP)
    frozen_spots_full = (active_frozen >= FROZEN_MEAL_CAP)
    week_13, week_24 = get_detailed_menus()
    return render_template(
        'tier_details.html',
        tier_type=tier_type,
        week_13=week_13,
        week_24=week_24,
        spots_remaining=spots_remaining,
        hot_spots_full=hot_spots_full,
        frozen_spots_full=frozen_spots_full
    )

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        phone = request.form.get('phone', '').strip()
        
        if not email or not password or not phone:
            flash("All fields are required.", "error")
            return render_template('register.html')
            
        name = email.split('@')[0].capitalize()
            
        db = get_db()
        cursor = db.cursor()
        
        # Check duplicate email
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            flash("Email address is already registered.", "error")
            return render_template('register.html')
            
        try:
            password_hash = generate_password_hash(password)
            cursor.execute('''
                INSERT INTO users (name, email, password_hash, phone_number, plan_tier, protein_upgrade, current_status)
                VALUES (?, ?, ?, ?, 'None', 0, 'Pending')
            ''', (name, email, password_hash, phone))
            db.commit()
            
            # Log user in
            user_id = cursor.lastrowid
            session['user_id'] = user_id
            session['user_name'] = name
            
            flash("Account created successfully! Select a subscription tier below to lock in your spot.", "success")
            return redirect(url_for('home'))
        except Exception as e:
            db.rollback()
            flash("An error occurred. Please try again.", "error")
            return render_template('register.html')
            
    return render_template('register.html')

@app.route('/checkout/<tier_type>', methods=['GET', 'POST'])
def checkout(tier_type):
    if 'user_id' not in session:
        return redirect(url_for('login', next=request.path))
        
    if tier_type not in ['fresh-hot', 'weekly-frozen']:
        return redirect(url_for('home'))
        
    active_hot = get_active_hot_subscribers()
    active_frozen = get_active_frozen_subscribers()
    hot_spots_full = (active_hot >= HOT_MEAL_CAP)
    frozen_spots_full = (active_frozen >= FROZEN_MEAL_CAP)
    
    db = get_db()
    user = query_db("SELECT * FROM users WHERE id = ?", (session['user_id'],), one=True)
    if not user:
        session.clear()
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        chosen_tier = 'Hot' if tier_type == 'fresh-hot' else 'Frozen'
        protein_upgrade = 1 if request.form.get('protein_upgrade') else 0
        
        # Check counter capacity rule
        if chosen_tier == 'Hot' and hot_spots_full:
            flash("Roster is full. Standard Fresh-Hot checkout blocked.", "error")
            return redirect(url_for('checkout', tier_type=tier_type))
        if chosen_tier == 'Frozen' and frozen_spots_full:
            flash("Frozen Vault is currently full.", "error")
            return redirect(url_for('checkout', tier_type=tier_type))
            
        try:
            db.execute('''
                UPDATE users 
                SET plan_tier = ?, protein_upgrade = ?, current_status = 'Active'
                WHERE id = ?
            ''', (chosen_tier, protein_upgrade, session['user_id']))
            
            # Generate first order
            order_number = generate_order_number()
            amount_due = calculate_amount(chosen_tier, protein_upgrade)
            db.execute('''
                INSERT INTO orders (user_id, order_number, amount_due, payment_status)
                VALUES (?, ?, ?, 'Unpaid')
            ''', (session['user_id'], order_number, amount_due))
            
            db.commit()
            flash("Subscription successfully activated! Welcome to Vault 716.", "success")
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.rollback()
            flash("An error occurred during checkout. Please try again.", "error")
            return redirect(url_for('checkout', tier_type=tier_type))
            
    return render_template(
        'checkout.html',
        tier_type=tier_type,
        hot_spots_full=hot_spots_full,
        frozen_spots_full=frozen_spots_full,
        user=user,
        whatsapp_link=WHATSAPP_LINK
    )

@app.route('/api/waitlist', methods=['POST'])
def api_waitlist():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    phone = request.form.get('phone', '').strip()
    plan_tier = request.form.get('plan_tier', 'Frozen')
    protein_upgrade = 1 if request.form.get('protein_upgrade') else 0
    
    if not name or not email or not phone:
        flash("All fields are required to join the waitlist.", "error")
        return redirect(url_for('home'))
        
    db = get_db()
    try:
        db.execute('''
            INSERT INTO waitlist (name, email, phone_number, plan_tier, protein_upgrade)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, email, phone, plan_tier, protein_upgrade))
        db.commit()
        weekly_cost = calculate_amount(plan_tier, protein_upgrade)
        flash("You have been successfully added to our Priority Waitlist!", "success")
        return render_template(
            'waitlist_success.html',
            name=name,
            email=email,
            phone=phone,
            plan_tier=plan_tier,
            protein_upgrade=protein_upgrade,
            weekly_cost=weekly_cost,
            whatsapp_link=WHATSAPP_LINK
        )
    except Exception as e:
        db.rollback()
        flash("An error occurred. Please try again.", "error")
        return redirect(url_for('home'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('home'))
        
    next_page = request.args.get('next') or request.form.get('next')
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template('login.html', next=next_page)
            
        user = query_db("SELECT * FROM users WHERE email = ?", (email,), one=True)
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash(f"Welcome back, {user['name']}!", "success")
            if next_page:
                return redirect(next_page)
            return redirect(url_for('home'))
        else:
            flash("Invalid email or password.", "error")
            
    return render_template('login.html', next=next_page)

@app.route('/logout')
def logout():
    session.clear()
    flash("You have logged out successfully.", "success")
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    db = get_db()
    user = query_db("SELECT * FROM users WHERE id = ?", (session['user_id'],), one=True)
    if not user:
        session.clear()
        return redirect(url_for('login'))
        
    # Count pickups (Paid orders)
    pickups_row = query_db("SELECT COUNT(*) as count FROM orders WHERE user_id = ? AND payment_status = 'Paid'", (user['id'],), one=True)
    pickup_count = pickups_row['count'] if pickups_row else 0

    # Determine if user has an active plan (has a plan_tier set and status is Active)
    has_plan = bool(user['plan_tier'] and user['current_status'] == 'Active')

    # Format membership start date & calculate weeks active
    start_date = user.get('date_created') if 'date_created' in user.keys() else None
    formatted_start_date = None
    weeks_active = 0
    if start_date:
        if isinstance(start_date, datetime.datetime):
            dt = start_date
        else:
            try:
                # Remove timezone if present, then parse
                dt_str = str(start_date).split('.')[0].split('+')[0]
                dt = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            except Exception:
                dt = None

        if dt:
            formatted_start_date = dt.strftime('%B %d, %Y')
            delta = datetime.datetime.utcnow() - dt
            weeks_active = max(1, delta.days // 7 + 1)
        else:
            formatted_start_date = str(start_date)
            weeks_active = 1

    # Check if there is an active/unpaid order in the current week to show
    recent_order = query_db("""
        SELECT * FROM orders 
        WHERE user_id = ? 
        ORDER BY id DESC LIMIT 1
    """, (user['id'],), one=True)
    
    # Check menu (handle simulated week number if provided)
    simulate_week = request.args.get('simulate_week')
    menu, week_number = get_current_menu(simulate_week)
    
    # Calculate amount due for next checkout (only meaningful if plan exists)
    weekly_cost = calculate_amount(user['plan_tier'], user['protein_upgrade']) if user['plan_tier'] else 0.0
    
    # Spots limit details
    active_hot = get_active_hot_subscribers()
    hot_spots_full = (active_hot >= HOT_MEAL_CAP)
    spots_remaining = max(0, HOT_MEAL_CAP - active_hot)
    
    return render_template(
        'dashboard.html',
        user=user,
        has_plan=has_plan,
        recent_order=recent_order,
        menu=menu,
        week_number=week_number,
        weekly_cost=weekly_cost,
        hot_spots_full=hot_spots_full,
        spots_remaining=spots_remaining,
        pickup_count=pickup_count,
        formatted_start_date=formatted_start_date,
        weeks_active=weeks_active
    )

@app.route('/update-plan', methods=['POST'])
def update_plan():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
    db = get_db()
    user = query_db("SELECT * FROM users WHERE id = ?", (session['user_id'],), one=True)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
        
    plan_tier = request.form.get('plan_tier')
    protein_upgrade = 1 if request.form.get('protein_upgrade') else 0
    current_status = request.form.get('current_status', user['current_status'])
    
    # Double check capping
    if plan_tier == 'Hot' and current_status == 'Active':
        # Check current count excluding ourselves if we are already Active & Hot
        active_hot = get_active_hot_subscribers()
        is_already_active_hot = (user['plan_tier'] == 'Hot' and user['current_status'] == 'Active')
        
        effective_active_hot = active_hot - 1 if is_already_active_hot else active_hot
        if effective_active_hot >= HOT_MEAL_CAP:
            flash("Daily Fresh-Hot spots are currently full! Select the Frozen Vault plan to lock in your meals.", "error")
            return redirect(url_for('dashboard'))

    try:
        db.execute('''
            UPDATE users 
            SET plan_tier = ?, protein_upgrade = ?, current_status = ?
            WHERE id = ?
        ''', (plan_tier, protein_upgrade, current_status, user['id']))
        db.commit()
        flash("Your subscription plan has been updated.", "success")
    except Exception as e:
        db.rollback()
        flash("Failed to update plan. Please try again.", "error")
        
    return redirect(url_for('dashboard'))

@app.route('/submit-review', methods=['POST'])
def submit_review():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    review_text = request.form.get('review_text', '').strip()
    if not review_text:
        flash("Review text cannot be empty.", "error")
        return redirect(url_for('dashboard'))
        
    db = get_db()
    try:
        db.execute('''
            INSERT INTO reviews (user_id, review_text)
            VALUES (?, ?)
        ''', (session['user_id'], review_text))
        db.commit()
        flash("Thank you! Your feedback has been logged.", "success")
    except Exception as e:
        db.rollback()
        flash("Failed to submit feedback.", "error")
        
    return redirect(url_for('dashboard'))


# -------------------------------------------------------------
# PRIVATE ADMIN PORTAL ROUTES
# -------------------------------------------------------------
@app.route('/admin-portal')
def admin_portal():
    db = get_db()
    
    # Real-time count of active hot subscribers
    active_hot_count = get_active_hot_subscribers()
    
    # Retrieve all users for status management
    users_list = query_db("""
        SELECT id, name, email, phone_number, plan_tier, protein_upgrade, current_status 
        FROM users
        ORDER BY current_status ASC, name ASC
    """)
    
    # Retrieve all orders in system with customer information
    orders_list = query_db("""
        SELECT o.id, o.order_number, o.amount_due, o.payment_status, o.date_created,
               u.id as user_id, u.name, u.phone_number, u.plan_tier, u.protein_upgrade
        FROM orders o
        JOIN users u ON o.user_id = u.id
        ORDER BY o.id DESC
    """)
    
    # Calculate Weekend Kitchen Prep Summary metrics
    # Let's count totals from active orders in the database for this prep cycle
    standard_orders_count = 0
    gym_orders_count = 0
    
    for order in orders_list:
        # Standard: protein_upgrade = 0. Gym: protein_upgrade = 1.
        if order['protein_upgrade'] == 1:
            gym_orders_count += 1
        else:
            standard_orders_count += 1
            
    # Estimated total pounds of bulk chicken:
    # Standard: 5 meals * 6oz = 30oz = 1.875 lbs of chicken
    # Gym: 5 meals * 8oz = 40oz = 2.500 lbs of chicken
    std_chicken_lbs = standard_orders_count * 1.875
    gym_chicken_lbs = gym_orders_count * 2.5
    total_chicken_lbs = std_chicken_lbs + gym_chicken_lbs
    
    # Standard container count: 5 meals per order
    standard_containers = standard_orders_count * 5
    gym_containers = gym_orders_count * 5
    
    kitchen_prep = {
        'total_orders': len(orders_list),
        'standard_orders': standard_orders_count,
        'gym_orders': gym_orders_count,
        'standard_containers': standard_containers,
        'gym_containers': gym_containers,
        'chicken_lbs': round(total_chicken_lbs, 2)
    }
    
    # Retrieve all submitted reviews
    reviews_list = query_db("""
        SELECT r.id, r.review_text, r.date_created, u.name, u.email
        FROM reviews r
        JOIN users u ON r.user_id = u.id
        ORDER BY r.id DESC
    """)
    
    # Retrieve priority waitlist queue
    waitlist_queue = query_db("""
        SELECT id, name, email, phone_number, plan_tier, protein_upgrade, date_created 
        FROM waitlist
        ORDER BY id ASC
    """)
    
    return render_template(
        'admin.html',
        active_hot_count=active_hot_count,
        hot_cap=HOT_MEAL_CAP,
        users=users_list,
        orders=orders_list,
        kitchen_prep=kitchen_prep,
        reviews=reviews_list,
        waitlist=waitlist_queue
    )

# AJAX API: Mark Paid
@app.route('/admin/order/<int:order_id>/pay', methods=['POST'])
def admin_mark_paid(order_id):
    db = get_db()
    try:
        db.execute("UPDATE orders SET payment_status = 'Paid' WHERE id = ?", (order_id,))
        db.commit()
        return jsonify({'success': True, 'message': f'Order {order_id} marked as Paid.'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# AJAX API: Cancel / Remove Member (Status -> Paused)
@app.route('/admin/user/<int:user_id>/pause', methods=['POST'])
def admin_pause_user(user_id):
    db = get_db()
    try:
        db.execute("UPDATE users SET current_status = 'Paused' WHERE id = ?", (user_id,))
        db.commit()
        # Get count to update UI
        active_hot = get_active_hot_subscribers()
        return jsonify({'success': True, 'message': 'Member subscription paused successfully.', 'active_hot': active_hot})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# AJAX API: Resume Member (Status -> Active)
@app.route('/admin/user/<int:user_id>/resume', methods=['POST'])
def admin_resume_user(user_id):
    db = get_db()
    # Check cap first if user is a 'Hot' tier user
    user = query_db("SELECT plan_tier, current_status FROM users WHERE id = ?", (user_id,), one=True)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
        
    if user['plan_tier'] == 'Hot':
        active_hot = get_active_hot_subscribers()
        if active_hot >= HOT_MEAL_CAP:
            return jsonify({
                'success': False, 
                'error': f'Daily Fresh-Hot tier cap ({HOT_MEAL_CAP}) has been reached. Cannot resume subscription.'
            }), 400
            
    try:
        db.execute("UPDATE users SET current_status = 'Active' WHERE id = ?", (user_id,))
        db.commit()
        active_hot = get_active_hot_subscribers()
        return jsonify({'success': True, 'message': 'Member subscription resumed successfully.', 'active_hot': active_hot})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# AJAX API: Delete Member
@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
def admin_delete_user(user_id):
    db = get_db()
    try:
        # Also clean up orders to maintain schema integrity
        db.execute("DELETE FROM orders WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        active_hot = get_active_hot_subscribers()
        return jsonify({'success': True, 'message': 'Member account and orders removed permanently.', 'active_hot': active_hot})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# -------------------------------------------------------------
# HEALTH CHECK ENDPOINT (used by keep-alive pinger)
# -------------------------------------------------------------
@app.route('/health')
def health_check():
    return jsonify({'status': 'ok'}), 200

# -------------------------------------------------------------
# KEEP-ALIVE SELF-PINGER  (prevents Render free-tier cold starts)
# Pings /health every 4.5 minutes so the instance never sleeps.
# -------------------------------------------------------------
def _keep_alive():
    # Wait 30s after startup before first ping so the server is ready
    time.sleep(30)
    base_url = os.environ.get('RENDER_EXTERNAL_URL', 'http://localhost:5000')
    url = base_url.rstrip('/') + '/health'
    while True:
        try:
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass  # silently ignore network hiccups
        time.sleep(270)  # 4.5 minutes

_pinger = threading.Thread(target=_keep_alive, daemon=True)
_pinger.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
