import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///travel_planner.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
    DOUBAN_API_KEY = os.environ.get('DOUBAN_API_KEY', '')
    BAIDU_API_KEY = os.environ.get('BAIDU_API_KEY', '')
    AMAP_WEATHER_KEY = os.environ.get('AMAP_WEATHER_KEY', '')
    # 导航用的地理编码 API Key（可选）。不填时自动复用 AMAP_WEATHER_KEY
    AMAP_API_KEY = os.environ.get('AMAP_API_KEY', '') or os.environ.get('AMAP_WEATHER_KEY', '')

    DEEPSEEK_BASE_URL = 'https://api.deepseek.com/v1/chat/completions'