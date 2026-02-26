# 🌸 Shiva Zen - Aesthetic Clinic Management System

Complete system for aesthetic clinic management developed in Django, with a modern interface and separation between administrative panel and client area.

## 🚀 Features

### For Clients
- ✅ Secure registration and login
- ✅ Online procedure scheduling
- ✅ View service history
- ✅ Personalized dashboard with upcoming appointments
- ✅ Profile editing

### For Administrators
- ✅ Dashboard with real-time statistics
- ✅ Complete schedule management
- ✅ Client registration and management
- ✅ Professional and availability management
- ✅ Procedure and price control
- ✅ Schedule blocking system
- ✅ Audit logs

## 📋 Prerequisites

- Python 3.12+
- PostgreSQL 14+
- pip (Python package manager)

## 🔧 Installation

### 1. Clone the repository
```bash
git clone <repository-url>
cd shivazen
```

### 2. Create the virtual environment
```bash
python -m venv venv
```

### 3. Activate the virtual environment

**Windows:**
```bash
venv\Scripts\activate
```

**Linux/Mac:**
```bash
source venv/bin/activate
```

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

### 5. Configure the PostgreSQL database

Create the database:
```sql
CREATE DATABASE shivazen_prod;
CREATE SCHEMA shivazen_prod;
CREATE SCHEMA shivazen_app;
```

### 6. Configure environment variables

Copy the example file:
```bash
copy .env.example .env  # Windows
cp .env.example .env    # Linux/Mac
```

Edit the `.env` file with your settings:
```env
DJANGO_SECRET_KEY=your-secret-key-here
DEBUG=True
DB_PASSWORD=your-postgres-password
```

### 7. Run migrations
```bash
python manage.py migrate
```

### 8. Create a superuser
```bash
python manage.py createsuperuser
```

### 9. Start the server
```bash
python manage.py runserver
```

Access: http://127.0.0.1:8000/

## 👤 User Types

### Client (is_staff=False)
- Accesses `/cliente/painel/`
- Simplified interface
- Only their own appointments

### Admin (is_staff=True)
- Accesses `/painel/`
- Complete dashboard
- Full system management

## 📁 Project Structure

```
shivazen/
├── app_shivazen/           # Main App
│   ├── models.py          # Django Models
│   ├── views.py           # Views (30+ functions)
│   ├── urls.py            # Routes (30+ endpoints)
│   ├── static/
│   │   └── css/
│   │       ├── base.css   # Global styles
│   │       └── painel.css # Admin dashboard
│   └── templates/
│       ├── cliente/       # Client templates
│       ├── painel/        # Admin templates
│       ├── usuario/       # Auth templates
│       └── partials/      # Reusable components
└── shivazen/              # Django Settings
    └── settings.py
```

## 🎨 Tech Stack

- **Backend**: Django 5.2
- **Database**: PostgreSQL 14
- **Frontend**: Bootstrap 5, Vanilla JS
- **Authentication**: Django Auth
- **Static**: WhiteNoise
- **Fonts**: Playfair Display + Lato
- **Icons**: Font Awesome 6

## 🔐 Security

- ✅ Standard Django authentication
- ✅ Hashed passwords (PBKDF2)
- ✅ CSRF protection on all forms
- ✅ SQL Injection protected (ORM)
- ✅ XSS protection (template escaping)
- ✅ @login_required on restricted views
- ✅ Access control via is_staff

## 🎯 Useful Commands

### Development
```bash
# Run server
python manage.py runserver

# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Interactive shell
python manage.py shell
```

### Production
```bash
# Collect static files
python manage.py collectstatic

# Check deployment
python manage.py check --deploy
```

## 🌐 Deploy

### Environment Variables (Production)
```env
DEBUG=False
DJANGO_SECRET_KEY=<generate-a-new-key>
ALLOWED_HOSTS=your-domain.com,www.your-domain.com
DB_NAME=shivazen_prod
DB_USER=postgres
DB_PASSWORD=<secure-password>
DB_HOST=<database-host>
DB_PORT=5432
```

### Deploy Checklist
- [ ] DEBUG=False
- [ ] Randomly generated SECRET_KEY
- [ ] ALLOWED_HOSTS configured
- [ ] Database on dedicated server
- [ ] HTTPS configured
- [ ] Static files collected
- [ ] Automatic backups configured

## 📚 Documentation

- `security_practices.md` - Security best practices
- `database_analysis.md` - Database structure
- `walkthrough.md` - Complete feature guide
- `.env.example` - Required environment variables

## 🤝 Contributing

1. Fork the project
2. Create a branch for your feature (`git checkout -b feature/NewFeature`)
3. Commit your changes (`git commit -m 'Add new feature'`)
4. Push to the branch (`git push origin feature/NewFeature`)
5. Open a Pull Request

## 📄 License

This project is under the [MIT](LICENSE) license.

## 👥 Authors

- **Development** - Shiva Zen Management System
- **Design** - Modern interface with Gold & Earth palette

## 📞 Support

For questions and support:
- Email: contato@shivazen.com.br
- Issues: GitHub Issues

---

**Developed with ❤️ using Django 5.2 + PostgreSQL + Bootstrap 5**

*Last update: November 2025*