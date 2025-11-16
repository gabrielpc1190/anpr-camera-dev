import os
import re
import getpass
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from anpr_web import User, db
from dotenv import load_dotenv

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

def get_session():
    db_user = os.getenv('MYSQL_USER')
    db_password = os.getenv('MYSQL_PASSWORD')
    db_host = os.getenv('MYSQL_HOST', 'mariadb')
    db_name = os.getenv('MYSQL_DATABASE')

    engine = create_engine(f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}")
    Session = sessionmaker(bind=engine)
    return Session()

def main():
    session = get_session()

    while True:
        print("\nUser Management")
        print("1. List users")
        print("2. Add user")
        print("3. Remove user")
        print("4. Reset password")
        print("5. Exit")
        choice = input("Enter your choice: ")

        if choice == '1':
            users = session.query(User).all()
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
            session.add(new_user)
            session.commit()
            print("User added successfully.")
        elif choice == '3':
            username = input("Enter username to remove: ")
            user = session.query(User).filter_by(username=username).first()
            if user:
                session.delete(user)
                session.commit()
                print("User removed successfully.")
            else:
                print("User not found.")
        elif choice == '4':
            username = input("Enter username to reset password: ")
            user = session.query(User).filter_by(username=username).first()
            if user:
                while True:
                    password = getpass.getpass("Enter new password: ")
                    if is_strong_password(password):
                        break
                    else:
                        print("Password is not strong enough. It must be at least 10 characters long and contain at least one uppercase letter, one number, and one special character.")
                user.set_password(password)
                session.commit()
                print("Password reset successfully.")
            else:
                print("User not found.")
        elif choice == '5':
            break
        else:
            print("Invalid choice.")

if __name__ == '__main__':
    main()
