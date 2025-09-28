import sqlite3
import os
import sys
import traceback
from flask import Flask, jsonify, request, render_template
from flask.templating import TemplateNotFound
from flask_cors import CORS

# --- Basic Setup ---
# By default, Flask looks for templates in a folder named "templates".
# Removed `template_folder='.'` to revert to this standard behavior.
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
        # Create CUSTOMER table with the CHECK constraint to match your DB
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
    """Handles both fetching and adding customers."""
    conn = connect_to_database()
    if conn is None:
        return jsonify({"error": "Database connection failed."}), 500

    try:
        if request.method == 'POST':
            # Handle saving a new customer
            data = request.get_json()
            customer_name = data.get('name')
            mobile_no = data.get('phone')
            customer_type = data.get('type')

            if not all([customer_name, mobile_no, customer_type]):
                return jsonify({"error": "Missing data"}), 400

            cursor = conn.cursor()
            # Check if a customer with that mobile number already exists
            cursor.execute("SELECT CUSTOMER_NAME FROM CUSTOMER WHERE MOBILE_NO = ?", (mobile_no,))
            if cursor.fetchone():
                return jsonify({"message": "Customer already exists."}), 200

            # Convert customer_type to uppercase to match the DB constraint
            customer_type_upper = customer_type.upper()
            
            # Additional check to provide a cleaner error than the 500 IntegrityError
            allowed_types = ['WHOLESALE', 'RETAIL', 'HOTEL-LINE']
            if customer_type_upper not in allowed_types:
                return jsonify({
                    "error": f"Invalid customer_type '{customer_type}'.",
                    "allowed_values": [t.title() for t in allowed_types]
                }), 400

            cursor.execute(
                "INSERT INTO CUSTOMER (CUSTOMER_NAME, MOBILE_NO, CUSTOMER_TYPE) VALUES (?, ?, ?)",
                (customer_name, mobile_no, customer_type_upper)
            )
            conn.commit()
            return jsonify({"message": "New customer saved successfully."}), 201

        else: # GET request
            # Handle fetching customer suggestions
            search_term = request.args.get('term', '')
            if not search_term:
                return jsonify([])

            cursor = conn.cursor()
            query = "SELECT CUSTOMER_NAME, MOBILE_NO, CUSTOMER_TYPE FROM CUSTOMER WHERE TRIM(CUSTOMER_NAME) LIKE ?"
            cursor.execute(query, (f'{search_term}%',))
            rows = cursor.fetchall()
            
            customers = [
                # Return type as .title() so "WHOLESALE" becomes "Wholesale" for the dropdown
                {"name": row['CUSTOMER_NAME'].strip(), "mobile": row['MOBILE_NO'], "type": row['CUSTOMER_TYPE'].title()}
                for row in rows
            ]
            return jsonify(customers)

    except Exception as e:
        print("\n" + "="*50)
        print("!!! AN UNEXPECTED ERROR OCCURRED IN manage_customers !!!")
        traceback.print_exc()
        print("="*50 + "\n")
        if conn:
            conn.rollback()
        return jsonify({"error": "An internal server error occurred. Check the server console for the full traceback."}), 500
    finally:
        if conn:
            conn.close()

@app.route('/')
def index():
    """Serves the main billing page."""
    try:
        return render_template('billing.html')
    except TemplateNotFound:
        # This error message guides the user to fix the folder structure.
        print("\n" + "="*50)
        print("!!! CRITICAL ERROR: billing.html NOT FOUND !!!")
        print("Make sure the 'billing.html' file is located inside a folder named 'templates'.")
        print("Your folder structure should be:")
        print("  - /your_project_folder")
        print("    |-- app.py")
        print("    |-- inventory.db")
        print("    +-- /templates")
        print("        |-- billing.html")
        print("="*50 + "\n")
        return "<h1>Error: billing.html not found</h1><p>Please check the server console for instructions on how to fix this.</p>", 404
    except Exception as e:
        traceback.print_exc()
        return "<h1>An unexpected error occurred</h1>", 500


