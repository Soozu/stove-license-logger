from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import os
from flask_cors import CORS
from functools import wraps
import requests
import time
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse
import json

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
        # First try TCP proxy connection
        proxy_url = f"postgresql://{PGUSER}:{PGPASSWORD}@{TCP_PROXY_DOMAIN}:{TCP_PROXY_PORT}/{PGDATABASE}"
        print(f"Connecting via TCP proxy: {proxy_url.replace(PGPASSWORD, '****')}")
        conn = psycopg2.connect(proxy_url)
        print("Successfully connected to database using TCP proxy")
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        raise

def init_db():
    """Initialize PostgreSQL database for license management"""
    retries = 3
    last_error = None
    
    for attempt in range(retries):
        try:
            print(f"Initializing database (attempt {attempt + 1}/{retries})")
            
            conn = get_db_connection()
            cur = conn.cursor()
            
            # Create users table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
            ''')
            
            # Create licenses table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS licenses (
                    id SERIAL PRIMARY KEY,
                    license_key TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    type TEXT NOT NULL DEFAULT 'premium',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    max_devices INTEGER DEFAULT 1,
                    CONSTRAINT chk_status CHECK (status IN ('active', 'inactive', 'expired')),
                    CONSTRAINT chk_type CHECK (type IN ('premium', 'basic', 'trial'))
                );
                CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses(license_key);
                CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses(status);
            ''')
            
            # Create user_licenses table (many-to-many relationship)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS user_licenses (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    license_id INTEGER REFERENCES licenses(id) ON DELETE CASCADE,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, license_id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_licenses_user ON user_licenses(user_id);
                CREATE INDEX IF NOT EXISTS idx_user_licenses_license ON user_licenses(license_id);
            ''')
            
            # Create license_activity table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS license_activity (
                    id SERIAL PRIMARY KEY,
                    license_id INTEGER REFERENCES licenses(id) ON DELETE CASCADE,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ip_address TEXT,
                    device_info TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT chk_action CHECK (action IN ('validation', 'activation', 'deactivation', 'renewal'))
                );
                CREATE INDEX IF NOT EXISTS idx_activity_license ON license_activity(license_id);
                CREATE INDEX IF NOT EXISTS idx_activity_user ON license_activity(user_id);
                CREATE INDEX IF NOT EXISTS idx_activity_created ON license_activity(created_at);
            ''')
            
            # Create device_activations table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS device_activations (
                    id SERIAL PRIMARY KEY,
                    license_id INTEGER REFERENCES licenses(id) ON DELETE CASCADE,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    device_id TEXT NOT NULL,
                    device_name TEXT,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT true,
                    UNIQUE(license_id, device_id)
                );
                CREATE INDEX IF NOT EXISTS idx_device_license ON device_activations(license_id);
                CREATE INDEX IF NOT EXISTS idx_device_user ON device_activations(user_id);
                CREATE INDEX IF NOT EXISTS idx_device_active ON device_activations(is_active);
            ''')
            
            conn.commit()
            print("Database tables created successfully")
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

@app.route('/api/verify-license', methods=['POST'])
@require_api_key
def verify_license():
    """Verify a license key and log the attempt"""
    try:
        data = request.get_json()
        license_key = data.get('license_key')
        user_id = data.get('user_id')
        device_info = data.get('device_info', {})
        device_id = device_info.get('device_id', '')
        
        if not license_key or not user_id:
            return jsonify({
                'valid': False,
                'message': 'License key and user ID are required'
            }), 400
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        try:
            # First, insert or get the user
            cur.execute('''
                INSERT INTO users (user_id)
                VALUES (%s)
                ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
                RETURNING id
            ''', (user_id,))
            user_db_id = cur.fetchone()['id']
            
            # Then, insert the license if it doesn't exist
            cur.execute('''
                INSERT INTO licenses (license_key, status, type, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (license_key) DO UPDATE SET 
                    status = EXCLUDED.status,
                    type = EXCLUDED.type,
                    expires_at = EXCLUDED.expires_at
                RETURNING id, status, type, expires_at, max_devices
            ''', (
                license_key,
                'active',
                'premium',
                datetime.now() + timedelta(days=30)  # Default 30-day license
            ))
            license_data = cur.fetchone()
            
            # Link user to license
            cur.execute('''
                INSERT INTO user_licenses (user_id, license_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, license_id) DO NOTHING
            ''', (user_db_id, license_data['id']))
            
            # Register device
            if device_id:
                cur.execute('''
                    INSERT INTO device_activations 
                    (license_id, user_id, device_id, device_name, is_active)
                    VALUES (%s, %s, %s, %s, true)
                    ON CONFLICT (license_id, device_id) 
                    DO UPDATE SET last_active = CURRENT_TIMESTAMP, is_active = true
                ''', (
                    license_data['id'],
                    user_db_id,
                    device_id,
                    device_info.get('device_name', '')
                ))
            
            # Log the activity
            cur.execute('''
                INSERT INTO license_activity 
                (license_id, user_id, action, status, ip_address, device_info)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (
                license_data['id'],
                user_db_id,
                'validation',
                'success',
                request.remote_addr,
                json.dumps(device_info)
            ))
            
            # Get active device count
            cur.execute('''
                SELECT COUNT(*) as active_devices
                FROM device_activations
                WHERE license_id = %s AND is_active = true
            ''', (license_data['id'],))
            active_devices = cur.fetchone()['active_devices']
            
            # Commit all changes
            conn.commit()
            
            # Calculate days remaining
            days_remaining = (license_data['expires_at'] - datetime.now()).days
            
            return jsonify({
                'valid': True,
                'type': license_data['type'],
                'status': license_data['status'],
                'expires_at': license_data['expires_at'].isoformat(),
                'days_remaining': days_remaining,
                'active_devices': active_devices,
                'max_devices': license_data['max_devices']
            })
            
        except Exception as e:
            conn.rollback()
            print(f"Database error: {e}")
            return jsonify({
                'valid': False,
                'message': 'Database error occurred'
            }), 500
            
    except Exception as e:
        print(f"Error verifying license: {e}")
        return jsonify({
            'valid': False,
            'message': 'Internal server error'
        }), 500
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

@app.route('/api/license/activity', methods=['GET'])
@require_api_key
def get_license_activity():
    """Get activity history for a license"""
    try:
        license_key = request.args.get('license_key')
        user_id = request.args.get('user_id')
        
        if not license_key:
            return jsonify({'error': 'License key is required'}), 400
            
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        # Get license activity with user details
        cur.execute('''
            SELECT 
                la.created_at,
                la.action,
                la.status,
                la.ip_address,
                la.device_info,
                u.user_id,
                u.email,
                u.name
            FROM license_activity la
            JOIN licenses l ON la.license_id = l.id
            JOIN users u ON la.user_id = u.id
            WHERE l.license_key = %s
            ORDER BY la.created_at DESC
            LIMIT 50
        ''', (license_key,))
        
        activities = [dict(row) for row in cur.fetchall()]
        
        return jsonify({
            'activities': activities
        })
        
    except Exception as e:
        print(f"Error getting license activity: {e}")
        return jsonify({'error': 'Internal server error'}), 500
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

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