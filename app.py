import sqlite3
import os
import sys
import traceback
from flask import Flask, jsonify, request, render_template
from flask.templating import TemplateNotFound
from flask_cors import CORS
from datetime import datetime

# --- Basic Setup ---
app = Flask(__name__)
CORS(app)

DATABASE_FILE = 'inventory.db'

def connect_to_database():
    """Connects to the SQLite database."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DATABASE_FILE)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row # Allows accessing columns by name
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        return None

def setup_database():
    """Creates the necessary database tables if they don't exist."""
    conn = connect_to_database()
    if conn is None:
        print("FATAL: Could not connect to the database to run setup.")
        return
        
    try:
        cursor = conn.cursor()
        # Create CUSTOMER table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS CUSTOMER (
                CUSTOMER_ID INTEGER PRIMARY KEY AUTOINCREMENT,
                CUSTOMER_NAME TEXT NOT NULL,
                MOBILE_NO TEXT NOT NULL UNIQUE,
                CUSTOMER_TYPE TEXT NOT NULL CHECK(CUSTOMER_TYPE IN ('WHOLESALE', 'RETAIL', 'HOTEL-LINE'))
            )
        """)
        # Create INVENTORY table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS INVENTORY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                BRAND TEXT,
                PRODUCT TEXT,
                CATEGORY TEXT,
                STOCK INT,
                MRP INT NOT NULL,
                PURCHASE_RATE INT NOT NULL,
                WHOLESALE_RATE INT NOT NULL,
                RETAIL_RATE INT NOT NULL,
                HOTEL_RATE INT NOT NULL,
                UNIQUE (BRAND, PRODUCT)
            )
        """)
        # *** FIX: Schema updated to match the new screenshot exactly ***
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS BILLS (
                BILL_ID INTEGER PRIMARY KEY AUTOINCREMENT,
                CUSTOMER_ID INT,
                TOTAL_ITEMS INT NOT NULL,
                BILL_AMOUNT REAL NOT NULL,
                TAX_AMOUNT REAL,
                DISCOUNT_AMOUNT REAL DEFAULT 0,
                TOTAL_AMOUNT REAL,
                PROFIT_EARNED REAL,
                PAYMENT_METHOD TEXT CHECK(PAYMENT_METHOD IN ('ONLINE', 'CASH', 'CREDIT', 'CARD')),
                PAYMENT_DATE DATE,
                STATUS TEXT DEFAULT 'SUCCESSFUL' CHECK(STATUS IN ('SUCCESSFUL', 'PENDING', 'FAILED')),
                FOREIGN KEY(CUSTOMER_ID) REFERENCES CUSTOMER(CUSTOMER_ID)
            )
        """)
        conn.commit()
        print("Database setup complete. Tables are ready.")
    except sqlite3.Error as e:
        print(f"Database setup error: {e}")
    finally:
        if conn:
            conn.close()

# --- Web Server Routes ---

@app.route('/api/customers', methods=['GET', 'POST'])
def manage_customers():
    """
    Handles fetching customer suggestions (GET) or 
    getting/creating a customer ID for a bill (POST).
    """
    conn = connect_to_database()
    if conn is None:
        return jsonify({"error": "Database connection failed."}), 500

    try:
        if request.method == 'POST':
            data = request.get_json()
            customer_name = data.get('name')
            mobile_no = data.get('phone')
            customer_type = data.get('type')

            if not all([customer_name, mobile_no, customer_type]):
                return jsonify({"error": "Missing data"}), 400

            cursor = conn.cursor()
            cursor.execute("SELECT CUSTOMER_ID FROM CUSTOMER WHERE MOBILE_NO = ?", (mobile_no,))
            existing_customer = cursor.fetchone()
            
            if existing_customer:
                customer_id = existing_customer['CUSTOMER_ID']
                message = "Existing customer ID retrieved."
            else:
                customer_type_upper = customer_type.upper()
                allowed_types = ['WHOLESALE', 'RETAIL', 'HOTEL-LINE']
                if customer_type_upper not in allowed_types:
                    return jsonify({"error": f"Invalid customer_type '{customer_type}'."}), 400

                cursor.execute(
                    "INSERT INTO CUSTOMER (CUSTOMER_NAME, MOBILE_NO, CUSTOMER_TYPE) VALUES (?, ?, ?)",
                    (customer_name, mobile_no, customer_type_upper)
                )
                customer_id = cursor.lastrowid
                message = "New customer created."
                conn.commit()

            return jsonify({"message": message, "customer_id": customer_id}), 201

        else: # GET request for suggestions
            search_term = request.args.get('term', '')
            if not search_term: return jsonify([])
            cursor = conn.cursor()
            query = "SELECT CUSTOMER_NAME, MOBILE_NO, CUSTOMER_TYPE FROM CUSTOMER WHERE TRIM(CUSTOMER_NAME) LIKE ?"
            cursor.execute(query, (f'{search_term}%',))
            rows = cursor.fetchall()
            customers = [{"name": row['CUSTOMER_NAME'].strip(), "mobile": row['MOBILE_NO'], "type": row['CUSTOMER_TYPE'].title()} for row in rows]
            return jsonify(customers)

    except Exception as e:
        print("\n" + "="*50 + "\n!!! UNEXPECTED ERROR IN manage_customers !!!"); traceback.print_exc(); print("="*50 + "\n")
        if conn: conn.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500
    finally:
        if conn: conn.close()

@app.route('/api/process-bill', methods=['POST'])
def process_bill():
    """Saves the bill, updates inventory, and calculates profit."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data received."}), 400

    customer_id = data.get('customer_id')
    products = data.get('products')
    payment_method = data.get('payment_method', '').upper()
    
    try:
        subtotal = float(data.get('subtotal', 0))
        tax = float(data.get('tax', 0))
        total = float(data.get('total', 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount format. Amounts must be numbers."}), 400

    if not all([customer_id, products, payment_method]):
        return jsonify({"error": "Missing critical bill data (customer, products, or payment method)."}), 400

    conn = connect_to_database()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT CUSTOMER_TYPE FROM CUSTOMER WHERE CUSTOMER_ID = ?", (customer_id,))
        customer_row = cursor.fetchone()
        if not customer_row:
            return jsonify({"error": "Customer not found."}), 404
        customer_type = customer_row['CUSTOMER_TYPE']

        price_column_map = {'WHOLESALE': 'WHOLESALE_RATE', 'RETAIL': 'RETAIL_RATE', 'HOTEL-LINE': 'HOTEL_RATE'}
        price_column = price_column_map.get(customer_type)
        total_profit_earned = 0.0
        valid_products_count = 0

        for product in products:
            product_name = product.get('name')
            try:
                quantity_sold = int(product.get('quantity'))
            except (ValueError, TypeError):
                continue

            if not product_name or quantity_sold <= 0:
                continue
            
            valid_products_count += 1
            
            query = f"""
                SELECT PURCHASE_RATE, {price_column} as SELLING_PRICE 
                FROM INVENTORY 
                WHERE (TRIM(UPPER(BRAND)) || ' ' || TRIM(UPPER(PRODUCT))) = ?
            """
            cursor.execute(query, (product_name.upper(),))
            rates = cursor.fetchone()
            
            if rates and rates['PURCHASE_RATE'] is not None and rates['SELLING_PRICE'] is not None:
                total_profit_earned += (rates['SELLING_PRICE'] - rates['PURCHASE_RATE']) * quantity_sold
            
            update_sql = "UPDATE INVENTORY SET STOCK = STOCK - ? WHERE (TRIM(UPPER(BRAND)) || ' ' || TRIM(UPPER(PRODUCT))) = ?"
            cursor.execute(update_sql, (quantity_sold, product_name.upper()))

        if valid_products_count == 0:
            return jsonify({"error": "No valid products to bill."}), 400

        payment_map = {'CASH': 'CASH', 'CARD': 'CARD', 'CREDIT': 'CREDIT', 'UPI': 'ONLINE'}
        db_payment_method = payment_map.get(payment_method, 'CASH')
        
        # *** FIX: Using new column names (TAX_AMOUNT, STATUS) in the INSERT statement ***
        bill_sql = """
            INSERT INTO BILLS (CUSTOMER_ID, TOTAL_ITEMS, BILL_AMOUNT, TAX_AMOUNT, TOTAL_AMOUNT, PROFIT_EARNED, PAYMENT_METHOD, PAYMENT_DATE, STATUS)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        cursor.execute(bill_sql, (customer_id, valid_products_count, subtotal, tax, total, total_profit_earned, db_payment_method, datetime.now().strftime("%Y-%m-%d"), 'SUCCESSFUL'))
        bill_id = cursor.lastrowid
        conn.commit()
        return jsonify({"message": f"Bill #{bill_id} generated successfully!", "bill_id": bill_id}), 201

    except Exception as e:
        if conn: conn.rollback()
        print("\n" + "="*50 + "\n!!! UNEXPECTED ERROR IN process_bill !!!"); traceback.print_exc(); print("="*50 + "\n")
        return jsonify({"error": "An internal server error occurred while processing the bill."}), 500
    finally:
        if conn: conn.close()

@app.route('/api/bills', methods=['GET'])
def get_bills():
    """Fetches all bill records for the history page."""
    conn = connect_to_database()
    if conn is None:
        return jsonify({"error": "Database connection failed."}), 500
    try:
        cursor = conn.cursor()
        # *** FIX: Selecting from the new column names to send to the frontend ***
        query = """
            SELECT
                b.BILL_ID,
                c.CUSTOMER_NAME,
                b.TOTAL_ITEMS,
                b.BILL_AMOUNT,
                b.TAX_AMOUNT,
                b.DISCOUNT_AMOUNT,
                b.TOTAL_AMOUNT,
                b.PROFIT_EARNED,
                b.PAYMENT_METHOD,
                b.PAYMENT_DATE,
                b.STATUS
            FROM BILLS b
            JOIN CUSTOMER c ON b.CUSTOMER_ID = c.CUSTOMER_ID
            ORDER BY b.BILL_ID DESC
        """
        cursor.execute(query)
        bills = [dict(row) for row in cursor.fetchall()]
        return jsonify(bills)
    except Exception as e:
        print("\n" + "="*50 + "\n!!! UNEXPECTED ERROR IN get_bills !!!"); traceback.print_exc(); print("="*50 + "\n")
        return jsonify({"error": "Failed to fetch bill history."}), 500
    finally:
        if conn:
            conn.close()

@app.route('/')
def index():
    """Serves the main billing page."""
    try:
        return render_template('billing.html')
    except TemplateNotFound:
        return "<h1>Error: Template Not Found</h1><p>Please make sure you have a folder named 'templates' in your project directory, and that 'billing.html' is inside it.</p>", 404
    except Exception as e:
        traceback.print_exc()
        return "<h1>An unexpected server error occurred</h1>", 500

@app.route('/history')
def history():
    """Serves the bill history page."""
    try:
        return render_template('history.html')
    except TemplateNotFound:
        return "<h1>Error: Template Not Found</h1><p>Please make sure 'history.html' is inside your 'templates' folder.</p>", 404

@app.route('/api/products', methods=['GET'])
def get_product_suggestions():
    """Fetches product suggestions and prices."""
    search_term, customer_type = request.args.get('term', ''), request.args.get('customer_type', '').upper()
    if not search_term or not customer_type: return jsonify([])
    price_column_map = {'WHOLESALE': 'WHOLESALE_RATE', 'RETAIL': 'RETAIL_RATE', 'HOTEL-LINE': 'HOTEL_RATE'}
    price_column = price_column_map.get(customer_type)
    if not price_column: return jsonify({"error": "Invalid customer type"}), 400
    conn = connect_to_database()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500
    try:
        cursor = conn.cursor()
        query = f"SELECT BRAND, PRODUCT, {price_column} as PRICE FROM INVENTORY WHERE (UPPER(BRAND) || ' ' || UPPER(PRODUCT)) LIKE ?"
        cursor.execute(query, (f'%{search_term.upper()}%',))
        rows = cursor.fetchall()
        products = [{"name": f"{row['BRAND']} {row['PRODUCT']}".strip(), "price": row['PRICE']} for row in rows]
        return jsonify(products)
    except Exception as e:
        print("\n" + "="*50 + "\n!!! UNEXPECTED ERROR IN get_product_suggestions !!!"); traceback.print_exc(); print("="*50 + "\n")
        return jsonify({"error": "Failed to query database."}), 500
    finally:
        if conn: conn.close()

# --- Main Execution ---
if __name__ == '__main__':
    if not os.path.exists(DATABASE_FILE):
        print(f"Database file '{DATABASE_FILE}' not found. Will be created.")
    setup_database()
    print(f"Starting server, using database '{DATABASE_FILE}'...")
    app.run(host='0.0.0.0', port=5000, debug=True)

