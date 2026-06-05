#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI调用缓存模块"""
import os
import json
import time
import hashlib
from threading import Lock

# 缓存目录
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)

# 锁
CACHE_LOCK = Lock()

# TTL（24小时）
TTL_SECONDS = 86400

def _get_cache_key(departure, destination, start_date, end_date, people, style, transport):
    """生成缓存key"""
    key_str = f"{departure}_{destination}_{start_date}_{end_date}_{people}_{style}_{transport}"
    return hashlib.md5(key_str.encode('utf-8')).hexdigest()

def _get_cache_path(cache_key):
    """获取缓存文件路径"""
    return os.path.join(CACHE_DIR, f'{cache_key}.json')

def get_cached_plan(departure, destination, start_date, end_date, people, style, transport):
    """获取缓存的AI生成结果"""
    cache_key = _get_cache_key(departure, destination, start_date, end_date, people, style, transport)
    filepath = _get_cache_path(cache_key)
    
    with CACHE_LOCK:
        if not os.path.exists(filepath):
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 检查是否过期
            if time.time() - data.get('created_at', 0) > TTL_SECONDS:
                os.remove(filepath)
                return None
            
            return data.get('raw_plan')
        except (json.JSONDecodeError, Exception):
            try:
                os.remove(filepath)
            except:
                pass
            return None

def set_cached_plan(departure, destination, start_date, end_date, people, style, transport, raw_plan):
    """设置缓存的AI生成结果"""
    cache_key = _get_cache_key(departure, destination, start_date, end_date, people, style, transport)
    filepath = _get_cache_path(cache_key)
    
    with CACHE_LOCK:
        data = {
            'created_at': time.time(),
            'raw_plan': raw_plan,
            'meta': {
                'departure': departure,
                'destination': destination,
                'start_date': str(start_date),
                'end_date': str(end_date),
                'people': people,
                'style': style,
                'transport': transport
            }
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # 清理过期缓存
        _clean_expired_cache()

def _clean_expired_cache():
    """清理过期的缓存文件"""
    expired_time = time.time() - TTL_SECONDS
    try:
        for filename in os.listdir(CACHE_DIR):
            if filename.endswith('.json'):
                filepath = os.path.join(CACHE_DIR, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if data.get('created_at', 0) < expired_time:
                            os.remove(filepath)
                except:
                    os.remove(filepath)
    except:
        pass

def clear_all_cache():
    """清除所有缓存"""
    with CACHE_LOCK:
        for filename in os.listdir(CACHE_DIR):
            if filename.endswith('.json'):
                os.remove(os.path.join(CACHE_DIR, filename))

def get_cache_count():
    """获取缓存数量"""
    count = 0
    try:
        for filename in os.listdir(CACHE_DIR):
            if filename.endswith('.json'):
                count += 1
    except:
        pass
    return count

# 测试
if __name__ == '__main__':
    print("=== 测试缓存模块 ===")
    
    # 测试设置缓存
    set_cached_plan('北京', '杭州', '2026-06-10', '2026-06-13', 2, '舒适', '高铁', '测试内容')
    print(f"设置缓存: OK")
    
    # 测试获取缓存
    result = get_cached_plan('北京', '杭州', '2026-06-10', '2026-06-13', 2, '舒适', '高铁')
    print(f"获取缓存: {result == '测试内容'}")
    
    # 测试不同参数
    result2 = get_cached_plan('上海', '杭州', '2026-06-10', '2026-06-13', 2, '舒适', '高铁')
    print(f"不同参数缓存隔离: {result2 is None}")
    
    print("=== 测试完成 ===")