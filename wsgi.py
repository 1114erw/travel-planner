import os
os.environ['PYTHONUTF8'] = '1'

from app import app as application

if __name__ == "__main__":
    application.run()
