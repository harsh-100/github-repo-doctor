import os
from flask import Flask

app = Flask(__name__)
API_KEY = "sk-ant-abcdefghijklmnop1234567890"  # fake key for redaction tests
DB_URL = "postgres://admin:hunter2secret@db.local/app"
