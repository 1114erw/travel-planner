# -*- coding: utf-8 -*-
import os
import sys

os.environ['PYTHONUTF8'] = '1'
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("Step 1: 正在加载app.py (文件较长，请稍候)...")
sys.stdout.flush()

try:
    from app import app
    print("Step 2: app加载成功")
    sys.stdout.flush()
except Exception as e:
    print(f"ERROR: 导入失败 - {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("Step 3: 正在初始化数据库...")
sys.stdout.flush()
try:
    with app.app_context():
        from models.database import db
        db.create_all()
    print("Step 4: 数据库初始化完成")
    sys.stdout.flush()
except Exception as e:
    print(f"WARNING: 数据库初始化警告 - {e}")

print("\n" + "="*50)
print("服务器启动成功!")
print("访问地址: http://127.0.0.1:5000/")
print("按 CTRL+C 停止服务器")
print("="*50 + "\n")
sys.stdout.flush()

app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
