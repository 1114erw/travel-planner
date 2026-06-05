#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""行程数据持久化模块 - 使用文件存储代替内存存储"""
import os
import json
import time
from threading import Lock

# 存储目录
STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plans')
os.makedirs(STORAGE_DIR, exist_ok=True)

# 锁
STORAGE_LOCK = Lock()

# TTL（1小时）
TTL_SECONDS = 3600

def _get_plan_path(plan_id):
    """获取计划文件路径"""
    return os.path.join(STORAGE_DIR, f'{plan_id}.json')

def store_plan(plan_id, departure, destination, plan_html, days=3):
    """安全存储行程数据到文件"""
    data = {
        'departure': departure,
        'destination': destination,
        'plan_html': plan_html,
        'days': days,
        'created_at': time.time()
    }
    with STORAGE_LOCK:
        filepath = _get_plan_path(plan_id)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 清理过期数据
        _clean_expired()

def get_plan(plan_id):
    """从文件读取行程数据"""
    filepath = _get_plan_path(plan_id)
    with STORAGE_LOCK:
        if not os.path.exists(filepath):
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 检查是否过期
            if time.time() - data.get('created_at', 0) > TTL_SECONDS:
                os.remove(filepath)
                return None
            
            return data
        except (json.JSONDecodeError, Exception):
            # 文件损坏，删除
            try:
                os.remove(filepath)
            except:
                pass
            return None

def delete_plan(plan_id):
    """删除指定计划"""
    filepath = _get_plan_path(plan_id)
    with STORAGE_LOCK:
        if os.path.exists(filepath):
            os.remove(filepath)

def _clean_expired():
    """清理过期的数据文件"""
    expired_time = time.time() - TTL_SECONDS
    try:
        for filename in os.listdir(STORAGE_DIR):
            if filename.endswith('.json'):
                filepath = os.path.join(STORAGE_DIR, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if data.get('created_at', 0) < expired_time:
                            os.remove(filepath)
                except:
                    # 文件损坏，直接删除
                    os.remove(filepath)
    except:
        pass

def get_all_plan_ids():
    """获取所有有效的计划ID"""
    plan_ids = []
    with STORAGE_LOCK:
        for filename in os.listdir(STORAGE_DIR):
            if filename.endswith('.json'):
                plan_id = filename[:-5]
                if get_plan(plan_id):
                    plan_ids.append(plan_id)
    return plan_ids

def get_plan_count():
    """获取计划数量"""
    return len(get_all_plan_ids())

# 测试
if __name__ == '__main__':
    print("=== 测试文件存储模块 ===")
    
    # 测试存储
    test_id = 'test-001'
    store_plan(test_id, '北京', '杭州', '<div>测试HTML</div>')
    print(f"存储测试: {get_plan(test_id) is not None}")
    
    # 测试读取
    data = get_plan(test_id)
    print(f"读取测试: departure={data['departure']}, destination={data['destination']}")
    
    # 测试不存在
    print(f"不存在测试: {get_plan('nonexistent') is None}")
    
    # 测试删除
    delete_plan(test_id)
    print(f"删除测试: {get_plan(test_id) is None}")
    
    print("=== 测试完成 ===")