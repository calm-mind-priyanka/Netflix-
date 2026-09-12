from getpass import getpass
from backend.auth import hash_admin_password
p=getpass("Admin password: ")
print("ADMIN_PASSWORD_HASH="+hash_admin_password(p))