@app.route('/api/products', methods=['GET'])
def get_product_suggestions():
    """
    Handles the AJAX request for product suggestions.
    Fetches product name and the correct price based on customer type.
    """
    search_term = request.args.get('term', '')
    customer_type = request.args.get('customer_type', '').upper() # Convert to uppercase for DB query

    if not search_term or not customer_type:
        return jsonify([])

    # Determine which price column to use based on customer_type
    price_column_map = {
        'WHOLESALE': 'WHOLESALE_RATE',
        'RETAIL': 'RETAIL_RATE',
        'HOTEL-LINE': 'HOTEL_RATE'
    }
    price_column = price_column_map.get(customer_type)

    if not price_column:
        return jsonify({"error": "Invalid customer type"}), 400

    conn = connect_to_database()
    if conn is None:
        return jsonify({"error": "Database connection failed."}), 500

    try:
        cursor = conn.cursor()
        query = f"""
            SELECT BRAND, PRODUCT, {price_column} as PRICE
            FROM INVENTORY
            WHERE UPPER(BRAND) LIKE ? OR UPPER(PRODUCT) LIKE ?
        """
        like_term = f'%{search_term.upper()}%'
        cursor.execute(query, (like_term, like_term))
        rows = cursor.fetchall()

        products = []
        for row in rows:
            full_product_name = f"{row['BRAND']} {row['PRODUCT']}".strip()
            products.append({
                "name": full_product_name,
                "price": row['PRICE']
            })
        
        return jsonify(products)

    except Exception as e:
        print("\n" + "="*50)
        print("!!! AN UNEXPECTED ERROR OCCURRED IN get_product_suggestions !!!")
        traceback.print_exc()
        print("="*50 + "\n")
        return jsonify({"error": "Failed to query database. Check server console for details."}), 500
    finally:
        if conn:
            conn.close()

# --- Command-Line Interface (CLI) Tool ---

def run_cli_tool():
    """Runs an interactive command-line tool for database operations."""
    print("--- CLI Database Tool ---")
    
    conn = connect_to_database()
    if conn is None:
        print("Could not connect to the database. Exiting.")
        return
        
    cursor = conn.cursor()
    
    try:
        # Get customer details
        customer_name = input("Enter customer name: ")
        customer_phone = input("Enter customer phone: ")
        customer_type_input = input("Select Your Type:\n 1. Retail\n 2. Wholesale\n 3. Hotel-Line\n> ")
        customer_type = ""
        
        if customer_type_input == "1":
            customer_type = "RETAIL"
        elif customer_type_input == "2":
            customer_type = "WHOLESALE"
        elif customer_type_input == "3":
            # *** FIX: Corrected "HOTEL" to "HOTEL-LINE" to match schema ***
            customer_type = "HOTEL-LINE"
        else:
            print("Invalid customer type selected.")
            return

        # Add customer to database
        try:
            cursor.execute("INSERT INTO CUSTOMER (CUSTOMER_NAME, MOBILE_NO, CUSTOMER_TYPE) VALUES (?, ?, ?)", (customer_name, customer_phone, customer_type))
            conn.commit()
            print(f"\nSuccessfully added customer: {customer_name}")
        except sqlite3.IntegrityError:
            print(f"\nCustomer with phone number {customer_phone} already exists.")
        
        # Get product price
        product_brand = input("Enter Brand name to purchase: ")
        
        price_column_map = {
            'WHOLESALE': 'WHOLESALE_RATE',
            'RETAIL': 'RETAIL_RATE',
            # *** FIX: Corrected "HOTEL" to "HOTEL-LINE" to match schema ***
            'HOTEL-LINE': 'HOTEL_RATE'
        }
        price_column = price_column_map.get(customer_type)

        if price_column:
            cursor.execute(f"SELECT {price_column} FROM INVENTORY WHERE UPPER(BRAND) = ?", (product_brand.upper(),))
            result = cursor.fetchone()
            if result:
                print(f"Price for '{product_brand}' for a {customer_type.title()} customer is: {result[0]}")
            else:
                print(f"Product with brand '{product_brand}' not found in inventory.")
        
    except Exception as e:
        print(f"An error occurred: {e}")
        if conn:
            conn.rollback()
    finally:
        print("\n--- CLI Tool Finished ---")
        if conn:
            conn.close()

# --- Main Execution ---

if __name__ == '__main__':
    # Check for database file and create tables if necessary before starting.
    if not os.path.exists(DATABASE_FILE):
        print(f"Database file '{DATABASE_FILE}' not found. Will be created.")
    setup_database()

    # Check for CLI argument
    if len(sys.argv) > 1 and sys.argv[1].lower() == 'cli':
        run_cli_tool()
    else:
        print(f"Starting server, using database '{DATABASE_FILE}'...")
        app.run(host='0.0.0.0', port=5000, debug=True)

