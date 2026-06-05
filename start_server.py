#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""启动旅行规划网站服务器"""
import os
import sys

# 设置UTF-8编码
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入后立即初始化数据库
from app import app, db, init_db

if __name__ == '__main__':
    print("=" * 60)
    print("  旅行规划网站 - 服务器启动中")
    print("=" * 60)
    print("")

    try:
        print("[1/3] 初始化数据库...")
        with app.app_context():
            db.create_all()
        print("        ✓ 数据库初始化完成")

        print("[2/3] 检查路由...")
        routes = [r.rule for r in app.url_map.iter_rules()]
        print(f"        ✓ 注册了 {len(routes)} 个路由")
        for r in sorted(routes):
            if not r.startswith('/static'):
                print(f"        - {r}")

        print("[3/3] 启动Web服务器...")
        print("")
        print("=" * 60)
        print("  ✓ 服务器已启动!")
        print("  访问地址: http://127.0.0.1:5000/")
        print("  按 CTRL+C 停止服务器")
        print("=" * 60)
        print("")

        app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)

    except KeyboardInterrupt:
        print("\n\n用户中断，服务器已停止")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
