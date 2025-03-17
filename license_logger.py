from flask import Flask, request, jsonify
from datetime import datetime
import sqlite3
import os
from flask_cors import CORS
from functools import wraps
import requests

app = Flask(__name__)
CORS(app)

# Configuration
PORT = int(os.environ.get('PORT', 8080))  # Railway.app uses port 8080 by default
HOST = os.environ.get('HOST', '0.0.0.0')
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
API_KEY = os.environ.get('API_KEY', 'STOVE_ADMIN_2024_SECRET')

# Database path
DB_PATH = os.environ.get('LOG_DB_PATH', 'license_logs.db')  # Default to current directory if not set

def ensure_db_directory():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

def init_db():
    """Initialize SQLite database for license logs"""
    try:
        ensure_db_directory()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Create logs table
        c.execute('''CREATE TABLE IF NOT EXISTS license_logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  license_key TEXT NOT NULL,
                  user_id TEXT,
                  action TEXT NOT NULL,
                  status TEXT NOT NULL,
                  ip_address TEXT,
                  device_info TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  additional_info TEXT)''')
        
        # Create summary table for quick statistics
        c.execute('''CREATE TABLE IF NOT EXISTS license_stats
                 (license_key TEXT PRIMARY KEY,
                  total_validations INTEGER DEFAULT 0,
                  last_validation DATETIME,
                  active_devices INTEGER DEFAULT 0,
                  failed_attempts INTEGER DEFAULT 0,
                  last_ip TEXT)''')
        
        # Check if tables are empty
        c.execute("SELECT COUNT(*) FROM license_logs")
        log_count = c.fetchone()[0]
        
        if log_count == 0:
            # Insert some initial test data
            print("Adding initial test data...")
            test_data = [
                ('STOVE-202403-TEST1', 'TEST1', 'validation', 'valid', 
                 '127.0.0.1', '{"os": "test"}', 'Initial test data'),
                ('STOVE-202403-TEST2', 'TEST2', 'validation', 'invalid', 
                 '127.0.0.1', '{"os": "test"}', 'Test invalid attempt'),
            ]
            
            c.executemany('''INSERT INTO license_logs 
                            (license_key, user_id, action, status, ip_address, 
                             device_info, additional_info)
                            VALUES (?, ?, ?, ?, ?, ?, ?)''', test_data)
            
            # Insert test statistics
            c.execute('''INSERT INTO license_stats 
                        (license_key, total_validations, last_validation, 
                         active_devices, failed_attempts, last_ip)
                        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)''',
                     ('STOVE-202403-TEST1', 1, 1, 0, '127.0.0.1'))
                     
        conn.commit()
        print(f"Database initialized at {DB_PATH}")
        print(f"Current log count: {log_count}")
        
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise
    finally:
        conn.close()

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
        conn = sqlite3.connect(DB_PATH)
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
        print(f"Received log request: {data}")  # Add debug logging
        
        license_key = data.get('license_key')
        user_id = data.get('user_id', 'unknown')
        status = data.get('status', 'unknown')
        device_info = data.get('device_info', {})
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Log the validation attempt
        c.execute('''INSERT INTO license_logs 
                    (license_key, user_id, action, status, ip_address, device_info, additional_info)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                 (license_key, user_id, 'validation', status,
                  request.remote_addr,
                  str(device_info),
                  str(data.get('additional_info', ''))))
        
        # Update statistics
        c.execute('''INSERT OR REPLACE INTO license_stats 
                    (license_key, total_validations, last_validation, active_devices, 
                     failed_attempts, last_ip)
                    VALUES (?, 
                           COALESCE((SELECT total_validations + 1 FROM license_stats 
                                   WHERE license_key = ?), 1),
                           CURRENT_TIMESTAMP,
                           CASE WHEN ? = 'valid' 
                                THEN COALESCE((SELECT active_devices FROM license_stats 
                                             WHERE license_key = ?), 0) + 1
                                ELSE COALESCE((SELECT active_devices FROM license_stats 
                                             WHERE license_key = ?), 0)
                           END,
                           CASE WHEN ? != 'valid' 
                                THEN COALESCE((SELECT failed_attempts FROM license_stats 
                                             WHERE license_key = ?), 0) + 1
                                ELSE COALESCE((SELECT failed_attempts FROM license_stats 
                                             WHERE license_key = ?), 0)
                           END,
                           ?)''',
                 (license_key, license_key, status, license_key, license_key, 
                  status, license_key, license_key, request.remote_addr))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
            query += ' AND license_key = ?'
            params.append(license_key)
        if user_id:
            query += ' AND user_id = ?'
            params.append(user_id)
        if status:
            query += ' AND status = ?'
            params.append(status)
        if start_date:
            query += ' AND timestamp >= ?'
            params.append(start_date)
        if end_date:
            query += ' AND timestamp <= ?'
            params.append(end_date)
            
        query += ' ORDER BY timestamp DESC LIMIT 1000'
        
        conn = sqlite3.connect(DB_PATH)
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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get basic stats
        c.execute('SELECT * FROM license_stats WHERE license_key = ?', (license_key,))
        stats = dict(zip([col[0] for col in c.description], c.fetchone() or []))
        
        # Get recent activity
        c.execute('''SELECT * FROM license_logs 
                    WHERE license_key = ? 
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
        conn = sqlite3.connect(DB_PATH)
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
        
        conn = sqlite3.connect(DB_PATH)
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
            WHERE license_key = ? 
            AND timestamp >= datetime('now', ?)
            ORDER BY timestamp DESC
        '''
        
        c.execute(query, (license_key, f'-{days} days'))
        
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
            WHERE license_key = ?
            AND timestamp >= datetime('now', ?)
        ''', (license_key, f'-{days} days'))
        
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
        conn = sqlite3.connect(DB_PATH)
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
            'database_path': DB_PATH,
            'log_count': log_count,
            'stats_count': stats_count,
            'recent_logs': recent_logs,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'database_path': DB_PATH
        }), 500

# Add this to help debug database location
print(f"Database path: {os.path.abspath(DB_PATH)}")

if __name__ == '__main__':
    try:
        print(f"Starting license logger server on {HOST}:{PORT}")
        print(f"Debug mode: {DEBUG}")
        
        # For local development
        if os.environ.get('ENVIRONMENT') == 'development':
            app.run(host=HOST, port=PORT, debug=DEBUG)
        else:
            # For production, let gunicorn handle the server
            import gunicorn.app.base
            
            class StandaloneApplication(gunicorn.app.base.BaseApplication):
                def __init__(self, app, options=None):
                    self.options = options or {}
                    self.application = app
                    super().__init__()

                def load_config(self):
                    for key, value in self.options.items():
                        self.cfg.set(key.lower(), value)

                def load(self):
                    return self.application

            options = {
                'bind': f'{HOST}:8080',
                'workers': 4,
                'worker_class': 'sync',
                'timeout': 120
            }
            
            StandaloneApplication(app, options).run()
            
    except Exception as e:
        print(f"Startup error: {e}")
        raise 