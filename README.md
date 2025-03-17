# STOVE License Logger

License activity logging service for the STOVE License System.

## Features
- Tracks license validation attempts
- Records user activity and device information
- Provides usage statistics and analytics
- API endpoints for log querying and statistics

## Environment Variables
- `PORT` - Server port (default: 5001)
- `HOST` - Server host (default: 0.0.0.0)
- `DEBUG` - Debug mode (default: False)
- `API_KEY` - API authentication key
- `LOG_DB_PATH` - Path to SQLite database (default: license_logs.db)

## API Endpoints
- `/health` - Health check
- `/api/log/validation` - Log license validation
- `/api/logs/search` - Search logs with filters
- `/api/stats/license/<license_key>` - Get license statistics
- `/api/stats/summary` - Get overall statistics

## Deployment
1. Create new project in Railway.app
2. Connect this repository
3. Set environment variables
4. Deploy