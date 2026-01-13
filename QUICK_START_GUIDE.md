# 🚀 CommCare Connect - Quick Start Guide

This guide will help you run the CommCare Connect app every time you want to see it.

## 📋 Prerequisites

- **Python 3.11** installed ✅
- **Docker & Docker Compose** installed (optional - app can run with SQLite)
- **Node.js & npm** installed ✅
- **Git** installed ✅

## 🏃‍♂️ Quick Start (Every Time You Want to Run the App)

### 1. Navigate to Project Directory
```bash
cd /Users/sarveshtewari/commcare-connect
```

### 2. Activate Virtual Environment
```bash
source .venv/bin/activate
```

### 3. Start Services (Optional - for PostgreSQL/Redis)
```bash
# If you have Docker installed, start database services:
inv up

# If no Docker, the app will use SQLite (already configured)
```

### 4. Start the Django Server
```bash
python manage.py runserver
```

### 5. Open Your Browser
Navigate to: **http://localhost:8000**

---

## 🚀 **Super Quick Start (No Docker Required)**

If you just want to run the app quickly without Docker:

```bash
cd /Users/sarveshtewari/commcare-connect
source .venv/bin/activate
python manage.py runserver
```

Then open: **http://localhost:8000**

---

## 🔧 First-Time Setup (Only Once)

If this is your first time running the app, follow these additional steps:

### 1. Install Python Dependencies
```bash
# Activate virtual environment first
source .venv/bin/activate

# Install requirements
pip install -r requirements-dev.txt
```

### 2. Install JavaScript Dependencies
```bash
npm ci
```

### 3. Build JavaScript Assets
```bash
# Build once
inv build-js

# Or build and watch for changes
inv build-js -w
```

### 4. Set Up Environment File
```bash
# Copy template if you haven't already
cp .env_template .env

# Edit .env file if needed (usually not required for local development)
```

### 5. Run Database Migrations
```bash
python manage.py migrate
```

### 6. Load Sample Data (Optional)
```bash
# Load solicitation data for testing
python manage.py load_uat_data
```

### 7. Create Superuser (Optional)
```bash
python manage.py createsuperuser
```

---

## 🛑 Stopping the App

### Stop Django Server
Press `Ctrl+C` in the terminal where Django is running

### Stop Docker Services
```bash
# Option A: Using invoke
inv down

# Option B: Using docker-compose directly
docker compose down
```

### Deactivate Virtual Environment
```bash
deactivate
```

---

## 🔍 Troubleshooting

### Database Connection Issues
```bash
# Check if Docker services are running
docker compose ps

# Restart Docker services
docker compose down
docker compose up -d
```

### Python Dependencies Issues
```bash
# Reinstall requirements
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### JavaScript Build Issues
```bash
# Reinstall npm packages
rm -rf node_modules package-lock.json
npm ci
inv build-js
```

### Port Already in Use
If port 8000 is already in use:
```bash
# Use a different port
python manage.py runserver 8001
```

---

## 📱 Accessing the App

Once running, you can access:

- **Main App**: http://localhost:8000
- **Admin Panel**: http://localhost:8000/admin
- **Solicitations**: http://localhost:8000/solicitations/
- **EOIs Only**: http://localhost:8000/solicitations/eoi/
- **RFPs Only**: http://localhost:8000/solicitations/rfp/

---

## 🎯 Common Commands Reference

| Command | Purpose |
|---------|---------|
| `inv up` | Start Docker services |
| `inv down` | Stop Docker services |
| `inv build-js` | Build JavaScript assets |
| `inv build-js -w` | Build and watch JS for changes |
| `python manage.py runserver` | Start Django development server |
| `python manage.py migrate` | Run database migrations |
| `python manage.py load_uat_data` | Load sample solicitation data |
| `python manage.py createsuperuser` | Create admin user |

---

## 💡 Pro Tips

1. **Keep Docker Running**: You can leave Docker services running between development sessions
2. **Use Two Terminals**: Keep one terminal for Django server, another for other commands
3. **Watch Mode**: Use `inv build-js -w` to automatically rebuild JS when files change
4. **Sample Data**: The `load_uat_data` command creates realistic test data for solicitations

---

## 🆘 Need Help?

- Check the main [README.md](README.md) for detailed setup instructions
- Look at the [tasks.py](tasks.py) file for available commands
- Ensure all prerequisites are installed correctly

---

**Happy coding! 🎉**
