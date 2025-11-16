import os
from anpr_web import db, app, User
import bcrypt
from dotenv import load_dotenv

load_dotenv()

with app.app_context():
    db.create_all()

    # Create a default admin user if one doesn't exist
    if not User.query.filter_by(username='admin').first():
        hashed_password = bcrypt.hashpw('Admin123!'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        admin_user = User(username='admin', password=hashed_password)
        db.session.add(admin_user)
        db.session.commit()
        print("Admin user created.")
    else:
        print("Admin user already exists.")
