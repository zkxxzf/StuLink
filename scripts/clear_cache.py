#!/usr/bin/env python
"""清除字典缓存"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

app = create_app()
with app.app_context():
    from app.utils.cache import cache
    
    keys_to_delete = [key for key in cache._cache.keys() if key.startswith('dict_values_')]
    for key in keys_to_delete:
        cache.delete(key)
        print(f"已清除缓存: {key}")
    
    print("\n字典缓存已全部清除")
