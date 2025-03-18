from flask import Flask, request, jsonify
from datetime import datetime
import os
from flask_cors import CORS
from functools import wraps
import requests
import time
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app)

# Configuration
try:
    PORT = int(os.environ.get('PORT', '8080'))
except ValueError:
    PORT = 8080

HOST = '0.0.0.0'
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
API_KEY = os.environ.get('API_KEY', 'STOVE_ADMIN_2024_SECRET')

# Database configuration
PGHOST = os.environ.get('PGHOST', 'postgres.railway.internal')
PGDATABASE = os.environ.get('PGDATABASE', 'railway')
PGUSER = os.environ.get('PGUSER', 'postgres')
PGPASSWORD = os.environ.get('PGPASSWORD', 'WMLcLSDAepSLgNOzSJDZkOWBCIqGlbUG')
PGPORT = os.environ.get('PGPORT', '5432')

# TCP Proxy details for local development
TCP_PROXY_DOMAIN = os.environ.get('RAILWAY_TCP_PROXY_DOMAIN', 'switchyard.proxy.rlwy.net')
TCP_PROXY_PORT = os.environ.get('RAILWAY_TCP_PROXY_PORT', '27152')

# Construct database URL from components
DATABASE_URL = f"postgresql://{PGUSER}:{PGPASSWORD}@{PGHOST}:{PGPORT}/{PGDATABASE}"

def get_db_connection():
    """Get a database connection"""
    try:
        # First try internal connection (for Railway environment)
        try:
            print(f"Attempting to connect to database at {PGHOST}:{PGPORT}")
            conn = psycopg2.connect(
                host=PGHOST,
                database=PGDATABASE,
                user=PGUSER,
                password=PGPASSWORD,
                port=PGPORT
            )
            print("Successfully connected to database using internal connection")
            return conn
        except psycopg2.OperationalError as e:
            # If internal connection fails, try TCP proxy connection
            print("Internal connection failed, attempting TCP proxy connection")
            proxy_url = f"postgresql://{PGUSER}:{PGPASSWORD}@{TCP_PROXY_DOMAIN}:{TCP_PROXY_PORT}/{PGDATABASE}"
            print(f"Connecting via TCP proxy: {proxy_url.replace(PGPASSWORD, '****')}")
            conn = psycopg2.connect(proxy_url)
            print("Successfully connected to database using TCP proxy")
            return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        raise

def init_db():
    """Initialize PostgreSQL database for license logs"""
    retries = 3
    last_error = None
    
    for attempt in range(retries):
        try:
            print(f"Initializing database (attempt {attempt + 1}/{retries})")
            
            conn = get_db_connection()
            cur = conn.cursor()
            
            # Create logs table
            cur.execute('''CREATE TABLE IF NOT EXISTS license_logs
                     (id SERIAL PRIMARY KEY,
                      license_key TEXT NOT NULL,
                      user_id TEXT,
                      action TEXT NOT NULL,
                      status TEXT NOT NULL,
                      ip_address TEXT,
                      device_info TEXT,
                      timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      additional_info TEXT)''')
            
            # Create summary table
            cur.execute('''CREATE TABLE IF NOT EXISTS license_stats
                     (license_key TEXT PRIMARY KEY,
                      total_validations INTEGER DEFAULT 0,
                      last_validation TIMESTAMP,
                      active_devices INTEGER DEFAULT 0,
                      failed_attempts INTEGER DEFAULT 0,
                      last_ip TEXT)''')
            
            conn.commit()
            print("Database tables created successfully")
            
            # Test write access
            cur.execute("INSERT INTO license_logs (license_key, user_id, action, status) VALUES (%s, %s, %s, %s)",
                     ('TEST-KEY', 'test-user', 'init', 'test'))
            conn.commit()
            print("Database write test successful")
            return True
            
        except Exception as e:
            last_error = e
            print(f"Error initializing database (attempt {attempt + 1}/{retries}): {e}")
            time.sleep(1)
        finally:
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                conn.close()
    
    if last_error:
        print(f"Failed to initialize database after {retries} attempts. Last error: {last_error}")
        raise last_error

