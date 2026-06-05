# -*- coding: utf-8 -*-
import os, sys, traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

try:
    print("正在导入app...")
    import sys
    sys.path.insert(0, '.')
    from app import app
    print("[OK] app导入成功")
    
    print("正在检查路由...")
    route_count = len(list(app.url_map.iter_rules()))
    print(f"[OK] 注册了 {route_count} 个路由")
    
    print("正在检查配置...")
    configs = {
        'SQLALCHEMY_DATABASE_URI': app.config.get('SQLALCHEMY_DATABASE_URI', '未设置'),
        'DEEPSEEK_API_KEY': '已配置' if app.config.get('DEEPSEEK_API_KEY') else '未配置',
        'DEBUG': app.config.get('DEBUG', False),
    }
    for k, v in configs.items():
        print(f"[OK] {k}: {v}")
    
    print("\n正在启动Flask服务器 (127.0.0.1:5000)...")
    print("提示: 服务器已启动，请勿关闭此窗口")
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
    
except Exception as e:
    print(f"\n[ERROR] 启动失败: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)
