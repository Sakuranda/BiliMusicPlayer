"""Local server recovery: python -m scripts.reset_admin. No HTTP recovery endpoint."""
import getpass
import secrets
from contextlib import closing
from app.admin import hash_password
from app.db import get_conn


def main():
    username = input('New administrator username [admin]: ').strip() or 'admin'
    password = getpass.getpass('New password (at least 12 characters): ')
    confirm = getpass.getpass('Confirm new password: ')
    if not 1 <= len(username) <= 80 or not 12 <= len(password) <= 256 or password != confirm:
        raise SystemExit('Invalid username/password or passwords do not match')
    with closing(get_conn()) as conn:
        conn.execute('INSERT INTO admin_account(id,username,password_hash,session_epoch) VALUES(1,?,?,?) '
                     'ON CONFLICT(id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash,'
                     'session_epoch=excluded.session_epoch,totp_secret=NULL,pending_secret=NULL,pending_since=NULL,last_totp_counter=-1',
                     (username, hash_password(password), secrets.token_hex(24)))
        conn.commit()
    print('Administrator reset. 2FA disabled and all browser sessions revoked; library preserved.')


if __name__ == '__main__':
    main()