def require_api_key(f):
    """Decorator to check API key"""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if api_key != API_KEY:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/', methods=['GET'])
def index():
    """Root endpoint"""
    return jsonify({
        'service': 'STOVE License Logger',
        'status': 'online',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat()
    })

# Add this new function to test database connection
def test_db_connection():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT 1')  # Simple test query
        conn.close()
        return True
    except Exception as e:
        print(f"Database connection test failed: {e}")
        return False

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'license-logger',
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route('/api/log/validation', methods=['POST'])
@require_api_key
def log_validation():
    """Log a license validation attempt"""
    try:
        data = request.get_json()
        print(f"Received log request: {data}")
        
        license_key = data.get('license_key')
        user_id = data.get('user_id', 'unknown')
        status = data.get('status', 'unknown')
        device_info = data.get('device_info', {})
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Log the validation attempt
        cur.execute('''INSERT INTO license_logs 
                    (license_key, user_id, action, status, ip_address, device_info, additional_info)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (license_key, user_id, 'validation', status,
                request.remote_addr,
                str(device_info),
                str(data.get('additional_info', ''))))
        
        # Update statistics using upsert
        cur.execute('''INSERT INTO license_stats 
                    (license_key, total_validations, last_validation, active_devices, failed_attempts, last_ip)
                    VALUES (%s, 1, CURRENT_TIMESTAMP,
                        CASE WHEN %s = 'valid' THEN 1 ELSE 0 END,
                        CASE WHEN %s != 'valid' THEN 1 ELSE 0 END,
                        %s)
                    ON CONFLICT (license_key) DO UPDATE SET
                        total_validations = license_stats.total_validations + 1,
                        last_validation = CURRENT_TIMESTAMP,
                        active_devices = CASE 
                            WHEN %s = 'valid' 
                            THEN license_stats.active_devices + 1
                            ELSE license_stats.active_devices
                        END,
                        failed_attempts = CASE 
                            WHEN %s != 'valid' 
                            THEN license_stats.failed_attempts + 1
                            ELSE license_stats.failed_attempts
                        END,
                        last_ip = %s''',
                (license_key, status, status, request.remote_addr,
                 status, status, request.remote_addr))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Validation logged successfully'
        })
        
    except Exception as e:
        error_msg = f"Error logging validation: {str(e)}"
        print(error_msg)
        return jsonify({'error': error_msg}), 500

@app.route('/api/logs/search', methods=['GET'])
@require_api_key
def search_logs():
    """Search license logs with filters"""
    try:
        license_key = request.args.get('license_key')
        user_id = request.args.get('user_id')
        status = request.args.get('status')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        query = 'SELECT * FROM license_logs WHERE 1=1'
        params = []
        
        if license_key:
            query += ' AND license_key = %s'
            params.append(license_key)
        if user_id:
            query += ' AND user_id = %s'
            params.append(user_id)
        if status:
            query += ' AND status = %s'
            params.append(status)
        if start_date:
            query += ' AND timestamp >= %s'
            params.append(start_date)
        if end_date:
            query += ' AND timestamp <= %s'
            params.append(end_date)
            
        query += ' ORDER BY timestamp DESC LIMIT 1000'
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(query, params)
        logs = [dict(zip([col[0] for col in c.description], row)) 
                for row in c.fetchall()]
        conn.close()
        
        return jsonify({'logs': logs})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/license/<license_key>', methods=['GET'])
@require_api_key
def get_license_stats(license_key):
    """Get statistics for a specific license"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get basic stats
        c.execute('SELECT * FROM license_stats WHERE license_key = %s', (license_key,))
        stats = dict(zip([col[0] for col in c.description], c.fetchone() or []))
        
        # Get recent activity
        c.execute('''SELECT * FROM license_logs 
                    WHERE license_key = %s 
                    ORDER BY timestamp DESC LIMIT 10''', (license_key,))
        recent_activity = [dict(zip([col[0] for col in c.description], row)) 
                        for row in c.fetchall()]
        
        conn.close()
        
        return jsonify({
            'stats': stats,
            'recent_activity': recent_activity
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/summary', methods=['GET'])
@require_api_key
def get_summary_stats():
    """Get summary statistics for all licenses"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get overall statistics
        c.execute('''SELECT 
                        COUNT(DISTINCT license_key) as total_licenses,
                        SUM(total_validations) as total_validations,
                        SUM(active_devices) as total_active_devices,
                        SUM(failed_attempts) as total_failed_attempts
                    FROM license_stats''')
        summary = dict(zip([col[0] for col in c.description], c.fetchone()))
        
        # Get recent validations
        c.execute('''SELECT * FROM license_logs 
                    ORDER BY timestamp DESC LIMIT 20''')
        recent_logs = [dict(zip([col[0] for col in c.description], row)) 
                    for row in c.fetchall()]
        
        conn.close()
        
        return jsonify({
            'summary': summary,
            'recent_activity': recent_logs
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs/user-activity', methods=['GET'])
@require_api_key
def get_user_activity():
    """Get user login activity"""
    try:
        license_key = request.args.get('license_key')
        days = int(request.args.get('days', 7))  # Default to 7 days
        
        conn = get_db_connection()
        c = conn.cursor()
        
        query = '''
            SELECT 
                timestamp,
                license_key,
                user_id,
                status,
                ip_address,
                device_info,
                additional_info
            FROM license_logs 
            WHERE license_key = %s 
            AND timestamp >= CURRENT_TIMESTAMP - INTERVAL '1 day' * %s
            ORDER BY timestamp DESC
        '''
        
        c.execute(query, (license_key, days))
        
        activity = [dict(zip([col[0] for col in c.description], row)) 
                for row in c.fetchall()]
        
        # Get summary statistics
        c.execute('''
            SELECT 
                COUNT(*) as total_attempts,
                SUM(CASE WHEN status = 'valid' THEN 1 ELSE 0 END) as successful_logins,
                SUM(CASE WHEN status != 'valid' THEN 1 ELSE 0 END) as failed_attempts,
                COUNT(DISTINCT ip_address) as unique_ips,
                COUNT(DISTINCT device_info) as unique_devices
            FROM license_logs 
            WHERE license_key = %s
            AND timestamp >= CURRENT_TIMESTAMP - INTERVAL '1 day' * %s
        ''', (license_key, days))
        
        stats = dict(zip([col[0] for col in c.description], c.fetchone()))
        
        conn.close()
        
        return jsonify({
            'activity': activity,
            'statistics': stats,
            'period': f'Last {days} days'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Add this function to check database content
@app.route('/api/debug/db-status', methods=['GET'])
@require_api_key
def debug_db_status():
    """Debug endpoint to check database status"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get table counts
        c.execute("SELECT COUNT(*) FROM license_logs")
        log_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM license_stats")
        stats_count = c.fetchone()[0]
        
        # Get recent logs
        c.execute("""SELECT * FROM license_logs 
                    ORDER BY timestamp DESC LIMIT 5""")
        recent_logs = [dict(zip([col[0] for col in c.description], row)) 
                    for row in c.fetchall()]
        
        conn.close()
        
        return jsonify({
            'database_path': DATABASE_URL,
            'log_count': log_count,
            'stats_count': stats_count,
            'recent_logs': recent_logs,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'database_path': DATABASE_URL
        }), 500

# Add this to help debug database location
print(f"Database path: {DATABASE_URL}")

if __name__ == '__main__':
    try:
        print(f"Starting license logger server on {HOST}:{PORT}")
        print(f"Debug mode: {DEBUG}")
        
        # Initialize database
        print("Initializing database...")
        init_db()
        print("Database initialized successfully")
        
        app.run(host=HOST, port=PORT, debug=DEBUG)
            
    except Exception as e:
        print(f"Startup error: {e}")
        raise