import os
import re
import getpass
from dotenv import load_dotenv
from app.models import db, User
from app.anpr_web import app

load_dotenv()

def is_strong_password(password):
    if len(password) < 10:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[!@#$%^&*(),.?:{}|<>~`]", password):
        return False
    return True

def main():
    with app.app_context():
        while True:
            print("\nUser Management")
            print("1. List users")
            print("2. Add user")
            print("3. Remove user")
            print("4. Reset password")
            print("5. Exit")
            choice = input("Enter your choice: ")

            if choice == '1':
                users = db.session.query(User).all()
                for user in users:
                    print(f"- {user.username}")
            elif choice == '2':
                username = input("Enter username: ")
                while True:
                    password = getpass.getpass("Enter password: ")
                    if is_strong_password(password):
                        break
                    else:
                        print("Password is not strong enough. It must be at least 10 characters long and contain at least one uppercase letter, one number, and one special character.")

                new_user = User(username=username)
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                print("User added successfully.")
            elif choice == '3':
                username = input("Enter username to remove: ")
                user = db.session.query(User).filter_by(username=username).first()
                if user:
                    db.session.delete(user)
                    db.session.commit()
                    print("User removed successfully.")
                else:
                    print("User not found.")
            elif choice == '4':
                username = input("Enter username to reset password: ")
                user = db.session.query(User).filter_by(username=username).first()
                if user:
                    while True:
                        password = getpass.getpass("Enter new password: ")
                        if is_strong_password(password):
                            break
                        else:
                            print("Password is not strong enough. It must be at least 10 characters long and contain at least one uppercase letter, one number, and one special character.")
                    user.set_password(password)
                    db.session.commit()
                    print("Password reset successfully.")
                else:
                    print("User not found.")
            elif choice == '5':
                break
            else:
                print("Invalid choice.")

if __name__ == '__main__':
    main()
