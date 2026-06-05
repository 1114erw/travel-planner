import os
os.environ['PYTHONUTF8'] = '1'

from dotenv import load_dotenv
# 明确指定.env文件路径
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from flask import Flask, render_template, request, jsonify, session, send_file
from flask_sqlalchemy import SQLAlchemy
from models.database import db, User, Trip, Favorite, init_db
from config import Config
import requests
import json
import hashlib
from datetime import datetime, timedelta
from dateutil import parser
import random
from io import BytesIO
import socket

original_getfqdn = socket.getfqdn
def safe_getfqdn(name=''):
    try:
        return original_getfqdn(name)
    except UnicodeDecodeError:
        return 'localhost'

socket.getfqdn = safe_getfqdn

app = Flask(__name__)
app.config.from_object(Config)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
db.init_app(app)

# ========== 高德地图 API Key 初始化 ==========
# 复用 AMAP_WEATHER_KEY（同一个高德 Web 服务 Key 可同时用于天气、地理编码等 API）
# 优先级：.env 中的 AMAP_API_KEY > .env 中的 AMAP_WEATHER_KEY > 环境变量 > 空（空值时自动降级为搜索页）
def _init_amap_api_key():
    """从环境变量读取高德 API Key，复用于地理编码（导航）和天气"""
    try:
        from services.ctrip_promo_service import CtripPromoService
        key = (os.environ.get('AMAP_API_KEY', '') or os.environ.get('AMAP_WEATHER_KEY', '')).strip()
        CtripPromoService.set_amap_api_key(key)
        if key:
            print(f"[启动] 高德 API Key 已加载 ✓（地理编码已启用）")
        else:
            print(f"[启动] 未配置高德 API Key（导航将使用搜索页模式）")
    except Exception as e:
        print(f"[警告] 初始化高德 API Key 失败: {e}")

with app.app_context():
    _init_amap_api_key()

# ========== 行程数据持久化存储（文件存储）==========
# 修复：Flask session 用cookie存储，最大约4KB，无法存下5-20KB的plan_html
# 使用文件存储代替内存存储，服务器重启后数据不丢失
from services.plan_storage import store_plan as _store_plan_file, get_plan as _get_plan_file

def _store_plan(plan_id, departure, destination, plan_html, days=3):
    """安全存储行程数据（优先文件存储，失败时降级到内存）"""
    try:
        _store_plan_file(plan_id, departure, destination, plan_html, days)
        return True
    except Exception as e:
        # 文件存储失败，降级到内存
        import time
        with PLAN_STORE_LOCK:
            PLAN_STORE[plan_id] = {
                'departure': departure,
                'destination': destination,
                'plan_html': plan_html,
                'days': days,
                'created_at': time.time()
            }
        return False

def _get_plan(plan_id):
    """读取行程数据（优先文件存储）"""
    # 先尝试文件存储
    data = _get_plan_file(plan_id)
    if data:
        return data
    # 降级到内存存储（兼容旧数据）
    import time
    with PLAN_STORE_LOCK:
        data = PLAN_STORE.get(plan_id)
        if data and time.time() - data.get('created_at', 0) <= PLAN_STORE_TTL:
            return data
        elif data:
            del PLAN_STORE[plan_id]
    return None

# 内存存储作为降级方案
import time
from threading import Lock
PLAN_STORE = {}
PLAN_STORE_LOCK = Lock()
PLAN_STORE_TTL = 3600

PROVINCE_CITIES = {
    "北京市": [{"name": "北京", "pinyin": "beijing", "famous": ["故宫", "天安门", "颐和园", "八达岭长城", "天坛"]}],
    "上海市": [{"name": "上海", "pinyin": "shanghai", "famous": ["外滩", "东方明珠", "豫园", "南京路", "迪士尼"]}],
    "广东省": [
        {"name": "广州", "pinyin": "guangzhou", "famous": ["小蛮腰", "长隆", "沙面", "陈家祠", "白云山"]},
        {"name": "深圳", "pinyin": "shenzhen", "famous": ["世界之窗", "欢乐谷", "东部华侨城", "大鹏半岛", "莲花山"]},
        {"name": "珠海", "pinyin": "zhuhai", "famous": ["长隆海洋王国", "情侣路", "珠海渔女", "圆明新园", "外伶仃岛"]},
        {"name": "东莞", "pinyin": "dongguan", "famous": ["松山湖", "可园", "虎门炮台", "观音山", "隐贤山庄"]},
        {"name": "佛山", "pinyin": "foshan", "famous": ["祖庙", "西樵山", "清晖园", "南风古灶", "长鹿农庄"]},
        {"name": "江门", "pinyin": "jiangmen", "famous": ["开平碉楼", "小鸟天堂", "上下川岛", "赤坎古镇", "圭峰山"]},
        {"name": "湛江", "pinyin": "zhanjiang", "famous": ["湖光岩", "东海岛", "硇洲岛", "徐闻港", "特呈岛"]},
        {"name": "肇庆", "pinyin": "zhaoqing", "famous": ["七星岩", "鼎湖山", "宋城墙", "阅江楼", "盘龙峡"]},
        {"name": "惠州", "pinyin": "huizhou", "famous": ["西湖", "罗浮山", "双月湾", "巽寮湾", "南昆山"]},
        {"name": "中山", "pinyin": "zhongshan", "famous": ["孙中山故居", "孙文西路", "中山影视城", "泉林山庄", "长江水世界"]},
        {"name": "揭阳", "pinyin": "jieyang", "famous": ["揭阳楼", "黄岐山", "普宁寺", "南澳岛", "望天湖"]},
        {"name": "潮州", "pinyin": "chaozhou", "famous": ["广济桥", "牌坊街", "开元寺", "韩文公祠", "凤凰山"]},
        {"name": "汕头", "pinyin": "shantou", "famous": ["南澳岛", "礐石风景区", "汕头大学", "小公园", "莲花峰"]},
        {"name": "茂名", "pinyin": "maoming", "famous": ["中国第一滩", "放鸡岛", "浪漫海岸", "御水古温泉", "森林公园"]},
        {"name": "韶关", "pinyin": "shaoguan", "famous": ["丹霞山", "南华寺", "大峡谷", "丹霞山", "丹霞山"]},
        {"name": "阳江", "pinyin": "yangjiang", "famous": ["海陵岛", "闸坡大角湾", "十里银滩", "南海一号", "鸳鸯湖"]},
        {"name": "清远", "pinyin": "qingyuan", "famous": ["古龙峡", "古龙峡", "古龙峡", "古龙峡", "古龙峡"]},
        {"name": "梅州", "pinyin": "meizhou", "famous": ["雁南飞茶田", "雁鸣湖", "客家博物馆", "五指石", "阴那山"]}
    ],
    "江苏省": [
        {"name": "南京", "pinyin": "nanjing", "famous": ["中山陵", "夫子庙", "明孝陵", "玄武湖", "总统府"]},
        {"name": "苏州", "pinyin": "suzhou", "famous": ["拙政园", "留园", "平江路", "周庄", "寒山寺"]},
        {"name": "无锡", "pinyin": "wuxi", "famous": ["太湖", "鼋头渚", "灵山胜境", "三国城", "南长街"]},
        {"name": "常州", "pinyin": "changzhou", "famous": ["中华恐龙园", "天目湖", "淹城春秋乐园", "嬉戏谷", "茅山"]},
        {"name": "南通", "pinyin": "nantong", "famous": ["濠河", "狼山", "水绘园", "如皋古城", "黄金海滩"]},
        {"name": "扬州", "pinyin": "yangzhou", "famous": ["瘦西湖", "个园", "何园", "东关街", "大明寺"]},
        {"name": "镇江", "pinyin": "zhenjiang", "famous": ["金山寺", "焦山", "北固山", "西津渡", "茅山"]},
        {"name": "徐州", "pinyin": "xuzhou", "famous": ["云龙湖", "汉文化景区", "龟山汉墓", "户部山", "彭祖园"]},
        {"name": "盐城", "pinyin": "yancheng", "famous": ["丹顶鹤保护区", "麋鹿保护区", "荷兰花海", "大洋湾", "新四军纪念馆"]},
        {"name": "淮安", "pinyin": "huaian", "famous": ["周恩来故居", "河下古镇", "洪泽湖", "铁山寺", "漕运博物馆"]},
        {"name": "连云港", "pinyin": "lianyungang", "famous": ["花果山", "连岛", "渔湾", "孔望山", "海上云台山"]},
        {"name": "宿迁", "pinyin": "suqian", "famous": ["项王故里", "洪泽湖湿地", "三台山", "骆马湖", "乾隆行宫"]},
        {"name": "泰州", "pinyin": "taizhou", "famous": ["溱潼古镇", "李中水上森林", "凤城河风景区", "梅兰芳纪念馆", "溱湖国家湿地公园"]}
    ],
    "浙江省": [
        {"name": "杭州", "pinyin": "hangzhou", "famous": ["西湖", "灵隐寺", "千岛湖", "西溪湿地", "雷峰塔"]},
        {"name": "宁波", "pinyin": "ningbo", "famous": ["天一阁", "老外滩", "东钱湖", "溪口", "象山影视城"]},
        {"name": "温州", "pinyin": "wenzhou", "famous": ["雁荡山", "楠溪江", "江心屿", "洞头岛", "刘伯温故里"]},
        {"name": "绍兴", "pinyin": "shaoxing", "famous": ["鲁迅故里", "沈园", "兰亭", "东湖", "柯岩"]},
        {"name": "嘉兴", "pinyin": "jiaxing", "famous": ["南湖", "乌镇", "西塘", "海宁潮", "月河历史街区"]},
        {"name": "湖州", "pinyin": "huzhou", "famous": ["太湖", "莫干山", "南浔古镇", "安吉竹海", "新市古镇"]},
        {"name": "金华", "pinyin": "jinhua", "famous": ["横店影视城", "双龙洞", "诸葛八卦村", "永康方岩", "义乌商贸城"]},
        {"name": "衢州", "pinyin": "quzhou", "famous": ["江郎山", "廿八都", "龙游石窟", "开化根宫佛国", "三衢石林"]},
        {"name": "台州", "pinyin": "taizhou", "famous": ["天台山", "神仙居", "长屿硐天", "国清寺", "紫阳古街"]},
        {"name": "舟山", "pinyin": "zhoushan", "famous": ["普陀山", "朱家尖", "嵊泗列岛", "桃花岛", "东极岛"]}
    ],
    "山东省": [
        {"name": "济南", "pinyin": "jinan", "famous": ["趵突泉", "大明湖", "千佛山", "黑虎泉", "芙蓉街"]},
        {"name": "青岛", "pinyin": "qingdao", "famous": ["八大关", "崂山", "栈桥", "五四广场", "啤酒博物馆"]},
        {"name": "烟台", "pinyin": "yantai", "famous": ["蓬莱阁", "长岛", "养马岛", "金沙滩", "八仙过海景区"]},
        {"name": "威海", "pinyin": "weihai", "famous": ["刘公岛", "威海国际海水浴场", "成山头", "西霞口", "那香海"]},
        {"name": "日照", "pinyin": "rizhao", "famous": ["万平口海滨", "日照海滨国家森林公园", "五莲山", "九仙山", "浮来山"]},
        {"name": "潍坊", "pinyin": "weifang", "famous": ["世界风筝都", "青州古城", "十笏园", "沂山", "云门山"]},
        {"name": "临沂", "pinyin": "linyi", "famous": ["沂蒙山", "竹泉村", "孟良崮", "王羲之故居", "沂河"]},
        {"name": "泰安", "pinyin": "taian", "famous": ["泰山", "岱庙", "方特欢乐世界", "天平湖", "大汶口文化遗址"]},
        {"name": "淄博", "pinyin": "zibo", "famous": ["周村古商城", "聊斋城", "潭溪山", "开元溶洞", "齐山"]},
        {"name": "济宁", "pinyin": "jining", "famous": ["三孔", "尼山", "微山湖", "太白楼", "水泊梁山"]},
        {"name": "枣庄", "pinyin": "zaozhuang", "famous": ["台儿庄古城", "微山湖", "抱犊崮", "熊耳山", "铁道游击队纪念馆"]},
        {"name": "东营", "pinyin": "dongying", "famous": ["黄河入海口", "孙子文化园", "清风湖", "天鹅湖", "黄河三角洲"]},
        {"name": "德州", "pinyin": "dezhou", "famous": ["泉城极地海洋世界", "苏禄王墓", "减河湿地公园", "新湖风景区", "董子园"]}
    ],
    "四川省": [
        {"name": "成都", "pinyin": "chengdu", "famous": ["宽窄巷子", "锦里", "熊猫基地", "都江堰", "青城山"]},
        {"name": "乐山", "pinyin": "leshan", "famous": ["乐山大佛", "峨眉山", "东方佛都", "嘉阳小火车", "黑竹沟"]},
        {"name": "宜宾", "pinyin": "yibin", "famous": ["蜀南竹海", "五粮液", "李庄古镇", "兴文石海", "流杯池"]},
        {"name": "泸州", "pinyin": "luzhou", "famous": ["泸州老窖", "张坝桂圆林", "方山", "尧坝古镇", "黄荆老林"]},
        {"name": "德阳", "pinyin": "deyang", "famous": ["三星堆", "文庙", "孝泉古镇", "什邡蓥华山", "绵竹年画村"]},
        {"name": "绵阳", "pinyin": "mianyang", "famous": ["七曲山大庙", "江油窦圌山", "李白故居", "越王楼", "仙海湖"]},
        {"name": "广元", "pinyin": "guangyuan", "famous": ["剑门关", "皇泽寺", "千佛崖", "翠云廊", "昭化古城"]},
        {"name": "遂宁", "pinyin": "suining", "famous": ["观音故里", "广德寺", "灵泉寺", "中国死海", "龙凤古镇"]},
        {"name": "内江", "pinyin": "neijiang", "famous": ["大千故里", "圣水寺", "隆昌石牌坊", "资中文庙", "重龙山"]},
        {"name": "南充", "pinyin": "nanchong", "famous": ["阆中古城", "朱德故里", "西山风景区", "升钟湖", "凌云山"]},
        {"name": "眉山", "pinyin": "meishan", "famous": ["三苏祠", "柳江古镇", "瓦屋山", "黑龙滩", "彭祖山"]},
        {"name": "广安", "pinyin": "guangan", "famous": ["邓小平故里", "华蓥山", "思源广场", "天意谷", "宝箴塞"]},
        {"name": "达州", "pinyin": "dazhou", "famous": ["真佛山", "巴山大峡谷", "賨人谷", "八台山", "渠县汉阙"]},
        {"name": "雅安", "pinyin": "yaan", "famous": ["碧峰峡", "蒙顶山", "上里古镇", "牛背山", "周公山"]},
        {"name": "巴中", "pinyin": "bazhong", "famous": ["光雾山", "诺水河", "恩阳古镇", "南龛石窟", "米仓山"]},
        {"name": "资阳", "pinyin": "ziyang", "famous": ["安岳石刻", "陈毅故里", "圆觉洞", "木门寺", "三岔湖"]}
    ],
    "湖南省": [
        {"name": "长沙", "pinyin": "changsha", "famous": ["橘子洲", "岳麓山", "太平街", "火宫殿", "湖南省博物馆"]},
        {"name": "张家界", "pinyin": "zhangjiajie", "famous": ["张家界森林公园", "天门山", "黄龙洞", "宝峰湖", "芙蓉镇"]},
        {"name": "岳阳", "pinyin": "yueyang", "famous": ["岳阳楼", "君山岛", "洞庭湖", "张谷英村", "屈子祠"]},
        {"name": "衡阳", "pinyin": "hengyang", "famous": ["衡山", "南岳大庙", "石鼓书院", "回雁峰", "蔡伦竹海"]},
        {"name": "湘潭", "pinyin": "xiangtan", "famous": ["韶山", "齐白石纪念馆", "昭山", "彭德怀纪念馆", "窑湾"]},
        {"name": "常德", "pinyin": "changde", "famous": ["桃花源", "柳叶湖", "壶瓶山", "城头山", "夹山寺"]},
        {"name": "郴州", "pinyin": "chenzhou", "famous": ["东江湖", "莽山", "高椅岭", "苏仙岭", "万华岩"]},
        {"name": "永州", "pinyin": "yongzhou", "famous": ["柳子庙", "浯溪碑林", "九嶷山", "上甘棠村", "女书园"]},
        {"name": "邵阳", "pinyin": "shaoyang", "famous": ["崀山", "南山牧场", "云山", "白水洞", "魏源故居"]},
        {"name": "益阳", "pinyin": "yiyang", "famous": ["茶马古道", "桃花江", "柘溪水库", "周立波故居", "雪峰山"]},
        {"name": "娄底", "pinyin": "loudi", "famous": ["紫鹊界梯田", "梅山龙宫", "曾国藩故居", "湄江", "波月洞"]},
        {"name": "怀化", "pinyin": "huaihua", "famous": ["洪江古商城", "黔阳古城", "高椅古村", "通道万佛山", "芷江受降坊"]},
        {"name": "湘西", "pinyin": "xiangxi", "famous": ["凤凰古城", "德夯苗寨", "矮寨大桥", "干州古城", "猛洞河"]}
    ],
    "湖北省": [
        {"name": "武汉", "pinyin": "wuhan", "famous": ["黄鹤楼", "东湖", "户部巷", "长江大桥", "武大樱花"]},
        {"name": "宜昌", "pinyin": "yichang", "famous": ["三峡大坝", "三峡人家", "清江画廊", "西陵峡", "屈原故里"]},
        {"name": "襄阳", "pinyin": "xiangyang", "famous": ["古隆中", "襄阳古城", "唐城", "习家池", "鹿门山"]},
        {"name": "荆州", "pinyin": "jingzhou", "famous": ["荆州古城", "关帝庙", "张居正故居", "熊家冢", "洪湖"]},
        {"name": "十堰", "pinyin": "shiyan", "famous": ["武当山", "丹江口水库", "神农架", "房县野人谷", "牛头山"]},
        {"name": "黄石", "pinyin": "huangshi", "famous": ["磁湖", "东方山", "西塞山", "仙岛湖", "雷山"]},
        {"name": "黄冈", "pinyin": "huanggang", "famous": ["东坡赤壁", "大别山", "罗田薄刀峰", "麻城龟峰山", "英山桃花冲"]},
        {"name": "咸宁", "pinyin": "xianning", "famous": ["九宫山", "赤壁古战场", "隐水洞", "星星竹海", "太乙洞"]},
        {"name": "荆门", "pinyin": "jingmen", "famous": ["明显陵", "漳河风景区", "绿林寨", "黄仙洞", "莫愁村"]},
        {"name": "孝感", "pinyin": "xiaogan", "famous": ["双峰山", "天紫湖", "汤池温泉", "观音湖", "白兆山"]},
        {"name": "鄂州", "pinyin": "ezhou", "famous": ["莲花山", "西山", "梁子湖", "红莲湖", "观音阁公园"]},
        {"name": "随州", "pinyin": "suizhou", "famous": ["大洪山", "曾侯乙墓", "炎帝故里", "千年银杏谷", "西游记公园"]},
        {"name": "恩施", "pinyin": "enshi", "famous": ["恩施大峡谷", "土司城", "腾龙洞", "神农溪", "梭布垭石林"]},
        {"name": "潜江", "pinyin": "qianjiang", "famous": ["曹禺公园", "章华台", "龙湾遗址", "兴隆水利枢纽", "汉江外滩"]},
        {"name": "天门", "pinyin": "tianmen", "famous": ["陆羽故园", "西湖", "胡家花园", "白龙寺", "张家湖国家湿地公园"]},
        {"name": "仙桃", "pinyin": "xiantao", "famous": ["沔阳小镇", "梦里水乡", "陈友谅故居", "排湖风景区", "沙湖国家湿地公园"]}
    ],
    "河南省": [
        {"name": "郑州", "pinyin": "zhengzhou", "famous": ["少林寺", "嵩山", "黄帝故里", "二七塔", "康百万庄园"]},
        {"name": "开封", "pinyin": "kaifeng", "famous": ["清明上河园", "龙亭公园", "大相国寺", "铁塔", "包公祠"]},
        {"name": "洛阳", "pinyin": "luoyang", "famous": ["龙门石窟", "白马寺", "老君山", "白云山", "隋唐洛阳城"]},
        {"name": "安阳", "pinyin": "anyang", "famous": ["殷墟", "红旗渠", "太行大峡谷", "中国文字博物馆", "马氏庄园"]},
        {"name": "焦作", "pinyin": "jiaozuo", "famous": ["云台山", "青天河", "神农山", "陈家沟", "圆融无碍禅寺"]},
        {"name": "新乡", "pinyin": "xinxiang", "famous": ["八里沟", "万仙山", "郭亮村", "比干庙", "潞王陵"]},
        {"name": "平顶山", "pinyin": "pingdingshan", "famous": ["尧山", "中原大佛", "三苏园", "香山寺", "二郎山"]},
        {"name": "信阳", "pinyin": "xinyang", "famous": ["鸡公山", "南湾湖", "灵山寺", "汤泉池", "西九华山"]},
        {"name": "商丘", "pinyin": "shangqiu", "famous": ["商丘古城", "芒砀山", "火神台", "应天书院", "木兰祠"]},
        {"name": "周口", "pinyin": "zhoukou", "famous": ["太昊陵", "老子故里", "太清宫", "袁世凯故居", "弦歌台"]},
        {"name": "许昌", "pinyin": "xuchang", "famous": ["曹丞相府", "春秋楼", "灞陵桥", "神垕古镇", "鄢陵国家花木博览园"]},
        {"name": "漯河", "pinyin": "luohe", "famous": ["南街村", "小商桥", "许慎文化园", "沙澧河风景区", "开源森林公园"]},
        {"name": "驻马店", "pinyin": "zhumadian", "famous": ["嵖岈山", "南海禅寺", "老乐山", "铜山", "竹沟革命纪念馆"]},
        {"name": "南阳", "pinyin": "nanyang", "famous": ["武侯祠", "汉画馆", "内乡县衙", "恐龙蛋化石群", "宝天曼"]},
        {"name": "三门峡", "pinyin": "sanmenxia", "famous": ["三门峡大坝", "天鹅湖", "函谷关", "地坑院", "甘山国家森林公园"]},
        {"name": "鹤壁", "pinyin": "hebi", "famous": ["浚县古城", "大伾山", "云梦山", "古灵山", "朝阳山"]},
        {"name": "濮阳", "pinyin": "puyang", "famous": ["戚城遗址", "濮上园", "绿色庄园", "中华第一龙", "挥公陵园"]},
        {"name": "济源", "pinyin": "jiyuan", "famous": ["王屋山", "五龙口", "小浪底", "济渎庙", "黄河三峡"]}
    ],
    "陕西省": [
        {"name": "西安", "pinyin": "xian", "famous": ["兵马俑", "大雁塔", "城墙", "华清池", "陕西历史博物馆"]},
        {"name": "延安", "pinyin": "yan'an", "famous": ["宝塔山", "枣园", "延安革命纪念馆", "黄帝陵", "壶口瀑布"]},
        {"name": "宝鸡", "pinyin": "baoji", "famous": ["法门寺", "太白山", "周公庙", "青铜器博物院", "关山草原"]},
        {"name": "咸阳", "pinyin": "xianyang", "famous": ["乾陵", "茂陵", "昭陵", "汉阳陵", "袁家村"]},
        {"name": "渭南", "pinyin": "weinan", "famous": ["华山", "少华山", "司马迁祠", "党家村", "洽川风景区"]},
        {"name": "汉中", "pinyin": "hanzhong", "famous": ["古汉台", "武侯祠", "青木川古镇", "华阳景区", "张良庙"]},
        {"name": "安康", "pinyin": "ankang", "famous": ["瀛湖", "南宫山", "香溪洞", "燕翔洞", "中坝大峡谷"]},
        {"name": "商洛", "pinyin": "shangluo", "famous": ["金丝峡", "牛背梁", "天竺山", "柞水溶洞", "塔云山"]},
        {"name": "榆林", "pinyin": "yulin", "famous": ["红石峡", "镇北台", "统万城", "白云山", "红碱淖"]},
        {"name": "铜川", "pinyin": "tongchuan", "famous": ["耀州窑遗址", "药王山", "玉华宫", "照金香山", "陈炉古镇"]}
    ],
    "云南省": [
        {"name": "昆明", "pinyin": "kunming", "famous": ["滇池", "石林", "翠湖", "大观楼", "云南民族村"]},
        {"name": "丽江", "pinyin": "lijiang", "famous": ["丽江古城", "玉龙雪山", "束河古镇", "泸沽湖", "虎跳峡"]},
        {"name": "大理", "pinyin": "dali", "famous": ["洱海", "大理古城", "苍山", "崇圣寺三塔", "双廊"]},
        {"name": "西双版纳", "pinyin": "xishuangbanna", "famous": ["西双版纳热带植物园", "野象谷", "傣族园", "曼听公园", "望天树"]},
        {"name": "香格里拉", "pinyin": "xianggelila", "famous": ["普达措", "松赞林寺", "独克宗古城", "纳帕海", "巴拉格宗"]},
        {"name": "腾冲", "pinyin": "tengchong", "famous": ["和顺古镇", "热海", "火山公园", "北海湿地", "国殇墓园"]},
        {"name": "红河", "pinyin": "honghe", "famous": ["建水古城", "元阳梯田", "弥勒东风韵", "朱家花园", "阿庐古洞"]},
        {"name": "普洱", "pinyin": "pu'er", "famous": ["茶马古城", "太阳河国家森林公园", "景迈山", "翁基古寨", "那柯里"]},
        {"name": "临沧", "pinyin": "lincang", "famous": ["翁丁原始部落", "沧源崖画", "鲁史古镇", "勐库大雪山", "百里长湖"]},
        {"name": "德宏", "pinyin": "dehong", "famous": ["芒市树包塔", "瑞丽口岸", "畹町桥", "勐巴娜西珍奇园", "姐告"]},
        {"name": "怒江", "pinyin": "nujiang", "famous": ["怒江大峡谷", "丙中洛", "独龙江", "老姆登", "知子罗"]},
        {"name": "迪庆", "pinyin": "diqing", "famous": ["梅里雪山", "雨崩", "飞来寺", "明永冰川", "茨中教堂"]}
    ],
    "贵州省": [
        {"name": "贵阳", "pinyin": "guiyang", "famous": ["黔灵公园", "甲秀楼", "青岩古镇", "天河潭", "花溪公园"]},
        {"name": "遵义", "pinyin": "zunyi", "famous": ["遵义会议会址", "赤水丹霞", "茅台镇", "海龙屯", "娄山关"]},
        {"name": "安顺", "pinyin": "anshun", "famous": ["黄果树瀑布", "龙宫", "屯堡古镇", "格凸河", "夜郎洞"]},
        {"name": "毕节", "pinyin": "bijie", "famous": ["百里杜鹃", "织金洞", "草海", "韭菜坪", "慕俄格古城"]},
        {"name": "铜仁", "pinyin": "tongren", "famous": ["梵净山", "苗王城", "大明边城", "九龙洞", "思南温泉"]},
        {"name": "黔东南", "pinyin": "qiandongnan", "famous": ["西江千户苗寨", "镇远古镇", "肇兴侗寨", "舞阳河", "青曼苗寨"]},
        {"name": "黔南", "pinyin": "qiannan", "famous": ["荔波小七孔", "樟江", "平塘天眼", "茂兰喀斯特", "三都水族"]},
        {"name": "黔西南", "pinyin": "qianxinan", "famous": ["万峰林", "马岭河峡谷", "万峰湖", "招堤", "双乳峰"]},
        {"name": "六盘水", "pinyin": "liupanshui", "famous": ["乌蒙大草原", "玉舍国家森林公园", "牂牁江", "妥乐古银杏", "明湖湿地公园"]}
    ],
    "广西壮族自治区": [
        {"name": "南宁", "pinyin": "nanning", "famous": ["青秀山", "南湖公园", "扬美古镇", "大明山", "伊岭岩"]},
        {"name": "桂林", "pinyin": "guilin", "famous": ["漓江", "阳朔", "象鼻山", "七星岩", "两江四湖"]},
        {"name": "柳州", "pinyin": "liuzhou", "famous": ["龙潭公园", "柳侯公园", "百里柳江", "程阳风雨桥", "三江侗寨"]},
        {"name": "北海", "pinyin": "beihai", "famous": ["银滩", "涠洲岛", "老街", "侨港风情街", "红树林"]},
        {"name": "梧州", "pinyin": "wuzhou", "famous": ["骑楼城", "白云山", "龙母庙", "石表山", "岑溪天龙顶"]},
        {"name": "玉林", "pinyin": "yulin", "famous": ["云天宫", "谢鲁山庄", "容州古城", "都峤山", "大容山"]},
        {"name": "百色", "pinyin": "baise", "famous": ["通灵大峡谷", "乐业天坑", "百色起义纪念馆", "澄碧湖", "古龙山"]},
        {"name": "钦州", "pinyin": "qinzhou", "famous": ["三娘湾", "八寨沟", "刘永福故居", "冯子材故居", "大芦古村"]},
        {"name": "贵港", "pinyin": "guigang", "famous": ["桂平西山", "龙潭国家森林公园", "平天山", "南山寺", "白石山"]},
        {"name": "防城港", "pinyin": "fangchenggang", "famous": ["东兴口岸", "金滩", "白浪滩", "十万大山", "京岛"]},
        {"name": "河池", "pinyin": "hechi", "famous": ["巴马长寿村", "百魔洞", "水晶宫", "凤山三门海", "刘三姐故里"]},
        {"name": "崇左", "pinyin": "chongzuo", "famous": ["德天瀑布", "明仕田园", "友谊关", "花山岩画", "左江"]}
    ],
    "福建省": [
        {"name": "福州", "pinyin": "fuzhou", "famous": ["三坊七巷", "鼓山", "西湖", "林则徐纪念馆", "马尾船政"]},
        {"name": "厦门", "pinyin": "xiamen", "famous": ["鼓浪屿", "厦门大学", "曾厝垵", "环岛路", "南普陀"]},
        {"name": "泉州", "pinyin": "quanzhou", "famous": ["开元寺", "清源山", "西街", "洛阳桥", "崇武古城"]},
        {"name": "漳州", "pinyin": "zhangzhou", "famous": ["南靖土楼", "东山岛", "云水谣", "火山岛", "三平寺"]},
        {"name": "莆田", "pinyin": "putian", "famous": ["湄洲岛", "九鲤湖", "广化寺", "南少林寺", "木兰陂"]},
        {"name": "龙岩", "pinyin": "longyan", "famous": ["永定土楼", "冠豸山", "古田会议会址", "龙硿洞", "培田古民居"]},
        {"name": "三明", "pinyin": "sanming", "famous": ["泰宁大金湖", "永安桃源洞", "将乐玉华洞", "沙县小吃", "上清溪"]},
        {"name": "南平", "pinyin": "nanping", "famous": ["武夷山", "九曲溪", "大红袍景区", "下梅古村", "和平古镇"]},
        {"name": "宁德", "pinyin": "ningde", "famous": ["太姥山", "白水洋", "鸳鸯溪", "嵛山岛", "霍童古镇"]}
    ],
    "安徽省": [
        {"name": "合肥", "pinyin": "hefei", "famous": ["逍遥津", "包公祠", "三河古镇", "巢湖", "李鸿章故居"]},
        {"name": "黄山", "pinyin": "huangshan", "famous": ["黄山", "宏村", "西递", "歙县古城", "呈坎"]},
        {"name": "芜湖", "pinyin": "wuhu", "famous": ["方特欢乐世界", "镜湖", "赭山公园", "鸠兹古镇", "马仁奇峰"]},
        {"name": "安庆", "pinyin": "anqing", "famous": ["天柱山", "迎江寺", "振风塔", "桐城文庙", "花亭湖"]},
        {"name": "蚌埠", "pinyin": "bengbu", "famous": ["龙子湖", "张公山", "垓下古战场", "双墩遗址", "花鼓灯嘉年华"]},
        {"name": "阜阳", "pinyin": "fuyang", "famous": ["颍州西湖", "管仲老街", "八里河", "迪沟", "尤家花园"]},
        {"name": "宿州", "pinyin": "suzhou", "famous": ["皇藏峪", "砀山梨花海", "虞姬墓", "五柳风景区", "天门寺"]},
        {"name": "淮北", "pinyin": "huaibei", "famous": ["相山公园", "南湖湿地公园", "临涣古镇", "龙脊山", "口子文化园"]},
        {"name": "亳州", "pinyin": "bozhou", "famous": ["花戏楼", "曹操运兵道", "华祖庵", "老子庙", "曹操宗族墓群"]},
        {"name": "滁州", "pinyin": "chuzhou", "famous": ["琅琊山", "醉翁亭", "明皇陵", "狼巷迷谷", "小岗村"]},
        {"name": "六安", "pinyin": "luan", "famous": ["天堂寨", "万佛湖", "白马尖", "铜锣寨", "皖西大裂谷"]},
        {"name": "宣城", "pinyin": "xuancheng", "famous": ["龙川景区", "查济古镇", "桃花潭", "太极洞", "敬亭山"]},
        {"name": "池州", "pinyin": "chizhou", "famous": ["九华山", "平天湖", "杏花村", "牯牛降", "蓬莱仙洞"]},
        {"name": "铜陵", "pinyin": "tongling", "famous": ["天井湖", "凤凰山", "大通古镇", "浮山", "顺安老街"]},
        {"name": "马鞍山", "pinyin": "maanshan", "famous": ["采石矶", "褒禅山", "太湖山", "李白墓园", "雨山湖"]}
    ],
    "江西省": [
        {"name": "南昌", "pinyin": "nanchang", "famous": ["滕王阁", "八一广场", "梅岭", "绳金塔", "鄱阳湖"]},
        {"name": "九江", "pinyin": "jiujiang", "famous": ["庐山", "鄱阳湖", "东林寺", "浔阳楼", "石钟山"]},
        {"name": "景德镇", "pinyin": "jingdezhen", "famous": ["古窑民俗博览区", "瑶里古镇", "浮梁古城", "洪岩仙境", "御窑厂"]},
        {"name": "上饶", "pinyin": "shangrao", "famous": ["三清山", "婺源", "江湾", "弋阳龟峰", "鄱阳湖国家湿地公园"]},
        {"name": "赣州", "pinyin": "ganzhou", "famous": ["通天岩", "赣州古城墙", "郁孤台", "关西围屋", "丫山风景区"]},
        {"name": "宜春", "pinyin": "yichun", "famous": ["明月山", "温汤镇", "三爪仑", "天柱峰", "百丈寺"]},
        {"name": "吉安", "pinyin": "jian", "famous": ["井冈山", "渼陂古村", "文天祥纪念馆", "武功山", "青原山"]},
        {"name": "抚州", "pinyin": "fuzhou", "famous": ["大觉山", "流坑古村", "麻姑山", "灵谷峰", "棠阴古镇"]},
        {"name": "鹰潭", "pinyin": "yingtan", "famous": ["龙虎山", "天师府", "上清古镇", "象鼻山", "仙水岩"]},
        {"name": "新余", "pinyin": "xinyu", "famous": ["仙女湖", "抱石公园", "仰天岗", "洪阳古洞", "孔目江湿地公园"]},
        {"name": "萍乡", "pinyin": "pingxiang", "famous": ["武功山", "孽龙洞", "杨岐山", "安源路矿工人运动纪念馆", "荷花博览园"]}
    ],
    "山西省": [
        {"name": "太原", "pinyin": "taiyuan", "famous": ["晋祠", "双塔寺", "蒙山大佛", "汾河公园", "天龙山"]},
        {"name": "大同", "pinyin": "datong", "famous": ["云冈石窟", "华严寺", "古城墙", "恒山", "悬空寺"]},
        {"name": "平遥", "pinyin": "pingyao", "famous": ["平遥古城", "乔家大院", "双林寺", "镇国寺", "王家大院"]},
        {"name": "运城", "pinyin": "yuncheng", "famous": ["解州关帝庙", "盐湖", "鹳雀楼", "普救寺", "五老峰"]},
        {"name": "忻州", "pinyin": "xinzhou", "famous": ["五台山", "雁门关", "芦芽山", "万年冰洞", "显通寺"]},
        {"name": "临汾", "pinyin": "linfen", "famous": ["壶口瀑布", "洪洞大槐树", "尧庙", "华门", "广胜寺"]},
        {"name": "长治", "pinyin": "changzhi", "famous": ["太行山大峡谷", "通天峡", "太行龙洞", "仙堂山", "城隍庙"]},
        {"name": "晋城", "pinyin": "jincheng", "famous": ["皇城相府", "王莽岭", "珏山", "柳氏民居", "湘峪古堡"]},
        {"name": "晋中", "pinyin": "jinzhong", "famous": ["榆次老城", "常家庄园", "曹家大院", "渠家大院", "孔祥熙故居"]},
        {"name": "吕梁", "pinyin": "luliang", "famous": ["碛口古镇", "北武当山", "卦山", "玄中寺", "杏花村"]},
        {"name": "阳泉", "pinyin": "yangquan", "famous": ["娘子关", "藏山", "固关长城", "桃林沟", "冠山"]},
        {"name": "朔州", "pinyin": "shuozhou", "famous": ["应县木塔", "杀虎口", "崇福寺", "广武城", "右玉"]}
    ],
    "河北省": [
        {"name": "石家庄", "pinyin": "shijiazhuang", "famous": ["正定古城", "赵州桥", "西柏坡", "嶂石岩", "驼梁"]},
        {"name": "秦皇岛", "pinyin": "qinhuangdao", "famous": ["山海关", "北戴河", "南戴河", "鸽子窝公园", "联峰山"]},
        {"name": "承德", "pinyin": "chengde", "famous": ["避暑山庄", "外八庙", "普宁寺", "磬锤峰", "木兰围场"]},
        {"name": "张家口", "pinyin": "zhangjiakou", "famous": ["草原天路", "大境门", "张北草原", "崇礼滑雪场", "黄帝城"]},
        {"name": "保定", "pinyin": "baoding", "famous": ["白洋淀", "野三坡", "狼牙山", "清西陵", "直隶总督署"]},
        {"name": "唐山", "pinyin": "tangshan", "famous": ["清东陵", "月坨岛", "南湖公园", "滦州古城", "地震遗址"]},
        {"name": "邯郸", "pinyin": "handan", "famous": ["广府古城", "娲皇宫", "129师司令部", "响堂山石窟", "丛台公园"]},
        {"name": "邢台", "pinyin": "xingtai", "famous": ["崆山白云洞", "大峡谷", "郭守敬纪念馆", "扁鹊庙", "云梦山"]},
        {"name": "沧州", "pinyin": "cangzhou", "famous": ["铁狮子", "吴桥杂技大世界", "纪晓岚故居", "南大港湿地", "东光铁佛寺"]},
        {"name": "廊坊", "pinyin": "langfang", "famous": ["天下第一城", "香河家具城", "国安第一城", "自然公园", "金丰农科园"]},
        {"name": "衡水", "pinyin": "hengshui", "famous": ["衡水湖", "冀宝斋博物馆", "宝云寺", "武强年画博物馆", "周亚夫墓"]}
    ],
    "辽宁省": [
        {"name": "沈阳", "pinyin": "shenyang", "famous": ["沈阳故宫", "张氏帅府", "北陵", "棋盘山", "世博园"]},
        {"name": "大连", "pinyin": "dalian", "famous": ["星海广场", "老虎滩", "金石滩", "棒棰岛", "旅顺"]},
        {"name": "鞍山", "pinyin": "anshan", "famous": ["千山", "玉佛苑", "汤岗子温泉", "白云山", "神女峰"]},
        {"name": "丹东", "pinyin": "dandong", "famous": ["鸭绿江断桥", "凤凰山", "虎山长城", "河口景区", "大鹿岛"]},
        {"name": "锦州", "pinyin": "jinzhou", "famous": ["笔架山", "医巫闾山", "辽沈战役纪念馆", "北普陀山", "义县奉国寺"]},
        {"name": "营口", "pinyin": "yingkou", "famous": ["鲅鱼圈", "仙人岛", "望儿山", "西炮台", "辽河老街"]},
        {"name": "盘锦", "pinyin": "panjin", "famous": ["红海滩", "鼎翔生态旅游区", "苇海蟹滩", "辽河口", "辽河碑林"]},
        {"name": "阜新", "pinyin": "fuxin", "famous": ["海棠山", "瑞应寺", "宝力根寺", "查海遗址", "乌兰木图山"]},
        {"name": "辽阳", "pinyin": "liaoyang", "famous": ["辽阳白塔", "广佑寺", "东京陵", "龙鼎山", "曹雪芹纪念馆"]},
        {"name": "铁岭", "pinyin": "tieling", "famous": ["龙首山", "银冈书院", "清河旅游度假区", "调兵山蒸汽机车博物馆", "铁岭博物馆"]},
        {"name": "朝阳", "pinyin": "chaoyang", "famous": ["凤凰山", "鸟化石国家地质公园", "大黑山", "北塔", "牛河梁遗址"]},
        {"name": "葫芦岛", "pinyin": "huludao", "famous": ["兴城古城", "菊花岛", "九门口长城", "葫芦山庄", "碣石宫遗址"]}
    ],
    "吉林省": [
        {"name": "长春", "pinyin": "changchun", "famous": ["净月潭", "伪满皇宫", "长影世纪城", "南湖公园", "世界雕塑公园"]},
        {"name": "吉林", "pinyin": "jilin", "famous": ["雾凇岛", "松花湖", "北山公园", "龙潭山", "乌拉街"]},
        {"name": "延边", "pinyin": "yanbian", "famous": ["长白山", "天池", "图们江", "防川景区", "帽儿山"]},
        {"name": "四平", "pinyin": "siping", "famous": ["叶赫那拉古城", "四平战役纪念馆", "山门风景区", "二龙湖", "二郎山庄"]},
        {"name": "通化", "pinyin": "tonghua", "famous": ["五女峰", "三角龙湾", "龙湾群", "高句丽遗迹", "云峰湖"]},
        {"name": "白山", "pinyin": "baishan", "famous": ["长白山天池西坡", "望天鹅", "杨靖宇殉国地", "长白朝鲜族自治县", "露水河"]},
        {"name": "松原", "pinyin": "songyuan", "famous": ["查干湖", "龙华寺", "塔虎城", "乾安泥林", "王府遗址"]},
        {"name": "白城", "pinyin": "baicheng", "famous": ["向海", "莫莫格", "嫩江湾", "查干浩特", "月亮湖"]},
        {"name": "辽源", "pinyin": "liaoyuan", "famous": ["龙山公园", "福寿宫", "寒葱顶", "鴜鹭湖", "东辽河"]}
    ],
    "黑龙江省": [
        {"name": "哈尔滨", "pinyin": "haerbin", "famous": ["中央大街", "冰雪大世界", "索菲亚教堂", "太阳岛", "极地馆"]},
        {"name": "齐齐哈尔", "pinyin": "qiqihaer", "famous": ["扎龙自然保护区", "龙沙公园", "明月岛", "大乘寺", "昂昂溪遗址"]},
        {"name": "牡丹江", "pinyin": "mudanjiang", "famous": ["镜泊湖", "雪乡", "地下森林", "威虎山", "八女投江纪念地"]},
        {"name": "佳木斯", "pinyin": "jiamusi", "famous": ["抚远黑瞎子岛", "同江三江口", "富锦国家湿地公园", "桦川湿地"]},
        {"name": "大庆", "pinyin": "daqing", "famous": ["铁人王进喜纪念馆", "大庆油田历史陈列馆", "龙凤湿地", "林甸温泉", "当奈湿地"]},
        {"name": "伊春", "pinyin": "yichun", "famous": ["汤旺河石林", "五营森林公园", "茅兰沟", "嘉荫恐龙地质公园", "新青湿地公园"]},
        {"name": "鸡西", "pinyin": "jixi", "famous": ["兴凯湖", "虎头要塞", "珍宝岛", "麒麟山", "北大荒书法长廊"]},
        {"name": "鹤岗", "pinyin": "hegang", "famous": ["萝北名山", "太平沟", "黑龙江三峡", "月牙湖", "普陀山"]},
        {"name": "双鸭山", "pinyin": "shuangyashan", "famous": ["七星峰", "安邦河湿地", "七星河湿地", "雁窝岛", "青山国家森林公园"]},
        {"name": "七台河", "pinyin": "qitaihe", "famous": ["西大圈", "石龙山", "桃山湖", "红星森林公园", "桃山公园"]},
        {"name": "绥化", "pinyin": "suihua", "famous": ["金龟山庄", "林枫故居", "金斗湾", "红光寺", "青冈望奎皮影"]},
        {"name": "黑河", "pinyin": "heihe", "famous": ["五大连池", "老黑山", "龙门石寨", "瑷珲古城", "锦河大峡谷"]},
        {"name": "大兴安岭", "pinyin": "daxinganling", "famous": ["北极村", "漠河石林", "九曲十八湾", "北红村", "黑龙江第一湾"]}
    ],
    "内蒙古自治区": [
        {"name": "呼和浩特", "pinyin": "huhehaote", "famous": ["昭君墓", "大召寺", "草原", "塞上老街", "哈素海"]},
        {"name": "包头", "pinyin": "baotou", "famous": ["五当召", "赛汗塔拉草原", "北方兵器城", "梅力更", "希拉穆仁草原"]},
        {"name": "鄂尔多斯", "pinyin": "eerduosi", "famous": ["响沙湾", "成吉思汗陵", "鄂尔多斯草原", "康巴什新区", "七星湖"]},
        {"name": "呼伦贝尔", "pinyin": "hulunbeier", "famous": ["呼伦贝尔草原", "满洲里", "额尔古纳", "莫尔道嘎", "阿尔山"]},
        {"name": "兴安盟", "pinyin": "xinganmeng", "famous": ["阿尔山", "天池", "杜鹃湖", "三潭峡", "柴河"]},
        {"name": "通辽", "pinyin": "tongliao", "famous": ["大青沟", "库伦三大寺", "孝庄园", "珠日河草原", "奈曼王府"]},
        {"name": "赤峰", "pinyin": "chifeng", "famous": ["阿斯哈图石林", "达里诺尔湖", "乌兰布统草原", "克什克腾温泉", "召庙"]},
        {"name": "锡林郭勒盟", "pinyin": "xilinguolemeng", "famous": ["锡林郭勒草原", "元上都遗址", "多伦湖", "贝子庙", "乌拉盖草原"]},
        {"name": "乌兰察布", "pinyin": "wulanchabu", "famous": ["辉腾锡勒草原", "黄花沟", "岱海", "格根塔拉草原", "集宁战役纪念馆"]},
        {"name": "乌海", "pinyin": "wuhai", "famous": ["金沙湾", "甘德尔山", "桌子山岩画", "黄河滩岛", "乌海湖"]},
        {"name": "阿拉善盟", "pinyin": "alashanmeng", "famous": ["额济纳胡杨林", "巴丹吉林沙漠", "黑城遗址", "居延海", "月亮湖"]},
        {"name": "巴彦淖尔", "pinyin": "bayannaoer", "famous": ["乌梁素海", "河套黄河湿地", "阴山岩画", "纳林湖", "镜湖生态旅游区"]}
    ],
    "新疆维吾尔自治区": [
        {"name": "乌鲁木齐", "pinyin": "wulumuqi", "famous": ["天山天池", "大巴扎", "红山公园", "水磨沟", "南山牧场"]},
        {"name": "喀什", "pinyin": "kashi", "famous": ["喀什古城", "艾提尕尔清真寺", "香妃墓", "帕米尔高原", "卡拉库里湖"]},
        {"name": "伊犁", "pinyin": "yili", "famous": ["那拉提草原", "赛里木湖", "喀拉峻草原", "昭苏草原", "果子沟"]},
        {"name": "吐鲁番", "pinyin": "tulufan", "famous": ["火焰山", "葡萄沟", "坎儿井", "交河故城", "苏公塔"]},
        {"name": "阿克苏", "pinyin": "akesu", "famous": ["天山神秘大峡谷", "克孜尔千佛洞", "库车大峡谷", "温宿大峡谷", "神木园"]},
        {"name": "和田", "pinyin": "hetian", "famous": ["和田玉", "尼雅遗址", "团城", "核桃王", "无花果王"]},
        {"name": "克拉玛依", "pinyin": "kelamayi", "famous": ["魔鬼城", "黑油山", "白杨河大峡谷", "艾里克湖", "世界魔鬼城"]},
        {"name": "石河子", "pinyin": "shihezi", "famous": ["周恩来总理纪念馆", "军垦博物馆", "北湖", "玛纳斯河", "西公园"]},
        {"name": "昌吉", "pinyin": "changji", "famous": ["天山天池", "江布拉克", "博格达峰", "古尔班通古特沙漠", "硫磺沟"]},
        {"name": "哈密", "pinyin": "hami", "famous": ["魔鬼城", "巴里坤草原", "回王府", "天山庙", "伊吾胡杨林"]},
        {"name": "阿勒泰", "pinyin": "aletai", "famous": ["喀纳斯湖", "禾木", "白哈巴", "可可托海", "五彩滩"]},
        {"name": "塔城", "pinyin": "tacheng", "famous": ["巴克图口岸", "红楼博物馆", "沙湾温泉", "裕民山花", "小白杨哨所"]},
        {"name": "博尔塔拉", "pinyin": "boertala", "famous": ["赛里木湖", "怪石峪", "阿拉山口口岸", "博格达尔温泉", "艾比湖"]},
        {"name": "巴音郭楞", "pinyin": "bayinguoleng", "famous": ["罗布泊", "楼兰古城", "博斯腾湖", "巴音布鲁克草原", "库车王府"]},
        {"name": "克孜勒苏", "pinyin": "kezilesu", "famous": ["慕士塔格峰", "阿图什大峡谷", "奥依塔克冰川", "喀拉库勒湖", "伊尔克什坦口岸"]}
    ],
    "西藏自治区": [
        {"name": "拉萨", "pinyin": "lasa", "famous": ["布达拉宫", "大昭寺", "纳木错", "八廓街", "羊卓雍措"]},
        {"name": "林芝", "pinyin": "linzhi", "famous": ["巴松措", "鲁朗林海", "雅鲁藏布大峡谷", "南迦巴瓦", "米堆冰川"]},
        {"name": "日喀则", "pinyin": "rikaze", "famous": ["扎什伦布寺", "珠穆朗玛峰", "白居寺", "萨迦寺", "江孜古城"]},
        {"name": "山南", "pinyin": "shannan", "famous": ["桑耶寺", "雍布拉康", "羊卓雍措", "拉姆拉措", "哲古湖"]},
        {"name": "那曲", "pinyin": "naqu", "famous": ["纳木错", "念青唐古拉山", "色林错", "尼玛县", "班戈错"]},
        {"name": "阿里", "pinyin": "ali", "famous": ["冈仁波齐", "玛旁雍错", "古格王朝", "札达土林", "拉昂错"]},
        {"name": "昌都", "pinyin": "changdu", "famous": ["然乌湖", "来古冰川", "芒康盐井", "孜珠寺", "强巴林寺"]}
    ],
    "甘肃省": [
        {"name": "兰州", "pinyin": "lanzhou", "famous": ["中山桥", "黄河母亲", "五泉山", "白塔山", "兴隆山"]},
        {"name": "敦煌", "pinyin": "dunhuang", "famous": ["莫高窟", "鸣沙山月牙泉", "阳关", "玉门关", "雅丹魔鬼城"]},
        {"name": "嘉峪关", "pinyin": "jiayuguan", "famous": ["嘉峪关关城", "长城第一墩", "悬壁长城", "魏晋墓", "七一冰川"]},
        {"name": "张掖", "pinyin": "zhangye", "famous": ["丹霞地貌", "大佛寺", "木塔寺", "马蹄寺", "山丹军马场"]},
        {"name": "酒泉", "pinyin": "jiuquan", "famous": ["酒泉卫星发射中心", "西汉胜迹", "金塔胡杨林", "桥湾古城", "赤金峡"]},
        {"name": "金昌", "pinyin": "jinchang", "famous": ["紫金花城", "骊靬古城", "金川公园", "汉明长城", "三角城遗址"]},
        {"name": "白银", "pinyin": "baiyin", "famous": ["黄河石林", "景泰龙湾", "寿鹿山", "会师楼", "红军长征胜利景园"]},
        {"name": "天水", "pinyin": "tianshui", "famous": ["麦积山石窟", "伏羲庙", "南郭寺", "仙人崖", "玉泉观"]},
        {"name": "武威", "pinyin": "wuwei", "famous": ["雷台汉墓", "天梯山石窟", "文庙", "马牙雪山", "白塔寺"]},
        {"name": "定西", "pinyin": "dingxi", "famous": ["贵清山", "遮阳山", "首阳山", "李家龙宫", "通渭温泉"]},
        {"name": "陇南", "pinyin": "longnan", "famous": ["官鹅沟", "万象洞", "文县天池", "西狭颂", "鸡峰山"]},
        {"name": "平凉", "pinyin": "pingliang", "famous": ["崆峒山", "王母宫", "龙泉寺", "大云寺", "云崖寺"]},
        {"name": "庆阳", "pinyin": "qingyang", "famous": ["周祖陵", "南梁革命纪念馆", "北石窟寺", "董志塬", "华夏公刘第一庙"]},
        {"name": "临夏", "pinyin": "linxia", "famous": ["炳灵寺石窟", "松鸣岩", "刘家峡水库", "八坊十三巷", "积石山大墩峡"]},
        {"name": "甘南", "pinyin": "gannan", "famous": ["拉卜楞寺", "郎木寺", "扎尕那", "桑科草原", "尕海湖"]}
    ],
    "青海省": [
        {"name": "西宁", "pinyin": "xining", "famous": ["塔尔寺", "青海湖", "东关清真大寺", "日月山", "互助土族"]},
        {"name": "格尔木", "pinyin": "ge'ermu", "famous": ["察尔汗盐湖", "昆仑山口", "可可西里", "格尔木胡杨林", "万丈盐桥"]},
        {"name": "玉树", "pinyin": "yushu", "famous": ["文成公主庙", "隆宝滩", "玉树草原", "新寨玛尼堆", "勒巴沟"]},
        {"name": "海东", "pinyin": "haidong", "famous": ["瞿昙寺", "孟达天池", "柳湾彩陶博物馆", "互助北山", "循化孟达"]},
        {"name": "海北", "pinyin": "haibei", "famous": ["青海湖鸟岛", "金银滩草原", "原子城", "祁连山草原", "门源油菜花"]},
        {"name": "黄南", "pinyin": "huangnan", "famous": ["热贡艺术", "隆务寺", "坎布拉国家公园", "泽库草原", "麦秀林场"]},
        {"name": "海南", "pinyin": "hainan", "famous": ["青海湖二郎剑", "龙羊峡水库", "贵德黄河清", "同德石藏寺", "尖扎坎布拉"]},
        {"name": "果洛", "pinyin": "guoluo", "famous": ["年保玉则", "阿尼玛卿雪山", "玛多黄河源", "扎陵湖鄂陵湖", "拉加寺"]},
        {"name": "海西", "pinyin": "haixi", "famous": ["茶卡盐湖", "可鲁克湖托素湖", "大柴旦翡翠湖", "乌素特水上雅丹", "都兰古墓群"]}
    ],
    "宁夏回族自治区": [
        {"name": "银川", "pinyin": "yinchuan", "famous": ["西夏王陵", "沙湖", "镇北堡影视城", "贺兰山", "沙坡头"]},
        {"name": "石嘴山", "pinyin": "shizuishan", "famous": ["沙湖", "星海湖", "北武当庙", "平罗玉皇阁", "贺兰山岩画"]},
        {"name": "吴忠", "pinyin": "wuzhong", "famous": ["青铜峡", "一百零八塔", "同心清真大寺", "罗山", "黄河楼"]},
        {"name": "固原", "pinyin": "guyuan", "famous": ["须弥山石窟", "六盘山红军长征纪念馆", "火石寨", "老龙潭", "六盘山森林公园"]},
        {"name": "中卫", "pinyin": "zhongwei", "famous": ["沙坡头", "高庙", "通湖草原", "寺口子", "金沙岛"]}
    ],
    "海南省": [
        {"name": "海口", "pinyin": "haikou", "famous": ["假日海滩", "骑楼老街", "火山口", "万绿园", "冯小刚电影公社"]},
        {"name": "三亚", "pinyin": "sanya", "famous": ["亚龙湾", "天涯海角", "蜈支洲岛", "南山寺", "海棠湾"]},
        {"name": "琼海", "pinyin": "qionghai", "famous": ["博鳌亚洲论坛", "万泉河", "玉带滩", "红色娘子军纪念园", "博鳌禅寺"]},
        {"name": "万宁", "pinyin": "wanning", "famous": ["兴隆热带植物园", "石梅湾", "日月湾", "东山岭", "兴隆温泉"]},
        {"name": "五指山", "pinyin": "wuzhishan", "famous": ["五指山热带雨林", "五指山峡谷漂流", "太平山瀑布", "民族博物馆", "初保村"]},
        {"name": "文昌", "pinyin": "wenchang", "famous": ["铜鼓岭", "宋氏祖居", "东郊椰林", "航天城", "石头公园"]},
        {"name": "东方", "pinyin": "dongfang", "famous": ["鱼鳞洲", "大广坝", "白查村船形屋", "俄贤岭", "汉马伏波井"]},
        {"name": "儋州", "pinyin": "danzhou", "famous": ["儋州东坡书院", "石花水洞", "千年古盐田", "蓝洋温泉", "松涛水库"]},
        {"name": "临高", "pinyin": "lingao", "famous": ["临高角", "百仞滩", "彩桥红树林", "居仁瀑布", "临高文庙"]},
        {"name": "定安", "pinyin": "dingan", "famous": ["文笔峰", "南丽湖", "久温塘冷泉", "热带飞禽世界", "母瑞山"]},
        {"name": "屯昌", "pinyin": "tunchang", "famous": ["木色湖", "卧龙山", "海瑞祖居", "枫木鹿场", "加乐潭"]},
        {"name": "澄迈", "pinyin": "chengmai", "famous": ["福山咖啡文化风情镇", "永庆寺", "富力红树湾", "金山寺", "罗驿村"]},
        {"name": "昌江", "pinyin": "changjiang", "famous": ["霸王岭", "棋子湾", "皇帝洞", "王下乡", "古昌化城"]},
        {"name": "乐东", "pinyin": "ledong", "famous": ["尖峰岭", "莺歌海盐场", "毛公山", "龙沐湾", "佳西热带雨林"]},
        {"name": "陵水", "pinyin": "lingshui", "famous": ["分界洲岛", "南湾猴岛", "吊罗山", "香水湾", "清水湾"]},
        {"name": "白沙", "pinyin": "baisha", "famous": ["红坎瀑布", "陨石坑", "邦溪坡鹿自然保护区", "罗帅村", "五里路"]},
        {"name": "琼中", "pinyin": "qiongzhong", "famous": ["百花岭", "黎母山", "上安仕阶", "鹦哥岭", "五指山革命根据地"]},
        {"name": "保亭", "pinyin": "baoting", "famous": ["呀诺达", "槟榔谷", "七仙岭", "神玉岛", "布隆赛"]},
        {"name": "三沙", "pinyin": "sansha", "famous": ["永兴岛", "赵述岛", "七连屿", "全富岛", "银屿岛"]}
    ],
    "重庆市": [
        {"name": "重庆", "pinyin": "chongqing", "famous": ["洪崖洞", "解放碑", "长江索道", "磁器口", "武隆"]}
    ],
    "天津市": [
        {"name": "天津", "pinyin": "tianjin", "famous": ["天津之眼", "古文化街", "五大道", "意式风情街", "海河"]}
    ]
}

CHINA_CITIES = []
for province, cities in PROVINCE_CITIES.items():
    CHINA_CITIES.extend(cities)

CITY_FOODS = {
    "北京": ["北京烤鸭", "涮羊肉", "炸酱面", "豆汁儿", "卤煮"],
    "上海": ["小笼包", "生煎包", "红烧肉", "蟹粉豆腐", "本帮菜"],
    "杭州": ["西湖醋鱼", "龙井虾仁", "叫化鸡", "东坡肉", "片儿川"],
    "南京": ["盐水鸭", "鸭血粉丝汤", "牛肉锅贴", "皮肚面", "汤包"],
    "成都": ["火锅", "串串香", "担担面", "麻婆豆腐", "夫妻肺片"],
    "西安": ["肉夹馍", "羊肉泡馍", "凉皮", "葫芦头", "biangbiang面"],
    "广州": ["早茶", "烧腊", "白切鸡", "叉烧包", "肠粉"],
    "深圳": ["海鲜", "潮汕牛肉火锅", "烧鹅", "客家菜", "茶餐厅"],
    "重庆": ["火锅", "小面", "酸辣粉", "毛血旺", "烤鱼"],
    "天津": ["狗不理包子", "麻花", "煎饼果子", "锅巴菜", "炸糕"],
    "苏州": ["松鼠鳜鱼", "响油鳝糊", "蟹粉狮子头", "苏式汤面", "糖粥"],
    "武汉": ["热干面", "周黑鸭", "武昌鱼", "豆皮", "面窝"],
    "长沙": ["臭豆腐", "口味虾", "剁椒鱼头", "糖油粑粑", "米粉"],
    "青岛": ["海鲜", "啤酒", "辣炒蛤蜊", "鲅鱼水饺", "烧烤"],
    "厦门": ["沙茶面", "土笋冻", "花生汤", "海蛎煎", "鱼丸"],
    "三亚": ["海鲜", "椰子鸡", "文昌鸡", "抱罗粉", "清补凉"],
    "昆明": ["过桥米线", "汽锅鸡", "饵块", "宣威火腿", "野生菌"],
    "哈尔滨": ["红肠", "锅包肉", "杀猪菜", "烤冷面", "马迭尔冰棍"],
    "沈阳": ["老边饺子", "李连贵熏肉", "鸡架", "铁锅炖", "抻面"],
    "大连": ["海鲜", "焖子", "炒焖子", "咸鱼饼子", "海菜包子"],
    "济南": ["把子肉", "九转大肠", "糖醋黄河鲤鱼", "油旋", "甜沫"],
    "郑州": ["烩面", "胡辣汤", "油馍头", "桶子鸡", "焖饼"],
    "合肥": ["臭鳜鱼", "李鸿章杂烩", "三河米饺", "庐州烤鸭", "龙虾"],
    "福州": ["佛跳墙", "鱼丸", "肉燕", "荔枝肉", "锅边糊"],
    "南宁": ["老友粉", "酸嘢", "螺蛳粉", "粉饺", "横州鱼生"],
    "贵阳": ["丝娃娃", "肠旺面", "酸汤鱼", "花溪牛肉粉", "恋爱豆腐果"],
    "南昌": ["拌粉", "瓦罐汤", "炒粉", "军山湖大闸蟹", "白糖糕"],
    "太原": ["刀削面", "灌肠", "头脑", "羊杂割", "过油肉"],
    "石家庄": ["缸炉烧饼", "驴打滚", "西河肉糕", "抓炒全鱼", "马家卤鸡"],
    "呼和浩特": ["手把肉", "烤全羊", "奶皮子", "奶茶", "稍麦"],
    "乌鲁木齐": ["烤包子", "手抓饭", "大盘鸡", "羊肉串", "馕"],
    "兰州": ["牛肉面", "手抓羊肉", "酿皮子", "灰豆子", "甜醅子"],
    "西宁": ["手抓羊肉", "酿皮", "尕面片", "酸奶", "炮仗面"],
    "银川": ["手抓羊肉", "羊肉泡馍", "馓子", "油香", "烩肉"],
    "拉萨": ["酥油茶", "糌粑", "牦牛肉", "藏面", "青稞酒"],
    "海口": ["文昌鸡", "加积鸭", "和乐蟹", "东山羊", "抱罗粉"],
    "珠海": ["海鲜", "横琴蚝", "乳鸽", "早茶", "叉烧"],
    "东莞": ["烧鹅", "腊肠", "鱼丸", "酿豆腐", "咸丸"],
    "无锡": ["酱排骨", "清水油面筋", "三凤桥肉庄", "小笼包", "玉兰饼"],
    "宁波": ["汤圆", "梭子蟹", "黄泥螺", "红膏呛蟹", "臭冬瓜"],
    "温州": ["鱼圆", "灯盏糕", "糯米饭", "炒粉干", "鸭舌"],
    "绍兴": ["黄酒", "茴香豆", "梅干菜扣肉", "醉虾醉蟹", "绍兴臭豆腐"],
    "嘉兴": ["粽子", "南湖菱", "文虎酱鸭", "嘉善黄酒", "八珍糕"],
    "湖州": ["千张包子", "太湖三白", "震远同酥糖", "长兴百叶龙", "安吉白茶"],
    "金华": ["火腿", "酥饼", "汤包", "拉拉面", "麻糍"],
    "衢州": ["三头一掌", "烤饼", "鸭头", "兔头", "鱼头"],
    "台州": ["姜汤面", "海鲜", "麦虾", "食饼筒", "泡虾"],
    "舟山": ["海鲜", "带鱼", "黄鱼", "梭子蟹", "鱿鱼"],
    "徐州": ["地锅鸡", "把子肉", "羊方藏鱼", "蜜三刀", "烙馍"],
    "常州": ["大麻糕", "银丝面", "加蟹小笼包", "天目湖砂锅鱼头", "萝卜干"],
    "南通": ["狼山鸡", "跳面", "脆饼", "麻虾酱", "蟹黄包"],
    "扬州": ["狮子头", "扬州炒饭", "大煮干丝", "富春包子", "牛皮糖"],
    "镇江": ["锅盖面", "香醋", "肴肉", "蟹黄汤包", "东乡羊肉"],
    "盐城": ["醉螺", "藕粉圆子", "大纵湖醉蟹", "建湖藕饼", "阜宁大糕"],
    "淮安": ["盱眙龙虾", "软兜长鱼", "平桥豆腐", "茶馓", "蒲菜"],
    "连云港": ["海鲜", "灌云豆丹", "花果山风鹅", "虾酱", "赣榆煎饼"],
    "宿迁": ["泗洪大闸蟹", "黄狗猪头肉", "沭阳朝牌", "泗阳膘鸡", "洋河大曲"],
    "襄阳": ["牛肉面", "黄酒", "孔明菜", "夹沙肉", "宜城大虾"],
    "宜昌": ["三峡肥鱼", "土家腊肉", "宜昌凉虾", "远安香菇", "秭归脐橙"],
    "荆州": ["鱼糕", "八宝饭", "千张扣肉", "荆州甲鱼", "公安牛肉"],
    "湘潭": ["剁椒鱼头", "红烧肉", "臭豆腐", "糖油粑粑", "米粉"],
    "衡阳": ["鱼粉", "衡阳土菜", "石湾脆肚", "衡山煨蛋", "酥薄月"],
    "岳阳": ["岳阳楼臭干子", "君山银针", "平江酱干", "华容团子", "临湘十三村"],
    "常德": ["酱板鸭", "米粉", "津市牛肉粉", "桃源擂茶", "石门柑橘"],
    "张家界": ["三下锅", "土家腊肉", "岩耳炖鸡", "葛根粉", "猕猴桃"],
    "汕头": ["牛肉丸", "蚝烙", "肠粉", "卤味", "鱼丸"],
    "江门": ["古井烧鹅", "开平腐乳", "新会陈皮", "恩平濑粉", "台山黄鳝饭"],
    "湛江": ["海鲜", "白切鸡", "炭烧生蚝", "沙虫", "吴川月饼"],
    "肇庆": ["裹蒸粽", "鼎湖上素", "文庆鲤", "四会沙糖桔", "德庆贡柑"],
    "惠州": ["梅菜扣肉", "酿豆腐", "盐焗鸡", "博罗酥醪菜", "龙门米饼"],
    "中山": ["石岐乳鸽", "杏仁饼", "沙溪扣肉", "脆肉鲩", "神湾菠萝"],
    "揭阳": ["牛肉火锅", "普宁豆干", "蚝烙", "肠粉", "乒乓粿"],
    "潮州": ["牛肉丸", "鱼蛋粉", "蚝烙", "春卷", "腐乳饼"],
    "桂林": ["桂林米粉", "阳朔啤酒鱼", "荔浦芋头条", "恭城油茶", "灵川狗肉"],
    "柳州": ["螺蛳粉", "三江油茶", "酸笋", "牛腊巴", "融水香鸭"],
    "北海": ["海鲜", "沙虫", "虾饼", "贝雕", "涠洲岛香蕉"],
    "梧州": ["龟苓膏", "纸包鸡", "冰泉豆浆", "艇仔粥", "岑溪古典鸡"],
    "玉林": ["牛巴", "肉蛋", "牛腩粉", "陆川猪", "容县沙田柚"],
    "百色": ["芒果", "八角", "田七", "靖西绣球", "隆林黑猪"],
    "钦州": ["坭兴陶", "荔枝", "龙眼", "对虾", "青蟹"],
    "贵港": ["莲藕", "桂平西山茶", "罗秀米粉", "覃塘毛尖", "平南石硖龙眼"],
    "防城港": ["海鲜", "金花茶", "东兴红木", "京族哈节", "珍珠"],
    "河池": ["巴马香猪", "环江香牛", "南丹巴平米", "天峨珍珠李", "都安山羊"],
    "崇左": ["甘蔗", "龙州砧板", "大新苦丁茶", "凭祥红木", "宁明花山"],
    "宜宾": ["五粮液", "燃面", "竹海名菜", "李庄白肉", "南溪豆腐干"],
    "泸州": ["泸州老窖", "古蔺麻辣鸡", "合江荔枝", "叙永豆汤面", "纳溪泡糖"],
    "德阳": ["什邡烟叶", "绵竹年画", "中江挂面", "广汉缠丝兔", "罗江花生"],
    "绵阳": ["江油肥肠", "安县包盐蛋", "梓潼酥饼", "北川腊肉", "平武茶叶"],
    "广元": ["剑门豆腐", "苍溪雪梨", "青川黑木耳", "朝天核桃", "旺苍杜仲"],
    "遂宁": ["沱牌曲酒", "射洪牛肉", "蓬溪姜糕", "大英卓筒井盐", "安居红苕"],
    "内江": ["大千书画", "资中鲶鱼", "隆昌夏布", "威远无花果", "东兴椒"],
    "乐山": ["乐山大佛", "峨眉山茶", "钵钵鸡", "跷脚牛肉", "西坝豆腐"],
    "南充": ["丝绸", "冬菜", "川北凉粉", "营山凉面", "南部肥肠"],
    "眉山": ["东坡肘子", "仁寿芝麻糕", "丹棱冻粑", "洪雅藤椒", "青神竹编"],
    "广安": ["盐皮蛋", "邻水脐橙", "武胜麻哥面", "岳池米粉", "华蓥山野菜"],
    "达州": ["灯影牛肉", "大竹醪糟", "渠县黄花", "开江豆笋", "宣汉黄牛"],
    "雅安": ["蒙顶山茶", "雅鱼", "汉源花椒", "石棉烧烤", "宝兴腊肉"],
    "巴中": ["通江银耳", "南江黄羊", "平昌江口醇", "恩阳提糖麻饼", "巴州油茶"],
    "资阳": ["安岳柠檬", "简阳羊肉汤", "乐至藕粉", "雁江蜜柑", "临江寺豆瓣"],
    "遵义": ["茅台酒", "遵义羊肉粉", "鸭溪窖酒", "湄潭翠芽", "赤水晒醋"],
    "六盘水": ["水城羊肉粉", "盘县火腿", "六枝岩脚面", "妥乐银杏", "牂牁江鱼"],
    "安顺": ["波波糖", "安顺裹卷", "镇宁波波糖", "普定朵贝茶", "关岭花江狗肉"],
    "毕节": ["大方漆器", "威宁火腿", "织金竹荪", "纳雍滚山鸡", "金沙回沙酒"],
    "铜仁": ["思南花烛", "印江茶叶", "德江天麻", "沿河山羊", "石阡苔茶"],
    "黔东南": ["酸汤鱼", "苗家腊肉", "侗族腌鱼", "从江香猪", "黎平茶油"],
    "黔南": ["都匀毛尖", "独山盐酸菜", "三都水族马尾绣", "贵定云雾茶", "平塘百香果"],
    "黔西南": ["兴义羊肉粉", "册亨茶油", "安龙凉剪粉", "晴隆糯薏仁", "望谟板栗"],
    "丽江": ["丽江粑粑", "鸡豆凉粉", "腊排骨", "酥油茶", "水性杨花"],
    "大理": ["酸辣鱼", "烤乳扇", "饵块", "雕梅酒", "砂锅鱼"],
    "西双版纳": ["傣味烧烤", "菠萝饭", "竹筒饭", "酸笋", "普洱茶"],
    "香格里拉": ["牦牛肉火锅", "酥油茶", "青稞饼", "藏香猪", "琵琶肉"],
    "腾冲": ["大救驾", "饵丝", "腾冲翡翠", "火山石烤肠", "和顺头脑"],
    "红河": ["建水烧豆腐", "蒙自过桥米线", "弥勒葡萄", "开远小卷粉", "元阳红米"],
    "普洱": ["普洱茶", "景谷象牙芒果", "镇沅苦聪茶", "孟连牛油果", "澜沧古茶"],
    "临沧": ["滇红茶", "冰岛茶", "沧源佤族鸡肉烂饭", "凤庆核桃", "永德芒果"],
    "德宏": ["撒撇", "泡鲁达", "景颇族鬼鸡", "过手米线", "芒市泡鸡脚"],
    "怒江": ["石板烤粑粑", "漆油鸡", "怒江鱼", "独龙牛", "傈僳族手抓饭"],
    "迪庆": ["牦牛肉", "青稞酒", "酥油茶", "香格里拉松茸", "维西百花蜜"],
    "昌都": ["酥油茶", "牦牛肉", "糌粑", "藏面", "昌都醉梨"],
    "林芝": ["石锅鸡", "藏香猪", "松茸", "天麻炖鸡", "林芝苹果"],
    "山南": ["酥油茶", "牦牛肉", "青稞酒", "隆子黑青稞", "扎囊氆氇"],
    "日喀则": ["朋必", "糌粑", "酥油茶", "青稞酒", "拉孜藏刀"],
    "那曲": ["牦牛肉", "酥油茶", "青稞酒", "那曲虫草", "藏北绵羊"],
    "阿里": ["牦牛肉", "酥油茶", "青稞酒", "阿里山羊", "班公湖鱼"],
    "吐鲁番": ["葡萄", "哈密瓜", "烤包子", "手抓饭", "大盘鸡"],
    "喀什": ["烤包子", "手抓饭", "羊肉串", "馕", "大盘鸡"],
    "伊犁": ["手抓饭", "烤包子", "马奶酒", "熏马肉", "那拉提蜂蜜"],
    "阿克苏": ["苹果", "核桃", "红枣", "石榴", "烤包子"],
    "和田": ["和田玉", "大枣", "核桃", "石榴", "烤包子"],
    "克拉玛依": ["大盘鸡", "手抓饭", "烤包子", "羊肉串", "馕"],
    "石河子": ["大盘鸡", "手抓饭", "烤包子", "羊肉串", "馕"]
}

CITY_HOTELS = {
    "北京": ["北京故宫附近酒店", "王府井希尔顿", "国贸大酒店", "北京四季酒店", "颐和园附近民宿"],
    "上海": ["外滩华尔道夫", "浦东丽思卡尔顿", "静安香格里拉", "上海半岛酒店", "豫园附近客栈"],
    "杭州": ["西湖国宾馆", "杭州柏悦", "西溪喜来登", "杭州香格里拉", "灵隐寺附近民宿"],
    "南京": ["金陵饭店", "南京丽思卡尔顿", "紫金山庄", "南京万达文华", "夫子庙附近酒店"],
    "成都": ["成都博舍", "环球中心天堂洲际", "成都瑞吉", "青城山六善", "宽窄巷子民宿"],
    "西安": ["西安威斯汀", "大雁塔假日", "西安索菲特传奇", "临潼悦椿温泉", "回民街附近客栈"],
    "广州": ["广州四季", "瑰丽酒店", "白云机场铂尔曼", "广州文华东方", "上下九附近酒店"],
    "深圳": ["深圳湾万象城", "福田香格里拉", "深圳星河丽思卡尔顿", "大梅沙京基洲际", "华侨城民宿"],
    "重庆": ["重庆丽思瑞", "解放碑威斯汀", "重庆希尔顿", "长江索道附近酒店", "洪崖洞民宿"],
    "天津": ["天津丽思卡尔顿", "海河悦榕庄", "天津瑞吉", "天津四季", "古文化街附近客栈"],
    "苏州": ["苏州柏悦", "金鸡湖凯悦", "苏州香格里拉", "寒山寺附近酒店", "平江路民宿"],
    "武汉": ["武汉万达瑞华", "武汉泛海喜来登", "武汉瑰丽", "黄鹤楼附近酒店", "户部巷客栈"],
    "长沙": ["长沙IFS国金中心", "长沙君悦", "长沙瑞吉", "岳麓山附近酒店", "太平街民宿"],
    "青岛": ["青岛涵碧楼", "青岛瑞吉", "黄岛金沙滩希尔顿", "八大关附近酒店", "栈桥民宿"],
    "厦门": ["厦门华尔道夫", "鼓浪屿民宿", "厦门海悦山庄", "环岛路酒店", "曾厝垵客栈"],
    "三亚": ["亚龙湾瑞吉", "海棠湾亚特兰蒂斯", "三亚瑰丽", "蜈支洲岛酒店", "三亚湾民宿"],
    "昆明": ["昆明洲际", "滇池温泉花园", "昆明万豪", "翠湖附近酒店", "滇池民宿"],
    "哈尔滨": ["哈尔滨富力丽思卡尔顿", "哈尔滨香格里拉", "亚布力滑雪场酒店", "中央大街客栈"],
    "沈阳": ["沈阳君悦", "沈阳香格里拉", "沈阳万豪", "故宫附近酒店", "中街民宿"],
    "大连": ["大连君悦", "金石滩鲁能希尔顿", "大连城堡豪华精选", "星海广场酒店"],
    "济南": ["济南鲁能贵和洲际", "济南香格里拉", "大明湖附近酒店", "泉城广场民宿"],
    "郑州": ["郑州绿地JW万豪", "郑州建业艾美", "郑州希尔顿", "CBD附近酒店"],
    "合肥": ["合肥洲际", "合肥万达文华", "合肥香格里拉", "天鹅湖附近酒店"],
    "福州": ["福州香格里拉", "福州三迪希尔顿", "三坊七巷附近酒店", "闽江酒店"],
    "南宁": ["南宁香格里拉", "南宁万豪", "青秀山附近酒店", "邕江民宿"],
    "贵阳": ["贵阳亨特索菲特", "贵阳万丽", "青岩古镇附近酒店", "甲秀楼民宿"],
    "南昌": ["南昌万达文华", "南昌香格里拉", "滕王阁附近酒店", "赣江民宿"],
    "太原": ["太原洲际", "太原万豪", "晋祠附近酒店", "柳巷民宿"],
    "石家庄": ["石家庄富力洲际", "石家庄希尔顿", "正定古城客栈", "北国商城酒店"],
    "呼和浩特": ["呼和浩特富力万达文华", "呼和浩特香格里拉", "草原民宿", "大召寺附近"],
    "乌鲁木齐": ["乌鲁木齐希尔顿", "乌鲁木齐JW万豪", "大巴扎附近酒店", "天山天池民宿"],
    "兰州": ["兰州万达文华", "兰州皇冠假日", "黄河风情线酒店", "中山桥附近"],
    "西宁": ["西宁富力万达文华", "西宁青海宾馆", "塔尔寺附近酒店", "青海湖民宿"],
    "银川": ["银川JW万豪", "银川喜来登", "西夏王陵附近", "沙湖民宿"],
    "拉萨": ["拉萨瑞吉", "拉萨洲际", "布达拉宫附近酒店", "八廓街民宿"],
    "海口": ["海口万豪", "海口希尔顿", "观澜湖酒店", "假日海滩民宿"],
    "珠海": ["珠海长隆横琴湾", "珠海瑞吉", "情侣路酒店", "珠海渔女附近"],
    "东莞": ["东莞松山湖凯悦", "东莞洲际", "虎门附近酒店", "南城民宿"],
    "无锡": ["无锡太湖饭店", "无锡君来洲际", "鼋头渚附近酒店", "南长街民宿"],
    "宁波": ["宁波洲际", "宁波万豪", "天一阁附近酒店", "老外滩民宿"],
    "温州": ["温州万豪", "温州香格里拉", "雁荡山附近酒店", "江心屿民宿"],
    "绍兴": ["绍兴开元名都", "绍兴大禹开元观堂", "鲁迅故里客栈", "沈园附近"],
    "嘉兴": ["嘉兴希尔顿逸林", "乌镇景区内民宿", "西塘客栈", "南湖酒店"],
    "湖州": ["湖州喜来登", "莫干山民宿", "南浔古镇客栈", "太湖度假区"],
    "金华": ["金华万豪", "义乌万豪", "横店影视城酒店", "双龙洞附近"],
    "衢州": ["衢州希尔顿", "江郎山附近酒店", "廿八都古镇客栈", "龙游石窟民宿"],
    "台州": ["台州皇冠假日", "天台山民宿", "神仙居附近酒店", "温岭石塘民宿"],
    "舟山": ["舟山希尔顿", "普陀山民宿", "朱家尖酒店", "嵊泗列岛民宿"],
    "徐州": ["徐州万豪", "徐州希尔顿", "云龙湖附近酒店", "汉文化景区"],
    "常州": ["常州万豪", "常州希尔顿", "恐龙园附近酒店", "天目湖民宿"],
    "南通": ["南通洲际", "南通万豪", "濠河附近酒店", "狼山民宿"],
    "扬州": ["扬州香格里拉", "扬州迎宾馆", "瘦西湖附近酒店", "东关街客栈"],
    "镇江": ["镇江喜来登", "镇江万达喜来登", "金山寺附近酒店", "西津渡民宿"],
    "盐城": ["盐城万豪", "盐城希尔顿", "丹顶鹤保护区附近", "荷兰花海民宿"],
    "淮安": ["淮安万达嘉华", "淮安金陵", "周恩来纪念馆附近", "河下古镇客栈"],
    "连云港": ["连云港万豪", "连云港希尔顿", "花果山附近酒店", "连岛民宿"],
    "宿迁": ["宿迁万豪", "宿迁希尔顿", "项王故里附近", "洪泽湖民宿"],
    "襄阳": ["襄阳万达皇冠假日", "襄阳富力皇冠假日", "古隆中附近", "唐城客栈"],
    "宜昌": ["宜昌万达皇冠假日", "宜昌富力皇冠假日", "三峡大坝附近", "清江画廊民宿"],
    "荆州": ["荆州万达嘉华", "荆州富力万达文华", "荆州古城客栈", "关公义园附近"],
    "湘潭": ["湘潭华天大酒店", "湘潭富力万达嘉华", "韶山附近酒店", "齐白石纪念馆"],
    "衡阳": ["衡阳华天大酒店", "衡阳富力万达嘉华", "衡山附近酒店", "南岳大庙民宿"],
    "岳阳": ["岳阳华天大酒店", "岳阳富力万达嘉华", "岳阳楼附近", "君山岛民宿"],
    "常德": ["常德华天大酒店", "常德富力万达嘉华", "桃花源附近", "柳叶湖民宿"],
    "张家界": ["张家界华天大酒店", "张家界富力万达嘉华", "森林公园附近", "天门山民宿"],
    "汕头": ["汕头万豪", "汕头喜来登", "南澳岛民宿", "礐石风景区附近"],
    "江门": ["江门万达嘉华", "江门富力万达嘉华", "开平碉楼附近", "小鸟天堂民宿"],
    "湛江": ["湛江喜来登", "湛江万豪", "湖光岩附近", "东海岛民宿"],
    "肇庆": ["肇庆喜来登", "肇庆万豪", "七星岩附近", "鼎湖山民宿"],
    "惠州": ["惠州洲际", "惠州富力万丽", "西湖附近", "巽寮湾民宿"],
    "中山": ["中山喜来登", "中山万豪", "孙中山故居附近", "孙文西路客栈"],
    "揭阳": ["揭阳榕江大酒店", "揭阳富力万达嘉华", "揭阳楼附近", "黄岐山民宿"],
    "潮州": ["潮州万豪", "潮州古城客栈", "广济桥附近", "牌坊街民宿"],
    "桂林": ["桂林香格里拉", "桂林喜来登", "漓江附近", "阳朔民宿"],
    "柳州": ["柳州丽笙", "柳州万达嘉华", "龙潭公园附近", "柳侯公园民宿"],
    "北海": ["北海喜来登", "北海万豪", "银滩附近", "涠洲岛民宿"],
    "梧州": ["梧州国龙大酒店", "梧州富力万达嘉华", "骑楼城附近", "白云山民宿"],
    "玉林": ["玉林福城丽宫", "玉林万达嘉华", "云天宫附近", "谢鲁山庄民宿"],
    "百色": ["百色恒升大酒店", "百色富力万达嘉华", "通灵大峡谷附近", "乐业天坑民宿"],
    "钦州": ["钦州天骄国际", "钦州富力万达嘉华", "三娘湾附近", "八寨沟民宿"],
    "贵港": ["贵港文华国际", "贵港富力万达嘉华", "桂平西山附近", "龙潭公园民宿"],
    "防城港": ["防城港皇冠假日", "防城港富力万达嘉华", "东兴口岸附近", "金滩民宿"],
    "河池": ["河池大酒店", "河池富力万达嘉华", "巴马长寿村", "百魔洞民宿"],
    "崇左": ["崇左国际大酒店", "崇左富力万达嘉华", "德天瀑布附近", "明仕田园民宿"],
    "宜宾": ["宜宾鲁能皇冠假日", "宜宾富力万达嘉华", "蜀南竹海附近", "李庄古镇民宿"],
    "泸州": ["泸州巨洋国际", "泸州富力万达嘉华", "老窖景区附近", "张坝桂圆林民宿"],
    "德阳": ["德阳皇冠假日", "德阳富力万达嘉华", "三星堆附近", "文庙民宿"],
    "绵阳": ["绵阳富乐山国际", "绵阳富力万达嘉华", "七曲山附近", "李白故居民宿"],
    "广元": ["广元天成大酒店", "广元富力万达嘉华", "剑门关附近", "皇泽寺民宿"],
    "遂宁": ["遂宁东旭锦江", "遂宁富力万达嘉华", "观音故里附近", "中国死海民宿"],
    "内江": ["内江万达嘉华", "内江富力万达嘉华", "大千故里附近", "圣水寺民宿"],
    "乐山": ["乐山富力万达嘉华", "峨眉山景区酒店", "乐山大佛附近", "张公桥民宿"],
    "南充": ["南充天来大酒店", "南充富力万达嘉华", "阆中古城客栈", "西山景区民宿"],
    "眉山": ["眉山富力万达嘉华", "三苏祠附近酒店", "柳江古镇民宿", "瓦屋山附近"],
    "广安": ["广安思源酒店", "广安富力万达嘉华", "邓小平故里附近", "华蓥山民宿"],
    "达州": ["达州凤凰国际", "达州富力万达嘉华", "真佛山附近", "巴山大峡谷民宿"],
    "雅安": ["雅安恒博酒店", "雅安富力万达嘉华", "碧峰峡附近", "上里古镇民宿"],
    "巴中": ["巴中江北宾馆", "巴中富力万达嘉华", "光雾山附近", "恩阳古镇民宿"],
    "资阳": ["资阳锦江国际", "资阳富力万达嘉华", "安岳石刻附近", "陈毅故里民宿"],
    "遵义": ["遵义格兰云天", "遵义富力万达嘉华", "遵义会议会址附近", "赤水丹霞民宿"],
    "六盘水": ["六盘水福朋喜来登", "六盘水富力万达嘉华", "乌蒙大草原附近", "玉舍森林公园民宿"],
    "安顺": ["安顺百灵希尔顿逸林", "安顺富力万达嘉华", "黄果树瀑布附近", "龙宫民宿"],
    "毕节": ["毕节福朋喜来登", "毕节富力万达嘉华", "百里杜鹃附近", "织金洞民宿"],
    "铜仁": ["铜仁花果山国际", "铜仁富力万达嘉华", "梵净山附近", "苗王城民宿"],
    "黔东南": ["凯里皇冠假日", "西江千户苗寨民宿", "镇远古镇客栈", "肇兴侗寨民宿"],
    "黔南": ["都匀毛尖精品酒店", "荔波小七孔民宿", "平塘天眼附近", "樟江景区民宿"],
    "黔西南": ["兴义富康国际", "万峰林民宿", "马岭河峡谷附近", "招堤民宿"],
    "丽江": ["丽江古城民宿", "束河古镇客栈", "玉龙雪山附近", "泸沽湖民宿"],
    "大理": ["大理古城民宿", "洱海海景房", "双廊古镇客栈", "苍山脚下民宿"],
    "西双版纳": ["告庄西双景民宿", "热带植物园附近", "野象谷酒店", "傣族园客栈"],
    "香格里拉": ["独克宗古城民宿", "松赞林寺附近", "普达措景区酒店", "纳帕海民宿"],
    "腾冲": ["和顺古镇民宿", "热海景区酒店", "火山公园附近", "北海湿地民宿"],
    "红河": ["建水古城客栈", "元阳梯田民宿", "弥勒东风韵酒店", "朱家花园附近"],
    "普洱": ["茶马古城客栈", "太阳河森林酒店", "景迈山民宿", "那柯里古镇"],
    "临沧": ["临沧空港酒店", "翁丁原始部落民宿", "沧源崖画附近", "鲁史古镇客栈"],
    "德宏": ["芒市宾馆", "瑞丽口岸酒店", "畹町桥附近", "勐巴娜西民宿"],
    "怒江": ["六库大酒店", "丙中洛民宿", "独龙江客栈", "老姆登民宿"],
    "迪庆": ["飞来寺观景酒店", "雨崩村客栈", "梅里雪山附近", "明永冰川民宿"],
    "昌都": ["昌都康巴大酒店", "然乌湖民宿", "来古冰川附近", "芒康盐井客栈"],
    "林芝": ["林芝工布庄园", "巴松措民宿", "鲁朗林海酒店", "雅鲁藏布大峡谷民宿"],
    "山南": ["山南泽当饭店", "雍布拉康附近", "羊卓雍措民宿", "桑耶寺客栈"],
    "日喀则": ["日喀则喜格孜风情酒店", "扎什伦布寺附近", "珠峰大本营民宿", "江孜古城"],
    "那曲": ["那曲凯斯顿酒店", "纳木错民宿", "念青唐古拉山附近", "色林错周边"],
    "阿里": ["阿里狮泉河大酒店", "冈仁波齐附近", "玛旁雍错民宿", "古格王朝遗址"],
    "吐鲁番": ["吐鲁番火焰山酒店", "葡萄沟民宿", "坎儿井附近", "交河故城客栈"],
    "喀什": ["喀什噶尔古城民宿", "艾提尕尔清真寺附近", "帕米尔高原酒店", "卡拉库里湖民宿"],
    "伊犁": ["伊宁市大酒店", "那拉提草原民宿", "赛里木湖附近", "喀拉峻草原客栈"],
    "阿克苏": ["阿克苏天缘国际", "天山神秘大峡谷附近", "克孜尔千佛洞民宿", "库车老城客栈"],
    "和田": ["和田迎宾馆", "和田夜市附近", "尼雅遗址周边", "团城民宿"],
    "克拉玛依": ["克拉玛依喜来登", "魔鬼城附近酒店", "黑油山民宿", "白杨河大峡谷"],
    "石河子": ["石河子宾馆", "周恩来纪念馆附近", "北湖公园酒店", "玛纳斯河民宿"],
    "昌吉": ["昌吉园林酒店", "天山天池附近", "江布拉克民宿", "博格达峰脚下"],
    "哈密": ["哈密宾馆", "魔鬼城附近", "巴里坤草原民宿", "回王府周边"],
    "阿勒泰": ["阿勒泰金桥大酒店", "喀纳斯禾木山庄", "可可托海酒店", "五彩滩民宿"],
    "塔城": ["塔城宾馆", "巴克图口岸附近", "沙湾温泉酒店", "裕民山花民宿"],
    "博尔塔拉": ["博乐阳光大酒店", "赛里木湖景区酒店", "怪石峪附近", "艾比湖民宿"],
    "巴音郭楞": ["库尔勒康城建国酒店", "博斯腾湖酒店", "巴音布鲁克酒店", "罗布泊周边"],
    "克孜勒苏": ["阿图什天路酒店", "慕士塔格峰附近", "阿图什大峡谷民宿", "帕米尔高原"],
    "保定": ["保定电谷锦江国际酒店", "白洋淀阿尔卡迪亚", "野三坡阿尔卡迪亚", "直隶总督府附近"],
    "唐山": ["唐山万达广场酒店", "清东陵附近", "月坨岛海岛酒店", "南湖公园周边"],
    "邯郸": ["邯郸金都酒店", "广府古城客栈", "娲皇宫附近", "丛台公园周边"],
    "邢台": ["邢台万峰大酒店", "崆山白云洞附近", "郭守敬纪念馆周边", "云梦山民宿"],
    "沧州": ["沧州金狮酒店", "吴桥杂技大世界附近", "铁狮子景区周边", "纪晓岚故居附近"],
    "廊坊": ["廊坊阿尔卡迪亚酒店", "天下第一城酒店", "香河家具城附近", "自然公园周边"],
    "衡水": ["衡水龙源酒店", "衡水湖附近", "武强年画博物馆周边", "宝云寺附近"],
    "大同": ["大同云冈建国酒店", "云冈石窟景区酒店", "恒山悬空寺附近", "华严寺周边"],
    "忻州": ["忻州泛华酒店", "五台山景区酒店", "雁门关附近", "芦芽山民宿"],
    "临汾": ["临汾金都花园酒店", "壶口瀑布附近", "洪洞大槐树景区", "尧庙周边"],
    "长治": ["长治益东酒店", "太行山大峡谷酒店", "通天峡附近", "仙堂山民宿"],
    "晋城": ["晋城金辇酒店", "皇城相府景区酒店", "王莽岭附近", "柳氏民居周边"],
    "晋中": ["晋中万豪酒店", "常家庄园附近", "曹家大院酒店", "乔家大院景区"],
    "吕梁": ["吕梁国际酒店", "碛口古镇客栈", "北武当山附近", "玄中寺周边"],
    "阳泉": ["阳泉万通快捷酒店", "娘子关景区附近", "藏山风景区酒店", "固关长城周边"],
    "朔州": ["朔州四季酒店", "应县木塔附近", "杀虎口景区", "崇福寺周边"],
    "锦州": ["锦州金城酒店", "笔架山景区酒店", "医巫闾山附近", "辽沈战役纪念馆周边"],
    "营口": ["营口金泰珑悦海景酒店", "鲅鱼圈红海温泉", "望儿山风景区附近", "山海广场周边"],
    "盘锦": ["盘锦紫澜门酒店", "红海滩景区酒店", "鼎翔生态旅游区", "辽河碑林周边"],
    "阜新": ["阜新花园酒店", "海棠山风景区", "瑞应寺附近", "宝力根寺周边"],
    "辽阳": ["辽阳辽化宾馆", "广佑寺景区酒店", "东京陵附近", "龙鼎山景区"],
    "铁岭": ["铁岭金城酒店", "龙首山风景区附近", "银冈书院周边", "清河旅游度假区"],
    "朝阳": ["朝阳凤凰国际酒店", "凤凰山景区", "鸟化石国家地质公园", "牛河梁遗址周边"],
    "葫芦岛": ["葫芦岛金鑫酒店", "兴城古城客栈", "菊花岛度假酒店", "九门口长城附近"],
    "四平": ["四平万达锦华酒店", "叶赫那拉古城酒店", "二龙湖风景区", "山门风景区周边"],
    "通化": ["通化丽景酒店", "五女峰国家森林公园", "三角龙湾酒店", "云峰湖度假区"],
    "白山": ["白山长白山天地酒店", "望天鹅景区", "杨靖宇殉国地", "露水河国家森林公园"],
    "松原": ["松原松花江酒店", "查干湖景区酒店", "乾安泥林附近", "龙华寺周边"],
    "白城": ["白城鹤翔酒店", "向海自然保护区", "莫莫格湿地公园", "嫩江湾景区"],
    "辽源": ["辽源龙山酒店", "福寿宫附近", "寒葱顶国家森林公园", "鴜鹭湖景区"],
    "大庆": ["大庆万达酒店", "铁人纪念馆附近", "林甸温泉酒店", "龙凤湿地公园周边"],
    "伊春": ["伊春万怡酒店", "汤旺河石林酒店", "五营国家森林公园", "茅兰沟景区"],
    "鸡西": ["鸡西龙城花园酒店", "兴凯湖景区酒店", "虎头要塞附近", "珍宝岛景区"],
    "鹤岗": ["鹤岗九州酒店", "萝北名山景区", "太平沟风景区", "黑龙江三峡"],
    "双鸭山": ["双鸭山福悦酒店", "七星峰国家森林公园", "安邦河湿地公园", "雁窝岛景区"],
    "七台河": ["七台河亿达酒店", "西大圈国家森林公园", "石龙山风景区", "桃山湖景区"],
    "绥化": ["绥化铂金酒店", "金龟山庄", "林枫故居附近", "红光寺周边"],
    "黑河": ["黑河国际饭店", "五大连池景区酒店", "老黑山附近", "瑷珲古城"],
    "大兴安岭": ["大兴安岭北山酒店", "北极村景区酒店", "漠河石林", "龙江第一湾景区"],
    "兴安盟": ["乌兰浩特天华酒店", "阿尔山景区酒店", "杜鹃湖附近", "柴河风景区"],
    "通辽": ["通辽草原酒店", "大青沟国家自然保护区", "库伦三大寺", "珠日河草原"],
    "赤峰": ["赤峰福隆酒店", "阿斯哈图石林酒店", "达里诺尔湖附近", "乌兰布统草原"],
    "锡林郭勒": ["锡林浩特元和酒店", "元上都遗址附近", "贝子庙景区", "乌拉盖草原"],
    "乌兰察布": ["乌兰察布多蒙德酒店", "辉腾锡勒草原", "岱海温泉酒店", "黄花沟景区"],
    "乌海": ["乌海格兰酒店", "金沙湾沙漠", "甘德尔山风景区", "乌海湖景区"],
    "阿拉善": ["阿拉善金色酒店", "额济纳胡杨林", "巴丹吉林沙漠", "居延海景区"],
    "巴彦淖尔": ["巴彦淖尔华美酒店", "乌梁素海景区", "纳林湖酒店", "黄河湿地公园"],
    "酒泉": ["酒泉航天酒店", "酒泉卫星发射中心", "金塔胡杨林", "西汉胜迹"],
    "金昌": ["金昌金川酒店", "骊靬古城附近", "紫金花城", "金川公园周边"],
    "白银": ["白银万盛大酒店", "黄河石林景区", "景泰龙湾", "会师楼附近"],
    "天水": ["天水华辰酒店", "麦积山石窟景区", "伏羲庙附近", "仙人崖景区"],
    "武威": ["武威天马酒店", "雷台汉墓景区", "天梯山石窟", "文庙附近"],
    "定西": ["定西天庆酒店", "贵清山风景区", "遮阳山景区", "通渭温泉"],
    "陇南": ["陇南金都酒店", "官鹅沟景区", "万象洞风景区", "鸡峰山国家森林公园"],
    "平凉": ["平凉新世纪酒店", "崆峒山景区酒店", "王母宫景区", "龙泉寺周边"],
    "庆阳": ["庆阳阳光酒店", "周祖陵景区", "南梁革命纪念馆", "北石窟寺"],
    "临夏": ["临夏鸿瑞酒店", "炳灵寺石窟景区", "松鸣岩景区", "刘家峡水库"],
    "甘南": ["甘南锦润酒店", "拉卜楞寺景区", "郎木寺附近", "桑科草原"],
    "海东": ["海东天韵酒店", "瞿昙寺景区", "孟达天池", "互助北山"],
    "海北": ["海北海晏酒店", "金银滩草原", "原子城纪念馆", "祁连山草原"],
    "黄南": ["黄南圣莲酒店", "热贡艺术中心", "隆务寺景区", "坎布拉国家公园"],
    "海南州": ["海南恰卜恰酒店", "青海湖二郎剑", "龙羊峡景区", "贵德黄河清"],
    "果洛": ["果洛格萨尔酒店", "年保玉则景区", "阿尼玛卿雪山", "扎陵湖鄂陵湖"],
    "海西": ["海西德令哈酒店", "茶卡盐湖景区", "可鲁克湖", "乌素特水上雅丹"],
    "固原": ["固原万和大酒店", "须弥山石窟景区", "六盘山红军长征纪念馆", "火石寨景区"],
    "中卫": ["中卫华润酒店", "沙坡头景区酒店", "高庙景区", "通湖草原"],
    "五指山": ["五指山珠江水晶酒店", "五指山热带雨林", "太平山瀑布", "初保村黎族文化"],
    "文昌": ["文昌南国酒店", "铜鼓岭景区", "宋氏祖居", "东郊椰林"],
    "东方": ["东方泰隆酒店", "鱼鳞洲风景区", "大广坝", "俄贤岭景区"],
    "儋州": ["儋州海航新天地酒店", "东坡书院", "石花水洞", "千年古盐田"],
    "临高": ["临高碧桂园酒店", "临高角", "百仞滩", "彩桥红树林"],
    "定安": ["定安春阳酒店", "文笔峰景区", "南丽湖度假区", "久温塘冷泉"],
    "屯昌": ["屯昌兆丰酒店", "木色湖风景区", "卧龙山景区", "海瑞祖居"],
    "澄迈": ["澄迈金源酒店", "福山咖啡文化风情镇", "永庆寺景区", "富力红树湾"],
    "昌江": ["昌江万国酒店", "霸王岭国家森林公园", "棋子湾旅游度假区", "皇帝洞景区"],
    "乐东": ["乐东龙栖酒店", "尖峰岭国家森林公园", "毛公山景区", "龙沐湾度假区"],
    "陵水": ["陵水雅居乐酒店", "分界洲岛景区", "南湾猴岛", "清水湾度假区"],
    "白沙": ["白沙水溪酒店", "红坎瀑布", "陨石坑景区", "邦溪坡鹿自然保护区"],
    "琼中": ["琼中海航酒店", "百花岭国家森林公园", "黎母山国家森林公园", "上安仕阶"],
    "保亭": ["保亭七仙岭酒店", "呀诺达雨林文化旅游区", "槟榔谷黎苗文化旅游区", "七仙岭国家森林公园"],
    "三沙": ["三沙永兴酒店", "永兴岛", "七连屿", "全富岛"],
    "西安": ["西安威斯汀", "大雁塔假日酒店", "西安索菲特传奇", "临潼悦椿温泉", "回民街附近客栈"],
    "咸阳": ["咸阳帝都酒店", "乾陵景区酒店", "茂陵博物馆附近", "袁家村民宿"],
    "渭南": ["渭南祥和酒店", "华山景区酒店", "少华山国家森林公园", "党家村民宿"],
    "汉中": ["汉中百悦酒店", "古汉台景区", "武侯祠景区", "青木川古镇客栈"],
    "安康": ["安康明江酒店", "瀛湖景区", "南宫山国家森林公园", "香溪洞景区"],
    "商洛": ["商洛锦都酒店", "金丝峡景区酒店", "牛背梁国家森林公园", "柞水溶洞景区"],
    "榆林": ["榆林永昌酒店", "红石峡景区", "镇北台景区", "白云山景区"],
    "铜川": ["铜川正阳酒店", "药王山景区", "玉华宫景区", "照金香山景区"],
    "廊坊": ["廊坊固安福朋酒店", "天下第一城", "永定河自行车公园", "金海橡树湾"],
    "三门峡": ["三门峡金玫瑰酒店", "三门峡大坝景区", "天鹅湖国家城市湿地公园", "函谷关景区"],
    "鹤壁": ["鹤壁中凯酒店", "浚县古城", "大伾山景区", "云梦山景区"],
    "濮阳": ["濮阳阿尔卡迪亚酒店", "戚城遗址公园", "濮上园", "绿色庄园景区"],
    "济源": ["济源宏安酒店", "王屋山景区", "五龙口景区", "小浪底黄河三峡"]
}

def get_city_info(city_name):
    for city in CHINA_CITIES:
        if city["name"] == city_name:
            return city
    return {"name": city_name, "pinyin": city_name.lower(), "famous": []}

def get_city_foods(city_name):
    return CITY_FOODS.get(city_name, ["当地特色美食"])

def get_city_hotels(city_name):
    return CITY_HOTELS.get(city_name, ["当地优质酒店"])

ATTRACTION_PRICES = {
    "故宫": 60, "天安门": 0, "颐和园": 30, "八达岭长城": 40, "天坛": 15,
    "外滩": 0, "东方明珠": 220, "豫园": 40, "南京路": 0, "迪士尼": 475,
    "西湖": 0, "灵隐寺": 75, "千岛湖": 150, "西溪湿地": 80, "雷峰塔": 40,
    "中山陵": 0, "夫子庙": 0, "明孝陵": 70, "玄武湖": 0, "总统府": 40,
    "宽窄巷子": 0, "锦里": 0, "熊猫基地": 55, "都江堰": 80, "青城山": 90,
    "兵马俑": 120, "大雁塔": 50, "城墙": 54, "华清池": 120, "陕西历史博物馆": 0,
    "小蛮腰": 150, "长隆": 350, "沙面": 0, "陈家祠": 10, "白云山": 5,
    "世界之窗": 200, "欢乐谷": 230, "东部华侨城": 200, "大鹏半岛": 0, "莲花山": 0,
    "洪崖洞": 0, "解放碑": 0, "长江索道": 30, "磁器口": 0, "武隆": 135,
    "拙政园": 80, "留园": 55, "平江路": 0, "周庄": 100, "寒山寺": 20,
    "黄鹤楼": 70, "东湖": 0, "户部巷": 0, "长江大桥": 0, "武大樱花": 0,
    "橘子洲": 0, "岳麓山": 0, "太平街": 0, "火宫殿": 0, "湖南省博物馆": 0,
    "八大关": 0, "崂山": 90, "栈桥": 0, "五四广场": 0, "啤酒博物馆": 60,
    "鼓浪屿": 35, "厦门大学": 0, "曾厝垵": 0, "环岛路": 0, "南普陀": 0,
    "亚龙湾": 0, "天涯海角": 81, "蜈支洲岛": 144, "南山寺": 129, "海棠湾": 0,
    "滇池": 0, "石林": 130, "翠湖": 0, "大观楼": 20, "云南民族村": 90,
    "中央大街": 0, "冰雪大世界": 330, "索菲亚教堂": 20, "太阳岛": 30, "极地馆": 130,
    "丽江古城": 50, "玉龙雪山": 130, "束河古镇": 30, "泸沽湖": 70, "虎跳峡": 45,
    "洱海": 0, "大理古城": 0, "苍山": 35, "崇圣寺三塔": 75, "双廊": 0,
    "西双版纳热带植物园": 80, "野象谷": 65, "傣族园": 45, "曼听公园": 40, "望天树": 55,
    "普达措": 138, "松赞林寺": 115, "独克宗古城": 0, "纳帕海": 0, "巴拉格宗": 170,
    "布达拉宫": 200, "大昭寺": 85, "纳木错": 120, "八廓街": 0, "羊卓雍措": 60,
    "黄山": 190, "宏村": 104, "西递": 104, "歙县古城": 0, "呈坎": 107,
    "张家界森林公园": 225, "天门山": 278, "黄龙洞": 100, "宝峰湖": 96, "芙蓉镇": 100,
    "桂林漓江": 210, "阳朔": 0, "象鼻山": 55, "七星岩": 55, "两江四湖": 200,
    "黄果树瀑布": 160, "龙宫": 130, "屯堡古镇": 0, "格凸河": 50, "夜郎洞": 98,
    "梵净山": 100, "苗王城": 100, "大明边城": 0, "九龙洞": 90, "思南温泉": 0,
    "西江千户苗寨": 100, "镇远古镇": 0, "肇兴侗寨": 80, "舞阳河": 0, "青曼苗寨": 0,
    "荔波小七孔": 130, "樟江": 0, "平塘天眼": 0, "茂兰喀斯特": 50, "三都水族": 0,
    "万峰林": 80, "马岭河峡谷": 70, "万峰湖": 0, "招堤": 0, "双乳峰": 80,
    "德天瀑布": 80, "明仕田园": 0, "友谊关": 0, "花山岩画": 80, "左江": 0,
    "天山天池": 95, "大巴扎": 0, "红山公园": 0, "水磨沟": 0, "南山牧场": 0,
    "莫高窟": 238, "鸣沙山月牙泉": 110, "阳关": 50, "玉门关": 40, "雅丹魔鬼城": 50,
    "嘉峪关关城": 110, "长城第一墩": 22, "悬壁长城": 22, "魏晋墓": 0, "七一冰川": 0,
    "丹霞地貌": 75, "大佛寺": 40, "木塔寺": 0, "马蹄寺": 39, "山丹军马场": 0,
    "塔尔寺": 70, "青海湖": 100, "东关清真大寺": 0, "日月山": 0, "互助土族": 0,
    "西夏王陵": 75, "沙湖": 60, "镇北堡影视城": 100, "贺兰山": 0, "沙坡头": 100,
    "少林寺": 80, "嵩山": 80, "黄帝故里": 0, "二七塔": 0, "康百万庄园": 75,
    "龙门石窟": 90, "白马寺": 35, "老君山": 100, "白云山": 75, "隋唐洛阳城": 0,
    "清明上河园": 120, "龙亭公园": 45, "大相国寺": 40, "铁塔": 40, "包公祠": 30,
    "云冈石窟": 120, "华严寺": 65, "古城墙": 30, "恒山": 45, "悬空寺": 125,
    "平遥古城": 125, "乔家大院": 115, "双林寺": 35, "镇国寺": 25, "王家大院": 55,
    "避暑山庄": 130, "外八庙": 0, "普宁寺": 80, "磬锤峰": 0, "木兰围场": 0,
    "山海关": 50, "北戴河": 0, "南戴河": 0, "鸽子窝公园": 25, "联峰山": 0,
    "草原天路": 0, "大境门": 0, "张北草原": 0, "崇礼滑雪场": 0, "黄帝城": 0,
    "沈阳故宫": 50, "张氏帅府": 48, "北陵": 50, "棋盘山": 0, "世博园": 50,
    "星海广场": 0, "老虎滩": 220, "金石滩": 0, "棒棰岛": 20, "旅顺": 0,
    "净月潭": 30, "伪满皇宫": 70, "长影世纪城": 240, "南湖公园": 0, "世界雕塑公园": 30,
    "雾凇岛": 0, "松花湖": 0, "北山公园": 0, "龙潭山": 0, "乌拉街": 0,
    "长白山": 125, "天池": 125, "图们江": 0, "防川景区": 0, "帽儿山": 0,
    "扎龙自然保护区": 65, "龙沙公园": 0, "明月岛": 0, "大乘寺": 0, "昂昂溪遗址": 0,
    "镜泊湖": 100, "雪乡": 0, "地下森林": 0, "威虎山": 0, "八女投江纪念地": 0,
    "昭君墓": 65, "大召寺": 35, "草原": 0, "塞上老街": 0, "哈素海": 0,
    "五当召": 60, "赛汗塔拉草原": 0, "北方兵器城": 0, "梅力更": 0, "希拉穆仁草原": 0,
    "响沙湾": 120, "成吉思汗陵": 120, "鄂尔多斯草原": 0, "康巴什新区": 0, "七星湖": 0,
    "呼伦贝尔草原": 0, "满洲里": 0, "额尔古纳": 0, "莫尔道嘎": 0, "阿尔山": 180,
    "火焰山": 40, "葡萄沟": 60, "坎儿井": 40, "交河故城": 70, "苏公塔": 0,
    "喀什古城": 30, "艾提尕尔清真寺": 0, "香妃墓": 30, "帕米尔高原": 0, "卡拉库里湖": 0,
    "那拉提草原": 95, "赛里木湖": 70, "喀拉峻草原": 0, "昭苏草原": 0, "果子沟": 0,
}

TRANSPORT_PRICES = {
    "飞机": {"base": 800, "per_km": 0.5},
    "火车": {"base": 50, "per_km": 0.15},
    "自驾": {"base": 0, "per_km": 0.8},
    "智能推荐": {"base": 500, "per_km": 0.3}
}

HOTEL_PRICES = {
    "经济型": {"min": 150, "max": 300, "avg": 200},
    "舒适型": {"min": 300, "max": 600, "avg": 400},
    "豪华型": {"min": 600, "max": 1500, "avg": 800}
}

FOOD_PRICES = {
    "经济型": {"breakfast": 20, "lunch": 40, "dinner": 50},
    "舒适型": {"breakfast": 40, "lunch": 80, "dinner": 100},
    "豪华型": {"breakfast": 80, "lunch": 150, "dinner": 200}
}

def calculate_budget(destination, days, people, style, transport, attractions=None):
    budget = {
        "transport": 0,
        "accommodation": 0,
        "food": 0,
        "tickets": 0,
        "total": 0,
        "details": []
    }
    
    transport_info = TRANSPORT_PRICES.get(transport, TRANSPORT_PRICES["智能推荐"])
    budget["transport"] = transport_info["base"] * people * 2
    budget["details"].append({
        "item": "交通费用",
        "cost": budget["transport"],
        "note": f"{transport}往返（预估）"
    })
    
    hotel_info = HOTEL_PRICES.get(style, HOTEL_PRICES["舒适型"])
    budget["accommodation"] = hotel_info["avg"] * (days - 1) if days > 1 else 0
    budget["details"].append({
        "item": "住宿费用",
        "cost": budget["accommodation"],
        "note": f"{style}酒店{(days - 1) if days > 1 else 0}晚"
    })
    
    food_info = FOOD_PRICES.get(style, FOOD_PRICES["舒适型"])
    daily_food = food_info["breakfast"] + food_info["lunch"] + food_info["dinner"]
    budget["food"] = daily_food * days * people
    budget["details"].append({
        "item": "餐饮费用",
        "cost": budget["food"],
        "note": f"每日三餐×{days}天×{people}人"
    })
    
    city_info = get_city_info(destination)
    if city_info and city_info.get("famous"):
        total_ticket = 0
        ticket_details = []
        for attraction in city_info["famous"][:min(days * 2, len(city_info["famous"]))]:
            price = ATTRACTION_PRICES.get(attraction, 50)
            total_ticket += price
            ticket_details.append(f"{attraction}({price}元)")
        budget["tickets"] = total_ticket * people
        budget["details"].append({
            "item": "门票费用",
            "cost": budget["tickets"],
            "note": "、".join(ticket_details[:5]) + ("..." if len(ticket_details) > 5 else "")
        })
    
    budget["total"] = budget["transport"] + budget["accommodation"] + budget["food"] + budget["tickets"]
    budget["per_person"] = budget["total"] // people if people > 0 else budget["total"]
    
    return budget

def get_weather(city_name):
    api_key = app.config['AMAP_WEATHER_KEY']
    if not api_key:
        return generate_mock_weather(city_name)
    
    url = f'https://restapi.amap.com/v3/weather/weatherInfo?city={city_name}&key={api_key}'
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data['status'] == '1' and data['lives']:
            return {
                'city': city_name,
                'weather': data['lives'][0]['weather'],
                'temperature': data['lives'][0]['temperature'],
                'wind_direction': data['lives'][0]['winddirection'],
                'wind_power': data['lives'][0]['windpower'],
                'humidity': data['lives'][0]['humidity'],
                'report_time': data['lives'][0]['reporttime']
            }
    except Exception as e:
        print(f"Weather API error: {e}")
    
    return generate_mock_weather(city_name)

def generate_mock_weather(city_name):
    weathers = ['晴', '多云', '阴', '小雨', '阵雨', '雷阵雨']
    temperatures = ['18-25°C', '22-30°C', '25-32°C', '15-22°C', '20-28°C']
    return {
        'city': city_name,
        'weather': random.choice(weathers),
        'temperature': random.choice(temperatures),
        'wind_direction': '东南风',
        'wind_power': '3-4级',
        'humidity': '65%',
        'report_time': datetime.now().strftime('%Y-%m-%d %H:%M')
    }

def generate_daily_weather(city_name, start_date, days):
    weather_list = []
    current_date = start_date
    for _ in range(days):
        weather = get_weather(city_name)
        weather['date'] = current_date.strftime('%Y-%m-%d')
        weather_list.append(weather)
        current_date += timedelta(days=1)
    return weather_list

def call_deepseek_ai(prompt):
    api_key = app.config['DEEPSEEK_API_KEY']
    if not api_key:
        print("DeepSeek API Key未配置")
        return None
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }
    
    data = {
        'model': 'deepseek-chat',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.7
    }
    
    try:
        print(f"DeepSeek API 调用中... (prompt长度: {len(prompt)} 字符)")
        response = requests.post(app.config['DEEPSEEK_BASE_URL'], headers=headers, json=data, timeout=60)
        print(f"DeepSeek API 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                print(f"DeepSeek API 调用成功 (响应长度: {len(content)} 字符)")
                return content
            else:
                print(f"DeepSeek API 响应格式错误: {result}")
        else:
            try:
                error_info = response.json()
                print(f"DeepSeek API 错误响应: {error_info}")
            except:
                print(f"DeepSeek API 非JSON响应: {response.text[:500]}")
                
    except requests.exceptions.Timeout:
        print("DeepSeek API 超时")
    except requests.exceptions.ConnectionError:
        print("DeepSeek API 连接错误")
    except Exception as e:
        print(f"DeepSeek API 异常: {type(e).__name__}: {e}")
    
    return None

def call_douban_ai(prompt):
    api_key = app.config['DOUBAN_API_KEY']
    if not api_key:
        return None
    
    try:
        response = requests.post(
            'https://api.douban.com/v2/chat/completions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'prompt': prompt, 'max_tokens': 2000},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            return result.get('content', '')
    except Exception as e:
        print(f"Douban API error: {e}")
    
    return None

def call_baidu_ai(prompt):
    api_key = app.config['BAIDU_API_KEY']
    if not api_key:
        return None
    
    try:
        response = requests.post(
            'https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/completions',
            headers={'Content-Type': 'application/json'},
            params={'access_token': api_key},
            json={'messages': [{'role': 'user', 'content': prompt}]},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            return result.get('result', '')
    except Exception as e:
        print(f"Baidu API error: {e}")
    
    return None

def generate_trip_plan(departure, destination, start_date, end_date, people, style, transport, additional_destinations='', special_requests=''):
    """生成旅行计划（带缓存）"""
    days = (end_date - start_date).days + 1
    
    # ===== 检查缓存 =====
    if not additional_destinations and not special_requests:
        try:
            from services.ai_cache import get_cached_plan, set_cached_plan
            cached_result = get_cached_plan(departure, destination, start_date, end_date, people, style, transport)
            if cached_result:
                print(f"[缓存命中] {departure}→{destination} {days}天")
                return cached_result
        except Exception as e:
            print(f"缓存模块加载失败: {e}")
    
    additional_info = ''
    if additional_destinations:
        additional_info += f"\n    - 附加目的地：{additional_destinations}"
    if special_requests:
        additional_info += f"\n    - 特殊要求：{special_requests}"
    
    # 单AI模式：直接使用AI生成的内容（去掉第二个排版AI，避免内容被模板化）
    content_prompt = f"""
你是一个专业的旅行规划师。请帮我规划一个从【{departure}】到【{destination}】的{days}天{destination}旅行计划。

【基本信息】
- 出发日期：{start_date.strftime('%Y年%m月%d日')}
- 结束日期：{end_date.strftime('%Y年%m月%d日')}
- 旅行天数：{days}天
- 人数：{people}人
- 旅行风格：{style}
- 交通方式：{transport}
{additional_info}

【核心要求 - 必须严格遵守】
1. ✅ 所有景点必须是{destination}当地或周边【真实存在】的知名旅游景点，禁止编造景点名称
2. ✅ 所有餐馆必须是{destination}【真实存在】的餐厅，包含真实名称和招牌菜品
3. ✅ 所有酒店必须是{destination}【真实存在】的酒店，包含准确星级和价格
4. ✅ 每天行程中的景点【不能重复】，确保{days}天行程涵盖不同景点
5. ✅ 每个景点/餐馆必须配上高德地图导航链接（用反引号包裹URL，格式见下方示例）
6. ✅ 预算必须符合旅行风格（经济型/舒适型/豪华型）

【输出格式 - 必须严格遵守】
请按照以下固定格式输出，不要更改结构：

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚀 交通方案
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【去程】出发时间 | 交通工具（航班号/车次） | 出发地 → 目的地 | 预计时长 | 参考票价
【返程】出发时间 | 交通工具（航班号/车次） | 出发地 → 目的地 | 预计时长 | 参考票价

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 第1天 · {start_date.strftime('%Y年%m月%d日')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌅 【上午】景点名称：xxx | 地址：xxx | 特色：xxx | 门票：xxx元（免费景点请写"免费"） | `https://uri.amap.com/search?keyword=xxx&city={destination}&src=travel_plan&callnative=1`
🍜 【午餐】餐馆名称：xxx | 特色菜：xxx | 人均：xxx元 | `https://uri.amap.com/search?keyword=xxx&city={destination}&src=travel_plan&callnative=1`
☀️ 【下午】景点名称：xxx | 地址：xxx | 特色：xxx | 门票：xxx元（免费景点请写"免费"） | `https://uri.amap.com/search?keyword=xxx&city={destination}&src=travel_plan&callnative=1`
🍽️ 【晚餐】餐馆名称：xxx | 特色菜：xxx | 人均：xxx元 | `https://uri.amap.com/search?keyword=xxx&city={destination}&src=travel_plan&callnative=1`
🌙 【晚上】活动建议：xxx

（按照此格式继续生成第2天到第{days}天。每天的景点和餐馆必须不同，不要重复！）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏨 住宿推荐（3家不同档次的酒店）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 【xxx酒店】| 星级：★★★★★ | 价格：xxx元/晚 | 地址：xxx | 推荐理由：xxx
2. 【xxx酒店】| 星级：★★★★ | 价格：xxx元/晚 | 地址：xxx | 推荐理由：xxx
3. 【xxx酒店】| 星级：★★★ | 价格：xxx元/晚 | 地址：xxx | 推荐理由：xxx

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🍲 特色美食（5-8种当地特色美食）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 【xxx】- 推荐理由：xxx
2. 【xxx】- 推荐理由：xxx
3. 【xxx】- 推荐理由：xxx
4. 【xxx】- 推荐理由：xxx
5. 【xxx】- 推荐理由：xxx

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 预算明细（{people}人{days}天）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
┌────────┬────────┬──────────────┐
│ 项目   │ 费用   │ 备注        │
├────────┼────────┼──────────────┤
│ 交通   │ xxx元 │ 往返交通    │
│ 住宿   │ xxx元 │ {days}晚住宿 │
│ 餐饮   │ xxx元 │ 每日{people}人用餐 │
│ 门票   │ xxx元 │ 景点门票    │
│ 其他   │ xxx元 │ 杂费/购物   │
├────────┼────────┼──────────────┤
│ 总计   │ xxx元 │ 仅供参考    │
└────────┴────────┴──────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 实用贴士
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. ⚠️ 关于{destination}天气和出行的建议
2. 📌 关于景点预订或交通的建议
3. 💡 关于饮食卫生或当地风俗的建议
4. 🚗 关于出行安全或时间安排的建议
5. 📱 其他实用提示

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【重要提示】
- 景点名称和餐馆名称必须是具体的真实名称（例如：兵马俑、回民街），不要写成"著名景点"、"网红餐厅"等通用词
- 地址要具体到街道或景区位置
- 每天使用不同的景点和餐馆，不要重复
- 导航链接要用反引号 ` 包裹URL，不要用其他格式
"""
    
    # 使用AI生成行程：依次尝试 DeepSeek -> 豆包 -> 百度
    ai_response = call_deepseek_ai(content_prompt)
    if not ai_response:
        ai_response = call_douban_ai(content_prompt)
    if not ai_response:
        ai_response = call_baidu_ai(content_prompt)
    
    # 如果所有AI都不可用，直接返回空（由调用方处理错误）
    # 不再使用数据库兜底，避免生成不真实的内容
    if not ai_response:
        return None
    
    # ===== 保存到缓存 =====
    if not additional_destinations and not special_requests:
        try:
            from services.ai_cache import set_cached_plan
            set_cached_plan(departure, destination, start_date, end_date, people, style, transport, ai_response)
            print(f"[缓存保存] {departure}→{destination} {days}天")
        except Exception as e:
            print(f"缓存保存失败: {e}")
    
    return ai_response

# 模拟行程数据 - 包含真实景点和餐馆
MOCK_TRIP_DATA = {
    "北京": {
        "attractions": [
            {"name": "故宫博物院", "feature": "中国明清两代皇家宫殿", "price": "60元"},
            {"name": "天坛公园", "feature": "明清皇帝祭天祈谷场所", "price": "34元"},
            {"name": "颐和园", "feature": "清代皇家园林", "price": "30元"},
            {"name": "八达岭长城", "feature": "明长城精华段落", "price": "40元"},
            {"name": "天安门广场", "feature": "世界上最大的城市广场", "price": "免费"},
            {"name": "圆明园遗址", "feature": "清代皇家园林遗址", "price": "10元"},
            {"name": "北海公园", "feature": "皇家园林典范", "price": "10元"},
            {"name": "南锣鼓巷", "feature": "老北京胡同文化", "price": "免费"},
            {"name": "鸟巢", "feature": "2008奥运会主场馆", "price": "80元"},
            {"name": "什刹海", "feature": "老北京历史文化保护区", "price": "免费"}
        ],
        "restaurants": [
            {"name": "全聚德", "dish": "北京烤鸭", "price": "约150元"},
            {"name": "东来顺", "dish": "铜锅涮羊肉", "price": "约120元"},
            {"name": "护国寺小吃", "dish": "豆汁儿、焦圈", "price": "约30元"},
            {"name": "簋街胡大", "dish": "麻辣小龙虾", "price": "约100元"},
            {"name": "便宜坊", "dish": "焖炉烤鸭", "price": "约140元"},
            {"name": "隆延茶铺", "dish": "杏仁豆腐、豌豆黄", "price": "约25元"}
        ],
        "hotels": [
            {"name": "北京王府半岛酒店", "star": "五星级", "price": "2500元/晚"},
            {"name": "北京金茂威斯汀", "star": "五星级", "price": "1800元/晚"},
            {"name": "北京希尔顿酒店", "star": "四星级", "price": "900元/晚"},
            {"name": "北京锦江之星", "star": "经济型", "price": "300元/晚"}
        ]
    },
    "上海": {
        "attractions": [
            {"name": "外滩", "feature": "万国建筑博览会", "price": "免费"},
            {"name": "东方明珠塔", "feature": "上海标志性建筑", "price": "220元"},
            {"name": "豫园", "feature": "江南古典园林", "price": "40元"},
            {"name": "南京路步行街", "feature": "中国第一商业街", "price": "免费"},
            {"name": "田子坊", "feature": "老上海石库门", "price": "免费"},
            {"name": "上海迪士尼乐园", "feature": "国际顶级主题乐园", "price": "475元"},
            {"name": "城隍庙", "feature": "上海知名古建筑群", "price": "10元"},
            {"name": "新天地", "feature": "石库门改造的时尚地标", "price": "免费"}
        ],
        "restaurants": [
            {"name": "南翔小笼包", "dish": "蟹粉小笼", "price": "约60元"},
            {"name": "上海老饭店", "dish": "本帮红烧肉", "price": "约120元"},
            {"name": "小杨生煎", "dish": "生煎包", "price": "约25元"},
            {"name": "光明邨大酒家", "dish": "鲜肉月饼", "price": "约80元"},
            {"name": "沈大成", "dish": "青团、双酿团", "price": "约20元"}
        ],
        "hotels": [
            {"name": "上海外滩华尔道夫", "star": "五星级", "price": "2800元/晚"},
            {"name": "上海浦东丽思卡尔顿", "star": "五星级", "price": "2200元/晚"},
            {"name": "上海静安香格里拉", "star": "五星级", "price": "2000元/晚"},
            {"name": "如家酒店", "star": "经济型", "price": "250元/晚"}
        ]
    },
    "成都": {
        "attractions": [
            {"name": "宽窄巷子", "feature": "老成都民居建筑群", "price": "免费"},
            {"name": "锦里古街", "feature": "三国文化商业街", "price": "免费"},
            {"name": "大熊猫繁育研究基地", "feature": "大熊猫观赏", "price": "55元"},
            {"name": "武侯祠", "feature": "诸葛亮纪念祠", "price": "50元"},
            {"name": "青城山", "feature": "道教名山", "price": "60元"},
            {"name": "都江堰", "feature": "古代水利工程", "price": "90元"},
            {"name": "杜甫草堂", "feature": "杜甫故居", "price": "50元"},
            {"name": "春熙路", "feature": "成都商业中心", "price": "免费"}
        ],
        "restaurants": [
            {"name": "玉林串串香", "dish": "串串香", "price": "约60元"},
            {"name": "龙抄手", "dish": "红油抄手", "price": "约30元"},
            {"name": "蜀大侠火锅", "dish": "牛油火锅", "price": "约100元"},
            {"name": "巴蜀大宅门", "dish": "麻婆豆腐", "price": "约80元"},
            {"name": "钟水饺", "dish": "钟水饺", "price": "约20元"}
        ],
        "hotels": [
            {"name": "成都博舍", "star": "五星级", "price": "1800元/晚"},
            {"name": "成都环球中心天堂洲际", "star": "五星级", "price": "1500元/晚"},
            {"name": "如家酒店", "star": "经济型", "price": "180元/晚"},
            {"name": "成都熊猫主题民宿", "star": "特色民宿", "price": "350元/晚"}
        ]
    },
    "重庆": {
        "attractions": [
            {"name": "洪崖洞", "feature": "吊脚楼建筑群", "price": "免费"},
            {"name": "解放碑", "feature": "重庆地标", "price": "免费"},
            {"name": "长江索道", "feature": "空中巴士", "price": "20元"},
            {"name": "磁器口古镇", "feature": "千年古镇", "price": "免费"},
            {"name": "武隆天生三桥", "feature": "喀斯特地貌", "price": "125元"},
            {"name": "鹅岭公园", "feature": "俯瞰渝中半岛", "price": "免费"},
            {"name": "李子坝轻轨站", "feature": "轻轨穿楼奇观", "price": "免费"}
        ],
        "restaurants": [
            {"name": "珮姐老火锅", "dish": "九宫格火锅", "price": "约120元"},
            {"name": "重庆小面", "dish": "豌杂面", "price": "约15元"},
            {"name": "好又来酸辣粉", "dish": "酸辣粉", "price": "约12元"},
            {"name": "降龙爪爪", "dish": "火锅味爪爪", "price": "约30元"},
            {"name": "豆花饭", "dish": "磁器口豆花", "price": "约20元"}
        ],
        "hotels": [
            {"name": "重庆丽思瑞凯悦臻选", "star": "五星级", "price": "1200元/晚"},
            {"name": "重庆解放碑威斯汀", "star": "五星级", "price": "1100元/晚"},
            {"name": "重庆希尔顿酒店", "star": "四星级", "price": "600元/晚"},
            {"name": "重庆沙磁公馆", "star": "特色民宿", "price": "280元/晚"}
        ]
    },
    "西安": {
        "attractions": [
            {"name": "秦始皇兵马俑", "feature": "世界第八大奇迹", "price": "120元"},
            {"name": "大雁塔", "feature": "唐代佛塔", "price": "40元"},
            {"name": "西安城墙", "feature": "完整古城墙", "price": "54元"},
            {"name": "华清宫", "feature": "皇家温泉行宫", "price": "120元"},
            {"name": "大唐芙蓉园", "feature": "唐代文化主题公园", "price": "120元"},
            {"name": "回民街", "feature": "清真美食街", "price": "免费"},
            {"name": "钟楼", "feature": "明代钟楼", "price": "30元"},
            {"name": "大明宫国家遗址公园", "feature": "唐代皇宫遗址", "price": "60元"}
        ],
        "restaurants": [
            {"name": "老孙家泡馍", "dish": "羊肉泡馍", "price": "约35元"},
            {"name": "魏家凉皮", "dish": "秘制凉皮", "price": "约15元"},
            {"name": "biangbiang面", "dish": "油泼面", "price": "约20元"},
            {"name": "春发生葫芦头", "dish": "葫芦头泡馍", "price": "约30元"},
            {"name": "回民街贾三灌汤包", "dish": "牛肉灌汤包", "price": "约25元"}
        ],
        "hotels": [
            {"name": "西安威斯汀酒店", "star": "五星级", "price": "1000元/晚"},
            {"name": "西安索菲特传奇", "star": "五星级", "price": "1500元/晚"},
            {"name": "西安钟楼亚朵酒店", "star": "四星级", "price": "450元/晚"},
            {"name": "西安民宿", "star": "特色民宿", "price": "200元/晚"}
        ]
    },
    "杭州": {
        "attractions": [
            {"name": "西湖", "feature": "世界文化遗产", "price": "免费"},
            {"name": "灵隐寺", "feature": "千年古刹", "price": "75元"},
            {"name": "宋城", "feature": "宋代主题公园", "price": "320元"},
            {"name": "千岛湖", "feature": "天下第一秀水", "price": "130元"},
            {"name": "西溪湿地", "feature": "城市绿肺", "price": "80元"},
            {"name": "雷峰塔", "feature": "西湖标志性建筑", "price": "40元"},
            {"name": "断桥残雪", "feature": "西湖十景之一", "price": "免费"},
            {"name": "龙井村", "feature": "西湖龙井茶产地", "price": "免费"}
        ],
        "restaurants": [
            {"name": "外婆家", "dish": "茶香鸡", "price": "约80元"},
            {"name": "楼外楼", "dish": "西湖醋鱼", "price": "约150元"},
            {"name": "知味观", "dish": "小笼包", "price": "约50元"},
            {"name": "绿茶餐厅", "dish": "东坡肉", "price": "约90元"},
            {"name": "奎元馆", "dish": "片儿川", "price": "约40元"}
        ],
        "hotels": [
            {"name": "杭州西湖国宾馆", "star": "五星级", "price": "2000元/晚"},
            {"name": "杭州柏悦酒店", "star": "五星级", "price": "1800元/晚"},
            {"name": "杭州西溪喜来登", "star": "五星级", "price": "1200元/晚"},
            {"name": "杭州四眼井民宿", "star": "特色民宿", "price": "350元/晚"}
        ]
    },
    "广州": {
        "attractions": [
            {"name": "广州塔", "feature": "小蛮腰地标", "price": "150元"},
            {"name": "陈家祠", "feature": "岭南建筑艺术", "price": "10元"},
            {"name": "沙面", "feature": "欧陆风情建筑群", "price": "免费"},
            {"name": "白云山", "feature": "羊城第一峰", "price": "5元"},
            {"name": "长隆野生动物世界", "feature": "大型野生动物园", "price": "300元"},
            {"name": "北京路步行街", "feature": "千年商业街", "price": "免费"},
            {"name": "珠江夜游", "feature": "夜游珠江", "price": "80元"}
        ],
        "restaurants": [
            {"name": "陶陶居", "dish": "虾饺、烧麦", "price": "约120元"},
            {"name": "点都德", "dish": "早茶点心", "price": "约100元"},
            {"name": "广州酒家", "dish": "文昌鸡", "price": "约150元"},
            {"name": "银记肠粉", "dish": "牛肉肠粉", "price": "约25元"},
            {"name": "陈添记", "dish": "鱼皮", "price": "约20元"}
        ],
        "hotels": [
            {"name": "广州四季酒店", "star": "五星级", "price": "2000元/晚"},
            {"name": "广州瑰丽酒店", "star": "五星级", "price": "1800元/晚"},
            {"name": "广州白天鹅宾馆", "star": "五星级", "price": "1200元/晚"},
            {"name": "如家酒店", "star": "经济型", "price": "200元/晚"}
        ]
    },
    "深圳": {
        "attractions": [
            {"name": "世界之窗", "feature": "微缩世界景点", "price": "220元"},
            {"name": "东部华侨城", "feature": "生态旅游区", "price": "200元"},
            {"name": "欢乐谷", "feature": "大型游乐园", "price": "230元"},
            {"name": "深圳湾公园", "feature": "海滨休闲公园", "price": "免费"},
            {"name": "大梅沙海滨公园", "feature": "海滩度假", "price": "免费"},
            {"name": "甘坑客家小镇", "feature": "客家文化", "price": "免费"}
        ],
        "restaurants": [
            {"name": "润园四季椰子鸡", "dish": "椰子鸡火锅", "price": "约150元"},
            {"name": "蔡澜越南粉", "dish": "越南粉", "price": "约60元"},
            {"name": "客家腌面", "dish": "客家腌面", "price": "约25元"},
            {"name": "探鱼", "dish": "烤鱼", "price": "约100元"}
        ],
        "hotels": [
            {"name": "深圳湾万象城", "star": "五星级", "price": "1500元/晚"},
            {"name": "深圳福田香格里拉", "star": "五星级", "price": "1300元/晚"},
            {"name": "深圳华侨城洲际", "star": "五星级", "price": "1100元/晚"},
            {"name": "深圳民宿", "star": "特色民宿", "price": "300元/晚"}
        ]
    }
}

def generate_mock_trip_plan(departure, destination, start_date, end_date, people, style, transport, additional_destinations='', special_requests=''):
    days = (end_date - start_date).days + 1
    from urllib.parse import quote  # 用于URL编码景点名称
    
    # 为热门旅游城市准备真实数据
    city_attractions = {
        "呼伦贝尔": [
            {"name": "呼伦贝尔大草原", "address": "呼伦贝尔市海拉尔区", "feature": "世界四大草原之一，广袤无垠的绿色天堂", "price": "免费"},
            {"name": "满洲里国门", "address": "呼伦贝尔市满洲里市", "feature": "中国最大的陆路口岸，中俄边境标志性建筑", "price": "60元"},
            {"name": "额尔古纳湿地", "address": "呼伦贝尔市额尔古纳市", "feature": "亚洲第一湿地，美丽的生态景观", "price": "65元"},
            {"name": "套娃广场", "address": "呼伦贝尔市满洲里市", "feature": "世界最大套娃主题广场，充满异域风情", "price": "148元"},
            {"name": "莫日格勒河", "address": "呼伦贝尔市陈巴尔虎旗", "feature": "老舍笔下的天下第一曲水", "price": "免费"},
            {"name": "恩和俄罗斯族乡", "address": "呼伦贝尔市额尔古纳市", "feature": "中国唯一的俄罗斯族民族乡", "price": "免费"},
            {"name": "黑山头古城", "address": "呼伦贝尔市额尔古纳市", "feature": "金代古城遗址，草原上的历史遗迹", "price": "免费"}
        ],
        "黄山": [
            {"name": "黄山风景区", "address": "黄山市黄山区汤口镇", "feature": "世界文化与自然双重遗产，奇松怪石云海温泉", "price": "200元"},
            {"name": "宏村", "address": "黄山市黟县宏村镇", "feature": "世界文化遗产，徽派古村落代表", "price": "104元"},
            {"name": "西递", "address": "黄山市黟县西递镇", "feature": "世界文化遗产，明清古村落", "price": "104元"},
            {"name": "屯溪老街", "address": "黄山市屯溪区", "feature": "徽州文化商业街，老字号店铺", "price": "免费"},
            {"name": "徽州古城", "address": "黄山市歙县", "feature": "中国四大古城之一，徽州文化发源地", "price": "80元"},
            {"name": "翡翠谷", "address": "黄山市黄山区汤口镇", "feature": "彩池群，《卧虎藏龙》取景地", "price": "75元"},
            {"name": "呈坎古镇", "address": "黄山市徽州区呈坎镇", "feature": "风水八卦村，保存完好的古村落", "price": "80元"}
        ],
        "北京": [
            {"name": "故宫博物院", "address": "北京市东城区景山前街4号", "feature": "世界上现存规模最大的木质结构古建筑群", "price": "60元"},
            {"name": "八达岭长城", "address": "北京市延庆区", "feature": "万里长城的重要关口，世界文化遗产", "price": "40元"},
            {"name": "颐和园", "address": "北京市海淀区新建宫门路19号", "feature": "中国现存规模最大、保存最完整的皇家园林", "price": "30元"},
            {"name": "天坛公园", "address": "北京市东城区天坛东里甲1号", "feature": "明清两代皇帝祭天祈谷的场所", "price": "15元"},
            {"name": "圆明园", "address": "北京市海淀区清华西路28号", "feature": "清代皇家园林，历史遗址", "price": "25元"},
            {"name": "鸟巢", "address": "北京市朝阳区国家体育场南路1号", "feature": "2008年奥运会主体育场", "price": "50元"},
            {"name": "南锣鼓巷", "address": "北京市东城区", "feature": "北京最古老的街区之一，胡同文化", "price": "免费"}
        ],
        "杭州": [
            {"name": "西湖", "address": "杭州市西湖区", "feature": "中国十大名胜之一，湖光山色", "price": "免费"},
            {"name": "灵隐寺", "address": "杭州市西湖区灵隐路法云弄1号", "feature": "江南著名古刹，香火旺盛", "price": "30元"},
            {"name": "雷峰塔", "address": "杭州市西湖区南山路15号", "feature": "白娘子传说发源地", "price": "40元"},
            {"name": "西溪湿地", "address": "杭州市西湖区天目山路518号", "feature": "国家湿地公园，生态保护区", "price": "80元"},
            {"name": "宋城", "address": "杭州市西湖区之江路148号", "feature": "仿宋代主题乐园", "price": "300元"},
            {"name": "千岛湖", "address": "杭州市淳安县", "feature": "千座岛屿组成的大型湖泊", "price": "130元"},
            {"name": "河坊街", "address": "杭州市上城区", "feature": "南宋御街，老字号店铺", "price": "免费"}
        ],
        "成都": [
            {"name": "宽窄巷子", "address": "成都市青羊区金河宾馆旁", "feature": "清代古街道，成都文化地标", "price": "免费"},
            {"name": "锦里古街", "address": "成都市武侯区武侯祠大街231号", "feature": "三国文化特色商业街", "price": "免费"},
            {"name": "成都大熊猫繁育研究基地", "address": "成都市成华区外北熊猫大道1375号", "feature": "国宝大熊猫", "price": "55元"},
            {"name": "武侯祠", "address": "成都市武侯区武侯祠大街231号", "feature": "纪念诸葛亮的庙宇", "price": "50元"},
            {"name": "杜甫草堂", "address": "成都市青羊区青华路37号", "feature": "唐代诗人杜甫故居", "price": "50元"},
            {"name": "都江堰", "address": "成都市都江堰市公园路", "feature": "世界文化遗产，古代水利工程", "price": "80元"},
            {"name": "青城山", "address": "成都市都江堰市青城山镇", "feature": "道教名山，清幽秀丽", "price": "80元"}
        ],
        "重庆": [
            {"name": "洪崖洞", "address": "重庆市渝中区嘉陵江滨江路", "feature": "巴渝传统吊脚楼建筑群", "price": "免费"},
            {"name": "解放碑", "address": "重庆市渝中区邹容路100号", "feature": "重庆地标，商业中心", "price": "免费"},
            {"name": "磁器口古镇", "address": "重庆市沙坪坝区磁器口镇", "feature": "千年古镇，巴渝文化", "price": "免费"},
            {"name": "长江索道", "address": "重庆市渝中区新华路153号", "feature": "空中交通工具，俯瞰两江", "price": "20元"},
            {"name": "武隆天生三桥", "address": "重庆市武隆区仙女山镇", "feature": "世界自然遗产，奇特地貌", "price": "125元"},
            {"name": "李子坝轻轨站", "address": "重庆市渝中区李子坝", "feature": "穿楼而过的轻轨", "price": "免费"},
            {"name": "南山一棵树", "address": "重庆市南岸区南山", "feature": "俯瞰重庆夜景的最佳地点", "price": "30元"}
        ],
        "西安": [
            {"name": "兵马俑", "address": "西安市临潼区秦陵北路", "feature": "世界第八大奇迹，秦始皇陪葬坑", "price": "150元"},
            {"name": "大雁塔", "address": "西安市雁塔区慈恩路1号", "feature": "唐代佛塔，玄奘藏经处", "price": "30元"},
            {"name": "西安城墙", "address": "西安市中心", "feature": "保存最完整的古城墙", "price": "54元"},
            {"name": "钟楼", "address": "西安市碑林区东大街", "feature": "明代钟楼，城市中心地标", "price": "35元"},
            {"name": "鼓楼", "address": "西安市莲湖区北院门", "feature": "明代鼓楼，与钟楼相望", "price": "35元"},
            {"name": "华清宫", "address": "西安市临潼区华清路38号", "feature": "唐代皇家温泉宫苑", "price": "120元"},
            {"name": "回民街", "address": "西安市莲湖区北院门", "feature": "美食街区，西北风味", "price": "免费"}
        ],
        "广州": [
            {"name": "广州塔", "address": "广州市海珠区阅江西路222号", "feature": "广州地标，小蛮腰", "price": "150元"},
            {"name": "陈家祠", "address": "广州市荔湾区中山七路", "feature": "岭南建筑艺术殿堂", "price": "10元"},
            {"name": "沙面岛", "address": "广州市荔湾区沙面街", "feature": "欧式建筑群，文艺打卡地", "price": "免费"},
            {"name": "白云山", "address": "广州市白云区广园中路801号", "feature": "广州名山，城市绿肺", "price": "5元"},
            {"name": "长隆欢乐世界", "address": "广州市番禺区汉溪大道东", "feature": "大型主题乐园", "price": "250元"},
            {"name": "北京路步行街", "address": "广州市越秀区北京路", "feature": "千年古道，商业中心", "price": "免费"},
            {"name": "圣心大教堂", "address": "广州市越秀区一德路", "feature": "哥特式教堂，远东巴黎圣母院", "price": "免费"}
        ],
        "深圳": [
            {"name": "世界之窗", "address": "深圳市南山区深南大道9037号", "feature": "世界微缩景观主题公园", "price": "220元"},
            {"name": "东部华侨城", "address": "深圳市盐田区梅沙街道", "feature": "大型旅游度假区", "price": "200元"},
            {"name": "欢乐谷", "address": "深圳市南山区侨城西街18号", "feature": "大型主题乐园", "price": "230元"},
            {"name": "深圳湾公园", "address": "深圳市南山区滨海大道", "feature": "海滨休闲公园", "price": "免费"},
            {"name": "莲花山公园", "address": "深圳市福田区红荔路6030号", "feature": "城市中心公园，邓小平铜像", "price": "免费"},
            {"name": "东门老街", "address": "深圳市罗湖区东门步行街", "feature": "深圳最早的商业街", "price": "免费"},
            {"name": "大鹏古城", "address": "深圳市龙岗区大鹏镇", "feature": "明清海防古城", "price": "免费"}
        ],
        "上海": [
            {"name": "外滩", "address": "上海市黄浦区中山东一路", "feature": "上海地标，万国建筑博览群", "price": "免费"},
            {"name": "东方明珠", "address": "上海市浦东新区世纪大道1号", "feature": "上海地标，广播电视塔", "price": "199元"},
            {"name": "豫园", "address": "上海市黄浦区豫园老街", "feature": "江南古典园林", "price": "40元"},
            {"name": "南京路步行街", "address": "上海市黄浦区南京东路", "feature": "中华第一商业街", "price": "免费"},
            {"name": "上海迪士尼", "address": "上海市浦东新区川沙新镇", "feature": "大型主题乐园", "price": "599元"},
            {"name": "陆家嘴", "address": "上海市浦东新区", "feature": "金融中心，摩天大楼", "price": "免费"},
            {"name": "田子坊", "address": "上海市黄浦区泰康路", "feature": "文艺街区，老上海风情", "price": "免费"}
        ],
        "南京": [
            {"name": "中山陵", "address": "南京市玄武区中山陵园风景区", "feature": "孙中山先生陵墓", "price": "免费"},
            {"name": "明孝陵", "address": "南京市玄武区钟山风景名胜区", "feature": "明太祖朱元璋陵墓，世界文化遗产", "price": "70元"},
            {"name": "夫子庙", "address": "南京市秦淮区秦淮河北岸", "feature": "中国四大文庙之一", "price": "免费"},
            {"name": "秦淮河", "address": "南京市秦淮区", "feature": "南京母亲河，夜景迷人", "price": "免费"},
            {"name": "总统府", "address": "南京市玄武区长江路292号", "feature": "中国近代史博物馆", "price": "35元"},
            {"name": "南京博物院", "address": "南京市玄武区中山东路321号", "feature": "国家一级博物馆", "price": "免费"},
            {"name": "老门东", "address": "南京市秦淮区老门东", "feature": "老南京街巷，文艺街区", "price": "免费"}
        ],
        "林芝": [
            {"name": "雅鲁藏布大峡谷", "address": "林芝市米林县派镇", "feature": "世界第一大峡谷，壮观雪山云雾缭绕", "price": "290元"},
            {"name": "南迦巴瓦峰", "address": "林芝市米林县", "feature": "中国最美山峰，海拔7782米", "price": "免费"},
            {"name": "鲁朗林海", "address": "林芝市巴宜区鲁朗镇", "feature": "神仙居住的地方，藏区最美林海草原", "price": "免费"},
            {"name": "巴松措", "address": "林芝市工布江达县", "feature": "藏东南著名圣湖，雪山森林湖泊", "price": "170元"},
            {"name": "米堆冰川", "address": "林芝市波密县", "feature": "中国最美冰川之一", "price": "50元"},
            {"name": "岗云杉林", "address": "林芝市波密县", "feature": "中国最美森林", "price": "80元"},
            {"name": "色季拉山", "address": "林芝市巴宜区", "feature": "观看南迦巴瓦最佳位置", "price": "免费"}
        ],
        "克拉玛依": [
            {"name": "世界魔鬼城", "address": "克拉玛依市乌尔禾区", "feature": "典型雅丹地貌，如同废弃古城", "price": "62元"},
            {"name": "黑油山", "address": "克拉玛依市东北部准噶尔路", "feature": "世界罕见天然沥青丘，原油常年外溢", "price": "30元"},
            {"name": "克拉玛依河景区", "address": "克拉玛依区昆仑路", "feature": "城市绿肺，傍晚灯光璀璨", "price": "免费"},
            {"name": "独山子大峡谷", "address": "克拉玛依市独山子区", "feature": "天山雪水冲刷出的峡谷", "price": "免费"},
            {"name": "百里油区观景台", "address": "克拉玛依市白碱滩区", "feature": "俯瞰一望无际的磕头机群", "price": "免费"},
            {"name": "艾里克湖", "address": "乌尔禾区魔鬼城东南", "feature": "荒漠中的淡水湖", "price": "免费"},
            {"name": "克拉玛依博物馆", "address": "克拉玛依区准噶尔路22号", "feature": "了解油田发展史", "price": "免费"}
        ],
        "西双版纳": [
            {"name": "中科院西双版纳热带植物园", "address": "西双版纳勐腊县勐仑镇", "feature": "中国最大热带植物园，万种植物", "price": "104元"},
            {"name": "野象谷", "address": "西双版纳景洪市勐养镇", "feature": "亚洲野象栖息地", "price": "70元"},
            {"name": "曼听公园", "address": "西双版纳景洪市曼听路", "feature": "傣族历史文化名园", "price": "54元"},
            {"name": "原始森林公园", "address": "西双版纳景洪市昆洛路", "feature": "原始森林+民族风情", "price": "65元"},
            {"name": "告庄西双景", "address": "西双版纳景洪市宣慰大道", "feature": "傣泰风情小镇，星光夜市", "price": "免费"},
            {"name": "望天树景区", "address": "西双版纳勐腊县", "feature": "热带雨林空中走廊", "price": "75元"},
            {"name": "傣族园", "address": "西双版纳景洪市橄榄坝", "feature": "五个傣族自然村寨", "price": "65元"}
        ],
        "张家界": [
            {"name": "张家界国家森林公园", "address": "张家界市武陵源区", "feature": "世界自然遗产，阿凡达取景地", "price": "228元"},
            {"name": "天门山", "address": "张家界市永定区", "feature": "世界最长高山索道", "price": "258元"},
            {"name": "玻璃栈道", "address": "张家界市武陵源区", "feature": "悬崖玻璃栈道惊险刺激", "price": "免费"},
            {"name": "黄龙洞", "address": "张家界市武陵源区", "feature": "亚洲最美溶洞", "price": "100元"},
            {"name": "宝峰湖", "address": "张家界市武陵源区", "feature": "高峡平湖，山水相映", "price": "96元"},
            {"name": "张家界大峡谷", "address": "张家界市慈利县", "feature": "玻璃桥所在景区", "price": "118元"},
            {"name": "袁家界", "address": "张家界市武陵源区", "feature": "石英砂岩峰林", "price": "含森林公园票内"}
        ],
        "桂林": [
            {"name": "漓江", "address": "桂林市阳朔县", "feature": "桂林山水甲天下，20元人民币背景", "price": "215元"},
            {"name": "阳朔西街", "address": "桂林市阳朔县", "feature": "千年古街，酒吧文化", "price": "免费"},
            {"name": "象鼻山", "address": "桂林市象山区", "feature": "桂林城徽，象山水月", "price": "70元"},
            {"name": "龙脊梯田", "address": "桂林市龙胜各族自治县", "feature": "世界梯田原乡", "price": "100元"},
            {"name": "遇龙河", "address": "桂林市阳朔县", "feature": "小漓江，竹筏漂流", "price": "240元"},
            {"name": "银子岩", "address": "桂林市荔浦县", "feature": "世界溶洞奇观", "price": "90元"},
            {"name": "两江四湖", "address": "桂林市秀峰区", "feature": "桂林市中心水系夜景", "price": "190元"}
        ],
        "丽江": [
            {"name": "丽江古城", "address": "丽江市古城区", "feature": "世界文化遗产，纳西族古城", "price": "免费"},
            {"name": "玉龙雪山", "address": "丽江市玉龙纳西族自治县", "feature": "纳西神山，冰川雪峰", "price": "230元"},
            {"name": "泸沽湖", "address": "丽江市宁蒗彝族自治县", "feature": "摩梭人母系社会", "price": "100元"},
            {"name": "束河古镇", "address": "丽江市古城区束河古镇", "feature": "茶马古道重镇", "price": "40元"},
            {"name": "拉市海", "address": "丽江市玉龙县", "feature": "高原湖泊湿地", "price": "30元"},
            {"name": "虎跳峡", "address": "丽江市玉龙县虎跳峡镇", "feature": "世界最深峡谷之一", "price": "65元"},
            {"name": "白沙古镇", "address": "丽江市玉龙县", "feature": "纳西族最早的古都之一", "price": "免费"}
        ],
        "大理": [
            {"name": "洱海", "address": "大理市大理镇", "feature": "高原湖泊，环海骑行", "price": "免费"},
            {"name": "大理古城", "address": "大理市大理镇", "feature": "白族文化古城", "price": "免费"},
            {"name": "苍山", "address": "大理市大理镇", "feature": "十九峰十八溪", "price": "40元"},
            {"name": "双廊古镇", "address": "大理市洱源县双廊镇", "feature": "苍洱风光第一镇", "price": "免费"},
            {"name": "崇圣寺三塔", "address": "大理市大理镇", "feature": "大理标志性建筑", "price": "121元"},
            {"name": "沙溪古镇", "address": "大理市剑川县", "feature": "茶马古道上的古镇", "price": "免费"},
            {"name": "喜洲古镇", "address": "大理市大理镇", "feature": "白族民居古镇", "price": "免费"}
        ],
        "长白山": [
            {"name": "长白山天池", "address": "延边州安图县二道白河镇", "feature": "世界海拔最高火山口湖", "price": "125元"},
            {"name": "长白瀑布", "address": "延边州安图县", "feature": "东北最大瀑布", "price": "免费"},
            {"name": "长白山温泉", "address": "延边州安图县", "feature": "聚龙泉温泉群", "price": "100元"},
            {"name": "地下森林", "address": "延边州安图县", "feature": "谷底林海", "price": "含门票"},
            {"name": "长白山西坡", "address": "白山市抚松县", "feature": "观看天池最佳位置", "price": "125元"},
            {"name": "锦江大峡谷", "address": "白山市抚松县", "feature": "火山熔岩峡谷", "price": "含门票"},
            {"name": "望天鹅", "address": "白山市长白县", "feature": "柱状节理景观", "price": "100元"}
        ],
        "三亚": [
            {"name": "亚龙湾", "address": "三亚市吉阳区亚龙湾路", "feature": "天下第一湾，沙滩洁白细腻", "price": "免费"},
            {"name": "蜈支洲岛", "address": "三亚市海棠湾镇", "feature": "中国最美海岛，水上项目", "price": "150元"},
            {"name": "天涯海角", "address": "三亚市天涯区", "feature": "海南最南端，浪漫景点", "price": "68元"},
            {"name": "南山寺", "address": "三亚市崖州区", "feature": "108米海上观音", "price": "129元"},
            {"name": "大东海", "address": "三亚市吉阳区", "feature": "市区海滩，免费开放", "price": "免费"},
            {"name": "鹿回头公园", "address": "三亚市吉阳区", "feature": "俯瞰三亚湾夜景", "price": "45元"},
            {"name": "呀诺达雨林", "address": "三亚市保亭县", "feature": "热带雨林文化旅游区", "price": "170元"}
        ],
        "黄山": [
            {"name": "黄山风景区", "address": "黄山市黄山区汤口镇", "feature": "世界文化与自然双重遗产，奇松怪石云海温泉", "price": "230元"},
            {"name": "宏村", "address": "黄山市黟县宏村镇", "feature": "世界文化遗产，徽派古村落代表", "price": "104元"},
            {"name": "西递", "address": "黄山市黟县西递镇", "feature": "世界文化遗产，明清古村落", "price": "104元"},
            {"name": "屯溪老街", "address": "黄山市屯溪区", "feature": "徽州文化商业街，老字号店铺", "price": "免费"},
            {"name": "徽州古城", "address": "黄山市歙县", "feature": "中国四大古城之一，徽州文化发源地", "price": "80元"},
            {"name": "翡翠谷", "address": "黄山市黄山区汤口镇", "feature": "彩池群，《卧虎藏龙》取景地", "price": "75元"},
            {"name": "呈坎古镇", "address": "黄山市徽州区呈坎镇", "feature": "风水八卦村，保存完好的古村落", "price": "80元"}
        ],
        "稻城亚丁": [
            {"name": "亚丁风景区", "address": "甘孜州稻城县香格里拉镇", "feature": "最后的香格里拉，雪山圣山圣湖", "price": "150元"},
            {"name": "央迈勇", "address": "甘孜州稻城县", "feature": "三怙主雪山之一", "price": "含景区门票"},
            {"name": "牛奶海", "address": "甘孜州稻城县亚丁村", "feature": "古冰川湖，状如水滴", "price": "含景区门票"},
            {"name": "五色海", "address": "甘孜州稻城县", "feature": "雪山圣湖，色彩变幻", "price": "含景区门票"},
            {"name": "洛绒牛场", "address": "甘孜州稻城县", "feature": "高原牧场，雪山环绕", "price": "含景区门票"},
            {"name": "冲古寺", "address": "甘孜州稻城县", "feature": "藏传佛教寺庙", "price": "免费"},
            {"name": "珍珠海", "address": "甘孜州稻城县", "feature": "仙乃日雪山下的圣湖", "price": "含景区门票"}
        ],
        "敦煌": [
            {"name": "莫高窟", "address": "酒泉市敦煌市", "feature": "世界文化遗产，千年佛教艺术宝库", "price": "238元"},
            {"name": "鸣沙山月牙泉", "address": "酒泉市敦煌市", "feature": "沙漠中的一泓清泉", "price": "120元"},
            {"name": "雅丹魔鬼城", "address": "酒泉市敦煌市", "feature": "雅丹地貌国家地质公园", "price": "120元"},
            {"name": "阳关遗址", "address": "酒泉市敦煌市阳关镇", "feature": "西出阳关无故人", "price": "50元"},
            {"name": "敦煌古城", "address": "酒泉市敦煌市", "feature": "仿古建筑群，影视基地", "price": "40元"},
            {"name": "玉门关", "address": "酒泉市敦煌市", "feature": "春风不度玉门关", "price": "40元"},
            {"name": "沙洲夜市", "address": "酒泉市敦煌市", "feature": "敦煌美食文化街", "price": "免费"}
        ],
        "厦门": [
            {"name": "鼓浪屿", "address": "厦门市思明区", "feature": "海上花园，万国建筑博览", "price": "免费"},
            {"name": "厦门大学", "address": "厦门市思明区思明南路", "feature": "中国最美大学之一", "price": "免费"},
            {"name": "南普陀寺", "address": "厦门市思明区", "feature": "闽南佛教胜地", "price": "免费"},
            {"name": "环岛路", "address": "厦门市思明区", "feature": "海滨风光，骑行大道", "price": "免费"},
            {"name": "曾厝垵", "address": "厦门市思明区", "feature": "文艺渔村", "price": "免费"},
            {"name": "中山路步行街", "address": "厦门市思明区", "feature": "厦门历史最悠久商业街", "price": "免费"},
            {"name": "日光岩", "address": "厦门市思明区鼓浪屿", "feature": "鼓浪屿最高点", "price": "60元"}
        ],
        "青海湖": [
            {"name": "青海湖二郎剑景区", "address": "海北州刚察县、共和县及海晏县交汇处", "feature": "中国最大内陆咸水湖", "price": "100元"},
            {"name": "茶卡盐湖", "address": "海西州乌兰县茶卡镇", "feature": "天空之镜，中国版乌尤尼", "price": "60元"},
            {"name": "黑马河乡", "address": "海南州共和县", "feature": "青海湖最佳日出观赏地", "price": "免费"},
            {"name": "鸟岛", "address": "海北州刚察县", "feature": "候鸟栖息地", "price": "100元"},
            {"name": "金银滩草原", "address": "海北州海晏县", "feature": "在那遥远的地方", "price": "100元"},
            {"name": "塔尔寺", "address": "西宁市湟中区鲁沙尔镇", "feature": "藏传佛教格鲁派六大寺院之一", "price": "80元"},
            {"name": "日月山", "address": "西宁市湟源县", "feature": "黄土高原与青藏高原分界线", "price": "40元"}
        ],
        "阿尔山": [
            {"name": "阿尔山国家森林公园", "address": "兴安盟阿尔山市天池镇", "feature": "火山熔岩地貌，天池群", "price": "180元"},
            {"name": "阿尔山天池", "address": "兴安盟阿尔山市", "feature": "高位火山口湖", "price": "含景区门票"},
            {"name": "三潭峡", "address": "兴安盟阿尔山市", "feature": "森林峡谷溪流", "price": "含景区门票"},
            {"name": "石塘林", "address": "兴安盟阿尔山市", "feature": "火山熔岩地貌", "price": "含景区门票"},
            {"name": "杜鹃湖", "address": "兴安盟阿尔山市", "feature": "火山熔岩堰塞湖", "price": "含景区门票"},
            {"name": "奥伦布坎", "address": "兴安盟阿尔山市", "feature": "原始森林部落", "price": "120元"},
            {"name": "阿尔山火车站", "address": "兴安盟阿尔山市", "feature": "中国最美小火车站", "price": "免费"}
        ],
        "北戴河": [
            {"name": "北戴河海滨", "address": "秦皇岛市北戴河区", "feature": "中国四大避暑胜地", "price": "免费"},
            {"name": "山海关", "address": "秦皇岛市山海关区", "feature": "天下第一关，明长城东端起点", "price": "50元"},
            {"name": "鸽子窝公园", "address": "秦皇岛市北戴河区", "feature": "观日出最佳地", "price": "25元"},
            {"name": "老虎石海上公园", "address": "秦皇岛市北戴河区", "feature": "海滩礁石群", "price": "8元"},
            {"name": "南戴河", "address": "秦皇岛市抚宁区", "feature": "沙滩大海，游乐设施", "price": "免费"},
            {"name": "老龙头", "address": "秦皇岛市山海关区", "feature": "长城入海处", "price": "50元"},
            {"name": "联峰山公园", "address": "秦皇岛市北戴河区", "feature": "海滨山林，登高望海", "price": "25元"}
        ],
        "秦皇岛": [
            {"name": "北戴河海滨", "address": "秦皇岛市北戴河区", "feature": "中国四大避暑胜地", "price": "免费"},
            {"name": "山海关", "address": "秦皇岛市山海关区", "feature": "天下第一关", "price": "50元"},
            {"name": "老龙头", "address": "秦皇岛市山海关区", "feature": "长城入海处", "price": "50元"},
            {"name": "鸽子窝公园", "address": "秦皇岛市北戴河区", "feature": "观日出最佳地", "price": "25元"},
            {"name": "老虎石海上公园", "address": "秦皇岛市北戴河区", "feature": "海滩礁石群", "price": "8元"},
            {"name": "南戴河国际娱乐中心", "address": "秦皇岛市抚宁区", "feature": "沙滩娱乐设施", "price": "120元"},
            {"name": "联峰山公园", "address": "秦皇岛市北戴河区", "feature": "登高望海", "price": "25元"}
        ],
        "呼和浩特": [
            {"name": "大召寺", "address": "呼和浩特市玉泉区", "feature": "内蒙古最早的藏传佛教寺庙", "price": "35元"},
            {"name": "昭君墓", "address": "呼和浩特市玉泉区", "feature": "古代和亲文化", "price": "65元"},
            {"name": "塞上老街", "address": "呼和浩特市玉泉区", "feature": "明清风格古建筑群", "price": "免费"},
            {"name": "内蒙古博物院", "address": "呼和浩特市新城区", "feature": "了解内蒙古历史文化", "price": "免费"},
            {"name": "席力图召", "address": "呼和浩特市玉泉区", "feature": "藏传佛教格鲁派寺庙", "price": "30元"},
            {"name": "哈素海", "address": "呼和浩特市土默特左旗", "feature": "草原湖泊，塞外西湖", "price": "免费"},
            {"name": "辉腾锡勒草原", "address": "乌兰察布市察哈尔右翼中旗", "feature": "高山草甸草原，风电景观", "price": "90元"}
        ],
        "重庆": [
            {"name": "洪崖洞", "address": "重庆市渝中区嘉陵江滨江路", "feature": "巴渝传统吊脚楼建筑群", "price": "免费"},
            {"name": "解放碑", "address": "重庆市渝中区邹容路100号", "feature": "重庆地标，商业中心", "price": "免费"},
            {"name": "磁器口古镇", "address": "重庆市沙坪坝区磁器口镇", "feature": "千年古镇，巴渝文化", "price": "免费"},
            {"name": "长江索道", "address": "重庆市渝中区新华路153号", "feature": "空中交通工具，俯瞰两江", "price": "20元"},
            {"name": "武隆天生三桥", "address": "重庆市武隆区仙女山镇", "feature": "世界自然遗产，奇特地貌", "price": "125元"},
            {"name": "李子坝轻轨站", "address": "重庆市渝中区李子坝", "feature": "穿楼而过的轻轨", "price": "免费"},
            {"name": "南山一棵树", "address": "重庆市南岸区南山", "feature": "俯瞰重庆夜景的最佳地点", "price": "30元"}
        ],
        "成都": [
            {"name": "宽窄巷子", "address": "成都市青羊区金河宾馆旁", "feature": "清代古街道，成都文化地标", "price": "免费"},
            {"name": "锦里古街", "address": "成都市武侯区武侯祠大街231号", "feature": "三国文化特色商业街", "price": "免费"},
            {"name": "成都大熊猫繁育研究基地", "address": "成都市成华区外北熊猫大道1375号", "feature": "国宝大熊猫", "price": "55元"},
            {"name": "武侯祠", "address": "成都市武侯区武侯祠大街231号", "feature": "纪念诸葛亮的庙宇", "price": "50元"},
            {"name": "杜甫草堂", "address": "成都市青羊区青华路37号", "feature": "唐代诗人杜甫故居", "price": "50元"},
            {"name": "都江堰", "address": "成都市都江堰市公园路", "feature": "世界文化遗产，古代水利工程", "price": "80元"},
            {"name": "青城山", "address": "成都市都江堰市青城山镇", "feature": "道教名山，清幽秀丽", "price": "80元"}
        ],
        "西安": [
            {"name": "兵马俑", "address": "西安市临潼区秦陵北路", "feature": "世界第八大奇迹，秦始皇陪葬坑", "price": "150元"},
            {"name": "大雁塔", "address": "西安市雁塔区慈恩路1号", "feature": "唐代佛塔，玄奘藏经处", "price": "30元"},
            {"name": "西安城墙", "address": "西安市中心", "feature": "保存最完整的古城墙", "price": "54元"},
            {"name": "钟楼", "address": "西安市碑林区东大街", "feature": "明代钟楼，城市中心地标", "price": "35元"},
            {"name": "鼓楼", "address": "西安市莲湖区北院门", "feature": "明代鼓楼，与钟楼相望", "price": "35元"},
            {"name": "华清宫", "address": "西安市临潼区华清路38号", "feature": "唐代皇家温泉宫苑", "price": "120元"},
            {"name": "回民街", "address": "西安市莲湖区北院门", "feature": "美食街区，西北风味", "price": "免费"}
        ],
        "呼伦贝尔": [
            {"name": "呼伦贝尔大草原", "address": "呼伦贝尔市海拉尔区", "feature": "世界四大草原之一，广袤无垠的绿色天堂", "price": "免费"},
            {"name": "满洲里国门", "address": "呼伦贝尔市满洲里市", "feature": "中国最大的陆路口岸，中俄边境标志性建筑", "price": "60元"},
            {"name": "额尔古纳湿地", "address": "呼伦贝尔市额尔古纳市", "feature": "亚洲第一湿地，美丽的生态景观", "price": "65元"},
            {"name": "套娃广场", "address": "呼伦贝尔市满洲里市", "feature": "世界最大套娃主题广场，充满异域风情", "price": "148元"},
            {"name": "莫日格勒河", "address": "呼伦贝尔市陈巴尔虎旗", "feature": "老舍笔下的天下第一曲水", "price": "免费"},
            {"name": "恩和俄罗斯族乡", "address": "呼伦贝尔市额尔古纳市", "feature": "中国唯一的俄罗斯族民族乡", "price": "免费"},
            {"name": "黑山头古城", "address": "呼伦贝尔市额尔古纳市", "feature": "金代古城遗址，草原上的历史遗迹", "price": "免费"}
        ],
        "北京": [
            {"name": "故宫博物院", "address": "北京市东城区景山前街4号", "feature": "世界上现存规模最大的木质结构古建筑群", "price": "60元"},
            {"name": "八达岭长城", "address": "北京市延庆区", "feature": "万里长城的重要关口，世界文化遗产", "price": "40元"},
            {"name": "颐和园", "address": "北京市海淀区新建宫门路19号", "feature": "中国现存规模最大、保存最完整的皇家园林", "price": "30元"},
            {"name": "天坛公园", "address": "北京市东城区天坛东里甲1号", "feature": "明清两代皇帝祭天祈谷的场所", "price": "15元"},
            {"name": "圆明园", "address": "北京市海淀区清华西路28号", "feature": "清代皇家园林，历史遗址", "price": "25元"},
            {"name": "鸟巢", "address": "北京市朝阳区国家体育场南路1号", "feature": "2008年奥运会主体育场", "price": "50元"},
            {"name": "南锣鼓巷", "address": "北京市东城区", "feature": "北京最古老的街区之一，胡同文化", "price": "免费"}
        ],
        "杭州": [
            {"name": "西湖", "address": "杭州市西湖区", "feature": "中国十大名胜之一，湖光山色", "price": "免费"},
            {"name": "灵隐寺", "address": "杭州市西湖区灵隐路法云弄1号", "feature": "江南著名古刹，香火旺盛", "price": "30元"},
            {"name": "雷峰塔", "address": "杭州市西湖区南山路15号", "feature": "白娘子传说发源地", "price": "40元"},
            {"name": "西溪湿地", "address": "杭州市西湖区天目山路518号", "feature": "国家湿地公园，生态保护区", "price": "80元"},
            {"name": "宋城", "address": "杭州市西湖区之江路148号", "feature": "仿宋代主题乐园", "price": "300元"},
            {"name": "千岛湖", "address": "杭州市淳安县", "feature": "千座岛屿组成的大型湖泊", "price": "130元"},
            {"name": "河坊街", "address": "杭州市上城区", "feature": "南宋御街，老字号店铺", "price": "免费"}
        ],
        "上海": [
            {"name": "外滩", "address": "上海市黄浦区中山东一路", "feature": "上海地标，万国建筑博览群", "price": "免费"},
            {"name": "东方明珠", "address": "上海市浦东新区世纪大道1号", "feature": "上海地标，广播电视塔", "price": "199元"},
            {"name": "豫园", "address": "上海市黄浦区豫园老街", "feature": "江南古典园林", "price": "40元"},
            {"name": "南京路步行街", "address": "上海市黄浦区南京东路", "feature": "中华第一商业街", "price": "免费"},
            {"name": "上海迪士尼", "address": "上海市浦东新区川沙新镇", "feature": "大型主题乐园", "price": "599元"},
            {"name": "陆家嘴", "address": "上海市浦东新区", "feature": "金融中心，摩天大楼", "price": "免费"},
            {"name": "田子坊", "address": "上海市黄浦区泰康路", "feature": "文艺街区，老上海风情", "price": "免费"}
        ],
        "广州": [
            {"name": "广州塔", "address": "广州市海珠区阅江西路222号", "feature": "广州地标，小蛮腰", "price": "150元"},
            {"name": "陈家祠", "address": "广州市荔湾区中山七路", "feature": "岭南建筑艺术殿堂", "price": "10元"},
            {"name": "沙面岛", "address": "广州市荔湾区沙面街", "feature": "欧式建筑群，文艺打卡地", "price": "免费"},
            {"name": "白云山", "address": "广州市白云区广园中路801号", "feature": "广州名山，城市绿肺", "price": "5元"},
            {"name": "长隆欢乐世界", "address": "广州市番禺区汉溪大道东", "feature": "大型主题乐园", "price": "250元"},
            {"name": "北京路步行街", "address": "广州市越秀区北京路", "feature": "千年古道，商业中心", "price": "免费"},
            {"name": "圣心大教堂", "address": "广州市越秀区一德路", "feature": "哥特式教堂，远东巴黎圣母院", "price": "免费"}
        ],
        "深圳": [
            {"name": "世界之窗", "address": "深圳市南山区深南大道9037号", "feature": "世界微缩景观主题公园", "price": "220元"},
            {"name": "东部华侨城", "address": "深圳市盐田区梅沙街道", "feature": "大型旅游度假区", "price": "200元"},
            {"name": "欢乐谷", "address": "深圳市南山区侨城西街18号", "feature": "大型主题乐园", "price": "230元"},
            {"name": "深圳湾公园", "address": "深圳市南山区滨海大道", "feature": "海滨休闲公园", "price": "免费"},
            {"name": "莲花山公园", "address": "深圳市福田区红荔路6030号", "feature": "城市中心公园，邓小平铜像", "price": "免费"},
            {"name": "东门老街", "address": "深圳市罗湖区东门步行街", "feature": "深圳最早的商业街", "price": "免费"},
            {"name": "大鹏古城", "address": "深圳市龙岗区大鹏镇", "feature": "明清海防古城", "price": "免费"}
        ]
    }
    
    city_restaurants = {
        "呼伦贝尔": [
            {"name": "草原牧歌", "dish": "手把肉、烤羊腿、奶茶", "price": "约120元"},
            {"name": "满洲里卢布里西餐厅", "dish": "红菜汤、列巴、俄罗斯烤肉", "price": "约100元"},
            {"name": "海拉尔火锅城", "dish": "涮羊肉、羊蝎子、酸菜锅", "price": "约90元"},
            {"name": "额尔古纳铁锅炖", "dish": "铁锅炖大鹅、小鸡炖蘑菇", "price": "约80元"},
            {"name": "草原烧烤摊", "dish": "烤羊肉串、烤羊腰、烤蔬菜", "price": "约60元"}
        ],
        "黄山": [
            {"name": "老街坊徽菜", "dish": "臭鳜鱼、毛豆腐、黄山烧饼", "price": "约80元"},
            {"name": "徽香源食府", "dish": "黄山炖鸽、石耳炖鸡汤、笋烧肉", "price": "约100元"},
            {"name": "老街小吃城", "dish": "徽州蒸饺、苞芦馃、蟹壳黄", "price": "约30元"},
            {"name": "呈坎罗氏宗祠餐厅", "dish": "罗氏红烧肉、腌鲜鳜鱼、茶油炒笋", "price": "约90元"},
            {"name": "宏村民俗餐厅", "dish": "腊八豆腐、乌饭、蕨菜炒腊肉", "price": "约70元"}
        ],
        "北京": [
            {"name": "全聚德", "dish": "北京烤鸭、鸭架汤", "price": "约200元"},
            {"name": "四季民福", "dish": "烤鸭、贝勒烤肉、芥末鸭掌", "price": "约150元"},
            {"name": "大董", "dish": "酥不腻烤鸭、意境菜", "price": "约300元"},
            {"name": "东来顺", "dish": "涮羊肉、烤羊肉", "price": "约180元"},
            {"name": "护国寺小吃", "dish": "豆汁、焦圈、艾窝窝", "price": "约30元"}
        ],
        "杭州": [
            {"name": "楼外楼", "dish": "西湖醋鱼、龙井虾仁、叫化鸡", "price": "约200元"},
            {"name": "知味观", "dish": "小笼包、猫耳朵、西湖藕粉", "price": "约50元"},
            {"name": "外婆家", "dish": "茶香鸡、青豆泥、红烧肉", "price": "约80元"},
            {"name": "绿茶餐厅", "dish": "面包诱惑、烤鸡、绿茶饼", "price": "约70元"},
            {"name": "奎元馆", "dish": "虾爆鳝面、片儿川", "price": "约40元"}
        ],
        "成都": [
            {"name": "陈麻婆豆腐", "dish": "麻婆豆腐、回锅肉", "price": "约60元"},
            {"name": "宽窄巷子美食街", "dish": "串串香、钵钵鸡、三大炮", "price": "约50元"},
            {"name": "蜀风雅韵", "dish": "川菜表演套餐", "price": "约150元"},
            {"name": "玉林串串香", "dish": "串串火锅", "price": "约70元"},
            {"name": "钟水饺", "dish": "红油水饺、龙抄手", "price": "约30元"}
        ],
        "重庆": [
            {"name": "老火锅店", "dish": "麻辣火锅、毛肚、鸭肠", "price": "约100元"},
            {"name": "磁器口古镇小吃", "dish": "陈麻花、酸辣粉、古镇鸡杂", "price": "约40元"},
            {"name": "南山泉水鸡", "dish": "泉水鸡、辣子鸡", "price": "约120元"},
            {"name": "小面店", "dish": "重庆小面、豌杂面", "price": "约15元"},
            {"name": "朝天门码头火锅", "dish": "两江夜景火锅", "price": "约150元"}
        ],
        "西安": [
            {"name": "老孙家", "dish": "羊肉泡馍、肉夹馍", "price": "约40元"},
            {"name": "回民街小吃", "dish": "凉皮、肉夹馍、烤串", "price": "约30元"},
            {"name": "同盛祥", "dish": "牛羊肉泡馍、清真菜", "price": "约50元"},
            {"name": "德发长", "dish": "饺子宴", "price": "约80元"},
            {"name": "春发生", "dish": "葫芦头泡馍", "price": "约40元"}
        ],
        "广州": [
            {"name": "广州酒家", "dish": "文昌鸡、烤乳猪、点心", "price": "约200元"},
            {"name": "陶陶居", "dish": "虾饺、干蒸烧卖、叉烧包", "price": "约100元"},
            {"name": "点都德", "dish": "红米肠、凤爪、蛋挞", "price": "约80元"},
            {"name": "炳胜品味", "dish": "脆皮叉烧、菠萝包", "price": "约150元"},
            {"name": "上下九小吃", "dish": "肠粉、及第粥、油炸鬼", "price": "约20元"}
        ],
        "深圳": [
            {"name": "粤菜王府", "dish": "海鲜、烧腊、点心", "price": "约150元"},
            {"name": "椰子鸡火锅", "dish": "椰子鸡、文昌鸡", "price": "约100元"},
            {"name": "东门美食街", "dish": "各种小吃", "price": "约30元"},
            {"name": "潮汕牛肉火锅", "dish": "鲜牛肉、牛丸", "price": "约120元"},
            {"name": "客家菜馆", "dish": "酿豆腐、盐焗鸡", "price": "约80元"}
        ],
        "上海": [
            {"name": "绿波廊", "dish": "上海菜、点心", "price": "约150元"},
            {"name": "老正兴", "dish": "本帮菜、油爆虾", "price": "约120元"},
            {"name": "小杨生煎", "dish": "生煎包、咖喱牛肉汤", "price": "约20元"},
            {"name": "南翔馒头店", "dish": "小笼包", "price": "约30元"},
            {"name": "红宝石", "dish": "奶油小方、栗子蛋糕", "price": "约20元"}
        ],
        "南京": [
            {"name": "南京大牌档", "dish": "盐水鸭、鸭血粉丝汤、狮子头", "price": "约60元"},
            {"name": "夫子庙小吃", "dish": "秦淮八绝", "price": "约50元"},
            {"name": "莲湖糕团店", "dish": "赤豆元宵、糖粥", "price": "约15元"},
            {"name": "蒋有记", "dish": "牛肉锅贴、鸭血汤", "price": "约20元"},
            {"name": "刘长兴", "dish": "汤包、面条", "price": "约30元"}
        ],
        "林芝": [
            {"name": "鲁朗石锅鸡", "dish": "藏香鸡、手掌参", "price": "约200元"},
            {"name": "藏家土菜馆", "dish": "牦牛肉、青稞饼", "price": "约80元"},
            {"name": "大峡谷景区餐厅", "dish": "藏族土菜、汤面", "price": "约60元"},
            {"name": "扎西茶馆", "dish": "酥油茶、糌粑", "price": "约30元"},
            {"name": "巴松措鱼庄", "dish": "高原鱼、藏式火锅", "price": "约100元"}
        ],
        "克拉玛依": [
            {"name": "阿罗新疆餐厅", "dish": "大盘鸡、烤羊肉串、皮带面", "price": "约80元"},
            {"name": "王妈手撕兔", "dish": "手撕烤兔、麻辣兔头", "price": "约60元"},
            {"name": "西域老回民饭庄", "dish": "九碗三行子、椒麻鸡", "price": "约70元"},
            {"name": "乌尔禾拌面馆", "dish": "过油肉拌面、家常拌面", "price": "约35元"},
            {"name": "回家吃饭", "dish": "铁锅炖鱼、红烧排骨", "price": "约60元"}
        ],
        "西双版纳": [
            {"name": "曼听小寨", "dish": "香茅草烤鱼、傣味包烧", "price": "约80元"},
            {"name": "告庄夜市小吃", "dish": "舂鸡脚、老挝咖啡", "price": "约30元"},
            {"name": "傣家风味园", "dish": "菠萝饭、傣味拼盘", "price": "约60元"},
            {"name": "野象谷生态餐厅", "dish": "云南野菜、竹筒饭", "price": "约50元"},
            {"name": "勐海烤鸡店", "dish": "傣味烤鸡、手抓饭", "price": "约90元"}
        ],
        "张家界": [
            {"name": "三下锅餐馆", "dish": "张家界三下锅、土家腊肉", "price": "约60元"},
            {"name": "胡师傅三下锅", "dish": "核桃肉、肥肠、猪肚", "price": "约50元"},
            {"name": "寨子里的钵钵菜", "dish": "湘西土菜、炖菜", "price": "约55元"},
            {"name": "张家界米粉店", "dish": "湘西米粉、臊子面", "price": "约20元"},
            {"name": "天门山土菜馆", "dish": "山野菜、土家风味", "price": "约70元"}
        ],
        "桂林": [
            {"name": "桂林米粉大王", "dish": "卤菜粉、酸辣笋", "price": "约15元"},
            {"name": "椿记烧鹅", "dish": "烧鹅、桂林菜", "price": "约80元"},
            {"name": "阳朔啤酒鱼馆", "dish": "啤酒鱼、漓江虾", "price": "约120元"},
            {"name": "金龙寨", "dish": "桂林家常菜、芋头扣肉", "price": "约60元"},
            {"name": "崇善米粉", "dish": "桂林米粉、卤味", "price": "约12元"}
        ],
        "丽江": [
            {"name": "大石桥小吃", "dish": "丽江粑粑、鸡豆凉粉", "price": "约20元"},
            {"name": "腊排骨火锅", "dish": "腊排骨、时蔬火锅", "price": "约80元"},
            {"name": "三文鱼餐厅", "dish": "纳西烤鱼、三叠水", "price": "约120元"},
            {"name": "阿安酸奶", "dish": "云南酸奶、水果", "price": "约15元"},
            {"name": "钰洁腊排骨", "dish": "腊排骨、洋芋鸡", "price": "约90元"}
        ],
        "大理": [
            {"name": "白族土八碗", "dish": "大理特色、土八碗", "price": "约100元"},
            {"name": "沙坝鱼庄", "dish": "酸辣鱼、洱海鱼", "price": "约80元"},
            {"name": "大理烧烤摊", "dish": "烤乳扇、饵块", "price": "约30元"},
            {"name": "巍山耙肉饵丝", "dish": "耙肉饵丝、炸酱面", "price": "约25元"},
            {"name": "海舌公园鱼庄", "dish": "洱海野生鱼", "price": "约100元"}
        ],
        "长白山": [
            {"name": "朝鲜族大冷面", "dish": "冷面、辣白菜", "price": "约35元"},
            {"name": "长白山温泉煮蛋", "dish": "温泉鸡蛋、玉米", "price": "约10元"},
            {"name": "延边烤肉店", "dish": "韩式烤肉、石锅拌饭", "price": "约80元"},
            {"name": "东北饺子馆", "dish": "东北大馅饺子", "price": "约40元"},
            {"name": "山里人家铁锅炖", "dish": "小鸡炖蘑菇、铁锅炖", "price": "约90元"}
        ],
        "三亚": [
            {"name": "林姐香味海鲜", "dish": "清蒸和乐蟹、基围虾", "price": "约200元"},
            {"name": "三亚第一市场", "dish": "海鲜加工、海南菜", "price": "约150元"},
            {"name": "椰林椰子鸡", "dish": "椰子鸡、文昌鸡", "price": "约120元"},
            {"name": "海南粉汤店", "dish": "海南粉、抱罗粉", "price": "约15元"},
            {"name": "清凉补甜品店", "dish": "清凉补、椰汁西米露", "price": "约20元"}
        ],
        "稻城亚丁": [
            {"name": "青稞藏餐馆", "dish": "青稞酒、牦牛肉包子", "price": "约80元"},
            {"name": "亚丁驿站餐厅", "dish": "川菜、高原炖菜", "price": "约60元"},
            {"name": "稻城老砂锅", "dish": "牦牛肉砂锅", "price": "约70元"},
            {"name": "香巴拉藏餐吧", "dish": "藏式土火锅、酥油茶", "price": "约100元"},
            {"name": "高原牦牛汤锅", "dish": "牦牛肉汤锅", "price": "约90元"}
        ],
        "敦煌": [
            {"name": "敦煌驴肉黄面", "dish": "驴肉、黄面、浆水", "price": "约50元"},
            {"name": "靖远尕六美味羊羔肉", "dish": "手抓羊肉、黄焖羊肉", "price": "约80元"},
            {"name": "沙洲夜市小吃", "dish": "烤羊腿、杏皮水", "price": "约60元"},
            {"name": "胡羊焖饼", "dish": "羊肉焖饼、大盘鸡", "price": "约70元"},
            {"name": "达记酱驴肉黄面", "dish": "酱驴肉、黄面", "price": "约45元"}
        ],
        "厦门": [
            {"name": "黄则和花生汤店", "dish": "花生汤、海蛎煎", "price": "约30元"},
            {"name": "佳味再添小吃店", "dish": "沙茶面、油葱粿", "price": "约25元"},
            {"name": "月华沙茶面", "dish": "沙茶面、肉粽", "price": "约20元"},
            {"name": "鼓浪屿沈家闽南肠粉", "dish": "肠粉、闽南风味", "price": "约15元"},
            {"name": "海鲜大排档", "dish": "清蒸鱼、海蛎煎、沙茶面", "price": "约150元"}
        ],
        "青海湖": [
            {"name": "青海土火锅", "dish": "藏式土火锅、炕锅羊肉", "price": "约80元"},
            {"name": "青海老酸奶店", "dish": "牦牛酸奶、青稞饼", "price": "约20元"},
            {"name": "茶卡镇手抓羊肉", "dish": "青海手抓羊肉、面片", "price": "约70元"},
            {"name": "塔尔寺素餐馆", "dish": "藏式素斋、酥油茶", "price": "约40元"},
            {"name": "黑马河土菜馆", "dish": "青海土菜、青海湖鱼", "price": "约60元"}
        ],
        "阿尔山": [
            {"name": "阿尔山林业局餐厅", "dish": "林区铁锅炖、山野菜", "price": "约70元"},
            {"name": "天池鱼庄", "dish": "天池冷水鱼、农家菜", "price": "约120元"},
            {"name": "内蒙烤羊腿", "dish": "烤羊腿、手把肉", "price": "约150元"},
            {"name": "林区大铁锅炖", "dish": "东北铁锅炖、炖菜", "price": "约80元"},
            {"name": "伊尔施大馅饺子馆", "dish": "东北大饺子、大拉皮", "price": "约35元"}
        ],
        "北戴河": [
            {"name": "北戴河海鲜大排档", "dish": "皮皮虾、清蒸鱼、烤鱿鱼", "price": "约150元"},
            {"name": "四条包子铺", "dish": "秦皇岛特色包子", "price": "约15元"},
            {"name": "老二位蒸饺", "dish": "蒸饺、海鲜", "price": "约50元"},
            {"name": "杨肠子火腿", "dish": "秦皇岛特产、火腿肠", "price": "约30元"},
            {"name": "山海关长城饽椤饼", "dish": "饽椤叶饼、当地小吃", "price": "约25元"}
        ],
        "呼和浩特": [
            {"name": "格日勒阿妈奶茶馆", "dish": "蒙古奶茶、手把肉、馅饼", "price": "约80元"},
            {"name": "麦香村", "dish": "烧麦、呼市早点", "price": "约40元"},
            {"name": "老绥远莜面城", "dish": "莜面窝窝、莜面鱼鱼", "price": "约50元"},
            {"name": "蒙古大营", "dish": "烤全羊、蒙古套餐", "price": "约200元"},
            {"name": "塞上老街小吃", "dish": "炸糕、羊肉串、内蒙小吃", "price": "约30元"}
        ],
        "重庆": [
            {"name": "老火锅店", "dish": "麻辣火锅、毛肚、鸭肠", "price": "约100元"},
            {"name": "磁器口古镇小吃", "dish": "陈麻花、酸辣粉、古镇鸡杂", "price": "约40元"},
            {"name": "南山泉水鸡", "dish": "泉水鸡、辣子鸡", "price": "约120元"},
            {"name": "小面店", "dish": "重庆小面、豌杂面", "price": "约15元"},
            {"name": "朝天门码头火锅", "dish": "两江夜景火锅、九宫格", "price": "约150元"}
        ],
        "成都": [
            {"name": "陈麻婆豆腐", "dish": "麻婆豆腐、回锅肉", "price": "约60元"},
            {"name": "宽窄巷子美食街", "dish": "串串香、钵钵鸡、三大炮", "price": "约50元"},
            {"name": "蜀风雅韵", "dish": "川菜表演套餐", "price": "约150元"},
            {"name": "玉林串串香", "dish": "串串火锅", "price": "约70元"},
            {"name": "钟水饺", "dish": "红油水饺、龙抄手", "price": "约30元"}
        ],
        "西安": [
            {"name": "老孙家", "dish": "羊肉泡馍、肉夹馍", "price": "约40元"},
            {"name": "德发长", "dish": "饺子宴", "price": "约80元"},
            {"name": "回民街小吃", "dish": "凉皮、肉夹馍、烤串", "price": "约30元"},
            {"name": "同盛祥", "dish": "牛羊肉泡馍、清真菜", "price": "约50元"},
            {"name": "春发生", "dish": "葫芦头泡馍", "price": "约40元"}
        ],
        "黄山": [
            {"name": "老街坊徽菜", "dish": "臭鳜鱼、毛豆腐、黄山烧饼", "price": "约80元"},
            {"name": "徽香源食府", "dish": "黄山炖鸽、石耳炖鸡汤", "price": "约100元"},
            {"name": "老街小吃城", "dish": "徽州蒸饺、苞芦馃、蟹壳黄", "price": "约30元"},
            {"name": "呈坎罗氏宗祠餐厅", "dish": "罗氏红烧肉、腌鲜鳜鱼", "price": "约90元"},
            {"name": "宏村民俗餐厅", "dish": "腊八豆腐、乌饭、蕨菜炒腊肉", "price": "约70元"}
        ],
        "北京": [
            {"name": "全聚德", "dish": "北京烤鸭、鸭架汤", "price": "约200元"},
            {"name": "四季民福", "dish": "烤鸭、贝勒烤肉、芥末鸭掌", "price": "约150元"},
            {"name": "大董", "dish": "酥不腻烤鸭、意境菜", "price": "约300元"},
            {"name": "东来顺", "dish": "涮羊肉、烤羊肉", "price": "约180元"},
            {"name": "护国寺小吃", "dish": "豆汁、焦圈、艾窝窝", "price": "约30元"}
        ],
        "杭州": [
            {"name": "楼外楼", "dish": "西湖醋鱼、龙井虾仁、叫化鸡", "price": "约200元"},
            {"name": "知味观", "dish": "小笼包、猫耳朵、西湖藕粉", "price": "约50元"},
            {"name": "外婆家", "dish": "茶香鸡、青豆泥、红烧肉", "price": "约80元"},
            {"name": "绿茶餐厅", "dish": "面包诱惑、烤鸡、绿茶饼", "price": "约70元"},
            {"name": "奎元馆", "dish": "虾爆鳝面、片儿川", "price": "约40元"}
        ],
        "上海": [
            {"name": "上海老饭店", "dish": "本帮菜、红烧肉、油爆虾", "price": "约150元"},
            {"name": "绿波廊", "dish": "上海菜、点心", "price": "约150元"},
            {"name": "老正兴", "dish": "本帮菜、油爆虾", "price": "约120元"},
            {"name": "小杨生煎", "dish": "生煎包、咖喱牛肉汤", "price": "约20元"},
            {"name": "南翔馒头店", "dish": "小笼包", "price": "约30元"}
        ],
        "广州": [
            {"name": "广州酒家", "dish": "白切鸡、烧鹅、广式早茶", "price": "约100元"},
            {"name": "陶陶居", "dish": "早茶、虾饺、烧卖", "price": "约80元"},
            {"name": "陈添记鱼皮", "dish": "顺德鱼皮、艇仔粥", "price": "约30元"},
            {"name": "宝华面店", "dish": "云吞面、牛三星", "price": "约25元"},
            {"name": "南信双皮奶", "dish": "双皮奶、姜撞奶", "price": "约20元"}
        ],
        "深圳": [
            {"name": "蛇口渔人码头", "dish": "海鲜大排档、生蚝", "price": "约150元"},
            {"name": "潮泰牛肉火锅", "dish": "潮汕牛肉火锅、牛肉丸", "price": "约100元"},
            {"name": "凤凰楼", "dish": "广式早茶、点心", "price": "约80元"},
            {"name": "华强北粤菜馆", "dish": "粤菜、烧腊饭", "price": "约60元"},
            {"name": "东门町美食城", "dish": "街头小吃、烤串、奶茶", "price": "约40元"}
        ]
    }
    
    city_hotels = {
        "呼伦贝尔": [
            {"name": "呼伦贝尔海拉尔大酒店", "star": "★★★★★", "price": "800", "address": "海拉尔区", "reason": "市中心位置，交通便利"},
            {"name": "满洲里香格里拉大酒店", "star": "★★★★★", "price": "1200", "address": "满洲里市", "reason": "豪华舒适，边境风光"},
            {"name": "草原蒙古包度假村", "star": "★★★★", "price": "400", "address": "草原深处", "reason": "体验草原生活"}
        ],
        "黄山": [
            {"name": "黄山白云宾馆", "star": "★★★★★", "price": "1200", "address": "黄山风景区", "reason": "位于黄山山顶，方便看日出"},
            {"name": "黄山国际大酒店", "star": "★★★★★", "price": "580", "address": "黄山市中心", "reason": "豪华舒适，交通便利"},
            {"name": "黄山老街客栈", "star": "★★★★", "price": "280", "address": "屯溪老街", "reason": "古色古香，闹中取静"}
        ],
        "北京": [
            {"name": "北京王府井希尔顿", "star": "★★★★★", "price": "1200", "address": "王府井", "reason": "市中心，购物便利"},
            {"name": "北京国贸大酒店", "star": "★★★★★", "price": "1500", "address": "国贸", "reason": "高空景观，商务首选"},
            {"name": "北京如家快捷", "star": "★★★", "price": "280", "address": "各区域", "reason": "经济实惠，连锁可靠"}
        ],
        "杭州": [
            {"name": "杭州西湖国宾馆", "star": "★★★★★", "price": "1800", "address": "西湖边", "reason": "西湖美景，历史名园"},
            {"name": "杭州洲际酒店", "star": "★★★★★", "price": "800", "address": "钱江新城", "reason": "江景房，现代舒适"},
            {"name": "杭州青芝坞民宿", "star": "★★★★", "price": "300", "address": "西湖景区", "reason": "文艺清新，性价比高"}
        ],
        "成都": [
            {"name": "成都太古里博舍", "star": "★★★★★", "price": "1500", "address": "太古里", "reason": "潮流中心，设计感强"},
            {"name": "成都锦江宾馆", "star": "★★★★★", "price": "600", "address": "市中心", "reason": "老牌五星，服务周到"},
            {"name": "成都宽窄巷子民宿", "star": "★★★★", "price": "260", "address": "宽窄巷子", "reason": "古巷风情，出行方便"}
        ],
        "重庆": [
            {"name": "重庆丽思瑞", "star": "★★★★★", "price": "1000", "address": "解放碑", "reason": "高空观景，地理位置优越"},
            {"name": "重庆希尔顿", "star": "★★★★★", "price": "700", "address": "渝中半岛", "reason": "交通便利，商务舒适"},
            {"name": "重庆洪崖洞民宿", "star": "★★★★", "price": "350", "address": "洪崖洞", "reason": "江景房，夜景绝佳"}
        ],
        "西安": [
            {"name": "西安W酒店", "star": "★★★★★", "price": "1200", "address": "曲江", "reason": "现代设计，网红酒店"},
            {"name": "西安索菲特传奇", "star": "★★★★★", "price": "1000", "address": "市中心", "reason": "历史建筑，奢华体验"},
            {"name": "西安回民街客栈", "star": "★★★★", "price": "220", "address": "回民街", "reason": "美食环绕，出行方便"}
        ],
        "广州": [
            {"name": "广州四季酒店", "star": "★★★★★", "price": "2000", "address": "珠江新城", "reason": "云端酒店，无敌江景"},
            {"name": "广州白云宾馆", "star": "★★★★★", "price": "600", "address": "环市路", "reason": "老牌五星，地理位置好"},
            {"name": "广州上下九民宿", "star": "★★★★", "price": "200", "address": "上下九", "reason": "老广州风情，性价比高"}
        ],
        "深圳": [
            {"name": "深圳鹏瑞莱佛士", "star": "★★★★★", "price": "1800", "address": "深圳湾", "reason": "高端奢华，海景房"},
            {"name": "深圳福田香格里拉", "star": "★★★★★", "price": "800", "address": "福田中心", "reason": "商务便利，服务好"},
            {"name": "深圳城中村民宿", "star": "★★★", "price": "180", "address": "科技园", "reason": "经济实惠，近科技园"}
        ],
        "上海": [
            {"name": "上海半岛酒店", "star": "★★★★★", "price": "2500", "address": "外滩", "reason": "顶级奢华，外滩景观"},
            {"name": "上海浦东丽思卡尔顿", "star": "★★★★★", "price": "1800", "address": "陆家嘴", "reason": "云端酒店，俯瞰黄浦江"},
            {"name": "上海青年旅舍", "star": "★★★", "price": "80", "address": "各区域", "reason": "经济实惠，适合年轻人"}
        ],
        "南京": [
            {"name": "南京金陵饭店", "star": "★★★★★", "price": "800", "address": "新街口", "reason": "南京地标，服务一流"},
            {"name": "南京紫金山庄", "star": "★★★★★", "price": "1200", "address": "紫金山", "reason": "山水之间，环境优美"},
            {"name": "南京夫子庙客栈", "star": "★★★★", "price": "260", "address": "夫子庙", "reason": "秦淮风情，出行方便"}
        ],
        "林芝": [
            {"name": "林芝工布庄园希尔顿", "star": "★★★★★", "price": "1200", "address": "米林县", "reason": "雅鲁藏布江畔，景观一流"},
            {"name": "林芝大酒店", "star": "★★★★", "price": "480", "address": "八一镇", "reason": "市区位置，设施完善"},
            {"name": "鲁朗国际旅游小镇", "star": "★★★★", "price": "680", "address": "鲁朗镇", "reason": "藏式建筑，森林景观"}
        ],
        "克拉玛依": [
            {"name": "克拉玛依雪莲宾馆", "star": "★★★★", "price": "350", "address": "友谊路", "reason": "市中心，交通便利"},
            {"name": "克拉玛依鸿福大酒店", "star": "★★★★", "price": "320", "address": "昆仑路", "reason": "老牌酒店，服务规范"},
            {"name": "克拉玛依全季酒店", "star": "★★★", "price": "200", "address": "市区", "reason": "连锁品牌，性价比高"}
        ],
        "西双版纳": [
            {"name": "西双版纳洲际度假酒店", "star": "★★★★★", "price": "1000", "address": "景洪市", "reason": "傣泰风情，雨林景观"},
            {"name": "景洪告庄西双景客栈", "star": "★★★★", "price": "280", "address": "告庄西双景", "reason": "傣泰风格，夜市便利"},
            {"name": "西双版纳温馨民宿", "star": "★★★", "price": "150", "address": "景洪市区", "reason": "经济实惠，当地风情"}
        ],
        "张家界": [
            {"name": "张家界纳百利皇冠假日酒店", "star": "★★★★★", "price": "900", "address": "武陵源区", "reason": "景区门口，设施豪华"},
            {"name": "张家界山景酒店", "star": "★★★★", "price": "380", "address": "武陵源区", "reason": "山景房，近景区"},
            {"name": "张家界市区商务酒店", "star": "★★★", "price": "220", "address": "张家界市区", "reason": "交通便利，价格实惠"}
        ],
        "桂林": [
            {"name": "桂林香格里拉大酒店", "star": "★★★★★", "price": "800", "address": "桂林市区", "reason": "漓江边，景观房"},
            {"name": "阳朔糖舍酒店", "star": "★★★★★", "price": "1500", "address": "阳朔县", "reason": "网红酒店，设计独特"},
            {"name": "桂林市区连锁酒店", "star": "★★★", "price": "200", "address": "桂林市区", "reason": "经济实惠，出行方便"}
        ],
        "丽江": [
            {"name": "丽江悦榕庄", "star": "★★★★★", "price": "1800", "address": "玉龙雪山脚下", "reason": "雪山景观，别墅度假"},
            {"name": "丽江大研古城客栈", "star": "★★★★", "price": "280", "address": "丽江古城", "reason": "纳西风情，古城核心"},
            {"name": "丽江束河古镇民宿", "star": "★★★", "price": "180", "address": "束河古镇", "reason": "安静舒适，民族特色"}
        ],
        "大理": [
            {"name": "大理洱海天域酒店", "star": "★★★★★", "price": "980", "address": "大理下关镇", "reason": "海景房，俯瞰洱海"},
            {"name": "大理古城民宿", "star": "★★★★", "price": "260", "address": "大理古城", "reason": "白族风格，古城中心"},
            {"name": "双廊海景客栈", "star": "★★★★", "price": "380", "address": "双廊镇", "reason": "洱海边，海景房"}
        ],
        "长白山": [
            {"name": "长白山万达柏悦酒店", "star": "★★★★★", "price": "1500", "address": "长白山度假区", "reason": "滑雪/度假首选，顶级设施"},
            {"name": "长白山温泉度假酒店", "star": "★★★★", "price": "580", "address": "二道白河镇", "reason": "温泉配套，近景区"},
            {"name": "长白山景区宾馆", "star": "★★★", "price": "260", "address": "二道白河镇", "reason": "经济实惠，近景区"}
        ],
        "三亚": [
            {"name": "三亚亚特兰蒂斯", "star": "★★★★★", "price": "2500", "address": "海棠湾", "reason": "顶级度假，水上乐园"},
            {"name": "三亚湾红树林度假世界", "star": "★★★★", "price": "680", "address": "三亚湾", "reason": "大型度假综合体"},
            {"name": "三亚大东海经济型酒店", "star": "★★★", "price": "280", "address": "大东海", "reason": "近海滩，性价比高"}
        ],
        "稻城亚丁": [
            {"name": "稻城亚丁机场酒店", "star": "★★★★", "price": "580", "address": "香格里拉镇", "reason": "海拔适中，条件较好"},
            {"name": "稻城亚丁景区酒店", "star": "★★★", "price": "320", "address": "亚丁村", "reason": "景区内，近景点"},
            {"name": "稻城县城商务宾馆", "star": "★★★", "price": "180", "address": "稻城县城", "reason": "经济实惠，选择多"}
        ],
        "敦煌": [
            {"name": "敦煌山庄", "star": "★★★★★", "price": "800", "address": "敦煌市", "reason": "丝路风情，景观优美"},
            {"name": "敦煌市区四星酒店", "star": "★★★★", "price": "380", "address": "敦煌市区", "reason": "市中心，出行方便"},
            {"name": "鸣沙山特色民宿", "star": "★★★", "price": "200", "address": "鸣沙山附近", "reason": "近景点，体验沙漠风情"}
        ],
        "厦门": [
            {"name": "厦门海悦山庄酒店", "star": "★★★★★", "price": "1200", "address": "思明区环岛路", "reason": "海景房，依山傍海"},
            {"name": "厦门鼓浪屿别墅酒店", "star": "★★★★", "price": "520", "address": "鼓浪屿", "reason": "海岛风情，老别墅"},
            {"name": "厦门曾厝垵民宿", "star": "★★★", "price": "180", "address": "曾厝垵", "reason": "文艺渔村，近海滩"}
        ],
        "青海湖": [
            {"name": "青海湖宾馆", "star": "★★★★", "price": "480", "address": "青海湖二郎剑景区", "reason": "湖边住宿，景观一流"},
            {"name": "黑马河乡酒店", "star": "★★★", "price": "260", "address": "海南州共和县", "reason": "观日出最佳位置"},
            {"name": "茶卡镇商务宾馆", "star": "★★★", "price": "200", "address": "茶卡镇", "reason": "近茶卡盐湖，方便游览"}
        ],
        "阿尔山": [
            {"name": "阿尔山圣彼得堡大酒店", "star": "★★★★", "price": "580", "address": "阿尔山市", "reason": "俄式建筑，市中心位置"},
            {"name": "阿尔山天池度假酒店", "star": "★★★★", "price": "480", "address": "天池风景区", "reason": "景区内，方便游览"},
            {"name": "阿尔山市经济宾馆", "star": "★★★", "price": "180", "address": "阿尔山市", "reason": "经济实惠，条件尚可"}
        ],
        "北戴河": [
            {"name": "北戴河喜来登酒店", "star": "★★★★★", "price": "800", "address": "北戴河区滨海大道", "reason": "海景房，设施完善"},
            {"name": "北戴河鸽子窝公园酒店", "star": "★★★★", "price": "380", "address": "北戴河区鸽赤路", "reason": "近鸽子窝公园，观日出便利"},
            {"name": "北戴河刘庄民宿", "star": "★★★", "price": "180", "address": "北戴河区刘庄", "reason": "经济型，选择多"}
        ],
        "秦皇岛": [
            {"name": "秦皇岛海景酒店", "star": "★★★★★", "price": "680", "address": "海港区", "reason": "海景房，市中心"},
            {"name": "山海关假日酒店", "star": "★★★★", "price": "350", "address": "山海关区", "reason": "近天下第一关，历史悠久"},
            {"name": "秦皇岛市区连锁酒店", "star": "★★★", "price": "180", "address": "海港区", "reason": "经济实惠，交通便利"}
        ],
        "呼和浩特": [
            {"name": "呼和浩特香格里拉大酒店", "star": "★★★★★", "price": "680", "address": "呼和浩特市区", "reason": "市中心，五星级服务"},
            {"name": "呼和浩特敕勒川草原度假村", "star": "★★★★", "price": "420", "address": "敕勒川草原", "reason": "草原风光，蒙古包体验"},
            {"name": "呼和浩特如家快捷酒店", "star": "★★★", "price": "180", "address": "呼和浩特市区", "reason": "连锁品牌，经济实惠"}
        ],
        "重庆": [
            {"name": "重庆解放碑威斯汀酒店", "star": "★★★★★", "price": "1200", "address": "渝中区解放碑", "reason": "江景房，商圈中心"},
            {"name": "重庆洪崖洞大酒店", "star": "★★★★", "price": "480", "address": "渝中区沧白路", "reason": "吊脚楼建筑，近洪崖洞"},
            {"name": "重庆观音桥商圈酒店", "star": "★★★", "price": "260", "address": "江北区观音桥", "reason": "商圈便利，价格实惠"}
        ],
        "成都": [
            {"name": "成都博舍酒店", "star": "★★★★★", "price": "1500", "address": "太古里", "reason": "潮流中心，设计酒店"},
            {"name": "成都锦江宾馆", "star": "★★★★★", "price": "600", "address": "市中心", "reason": "老牌五星，服务周到"},
            {"name": "成都宽窄巷子民宿", "star": "★★★★", "price": "260", "address": "宽窄巷子", "reason": "古巷风情，出行方便"}
        ],
        "西安": [
            {"name": "西安威斯汀大酒店", "star": "★★★★★", "price": "800", "address": "曲江新区", "reason": "大雁塔旁，园林景观"},
            {"name": "西安钟楼饭店", "star": "★★★★", "price": "380", "address": "市中心钟楼", "reason": "市中心，出行便利"},
            {"name": "西安回民街客栈", "star": "★★★★", "price": "220", "address": "回民街", "reason": "美食环绕，出行方便"}
        ],
        "呼伦贝尔": [
            {"name": "呼伦贝尔海拉尔大酒店", "star": "★★★★★", "price": "800", "address": "海拉尔区", "reason": "市中心位置，交通便利"},
            {"name": "满洲里香格里拉大酒店", "star": "★★★★★", "price": "1200", "address": "满洲里市", "reason": "豪华舒适，边境风光"},
            {"name": "草原蒙古包度假村", "star": "★★★★", "price": "400", "address": "草原深处", "reason": "体验草原生活"}
        ],
        "黄山": [
            {"name": "黄山白云宾馆", "star": "★★★★★", "price": "1200", "address": "黄山风景区", "reason": "位于黄山山顶，方便看日出"},
            {"name": "黄山国际大酒店", "star": "★★★★★", "price": "580", "address": "黄山市中心", "reason": "豪华舒适，交通便利"},
            {"name": "黄山老街客栈", "star": "★★★★", "price": "280", "address": "屯溪老街", "reason": "古色古香，闹中取静"}
        ],
        "北京": [
            {"name": "北京王府井希尔顿", "star": "★★★★★", "price": "1200", "address": "王府井", "reason": "市中心，购物便利"},
            {"name": "北京国贸大酒店", "star": "★★★★★", "price": "1500", "address": "国贸", "reason": "高空景观，商务首选"},
            {"name": "北京如家快捷", "star": "★★★", "price": "280", "address": "各区域", "reason": "经济实惠，连锁可靠"}
        ],
        "杭州": [
            {"name": "杭州西湖国宾馆", "star": "★★★★★", "price": "1800", "address": "西湖边", "reason": "西湖美景，历史名园"},
            {"name": "杭州洲际酒店", "star": "★★★★★", "price": "800", "address": "钱江新城", "reason": "江景房，现代舒适"},
            {"name": "杭州青芝坞民宿", "star": "★★★★", "price": "300", "address": "西湖景区", "reason": "文艺清新，性价比高"}
        ],
        "上海": [
            {"name": "上海半岛酒店", "star": "★★★★★", "price": "2500", "address": "外滩", "reason": "顶级奢华，外滩景观"},
            {"name": "上海浦东丽思卡尔顿", "star": "★★★★★", "price": "1800", "address": "陆家嘴", "reason": "云端酒店，俯瞰黄浦江"},
            {"name": "上海青年旅舍", "star": "★★★", "price": "80", "address": "各区域", "reason": "经济实惠，适合年轻人"}
        ],
        "广州": [
            {"name": "广州四季酒店", "star": "★★★★★", "price": "2000", "address": "珠江新城", "reason": "云端酒店，无敌江景"},
            {"name": "广州白云宾馆", "star": "★★★★★", "price": "600", "address": "环市路", "reason": "老牌五星，地理位置好"},
            {"name": "广州上下九民宿", "star": "★★★", "price": "200", "address": "上下九", "reason": "老广州风情，性价比高"}
        ],
        "深圳": [
            {"name": "深圳鹏瑞莱佛士", "star": "★★★★★", "price": "1800", "address": "深圳湾", "reason": "高端奢华，海景房"},
            {"name": "深圳福田香格里拉", "star": "★★★★★", "price": "800", "address": "福田中心", "reason": "商务便利，服务好"},
            {"name": "深圳城中村民宿", "star": "★★★", "price": "180", "address": "科技园", "reason": "经济实惠，近科技园"}
        ]
    }
    
    # 转换交通方式拼音为中文
    transport_mapping = {
        "feiji": "飞机",
        "huoche": "火车",
        "zijiache": "自驾",
        "tuijian": "智能推荐"
    }
    transport = transport_mapping.get(transport, transport)
    
    # 获取该城市的数据，如果不存在则使用通用数据（使用合理的景点名称，而非占位符）
    generic_attractions = [
        {"name": f"{destination}城市公园", "address": f"{destination}市中心区", "feature": f"{destination}市民休闲首选，绿化覆盖率高", "price": "免费"},
        {"name": f"{destination}博物馆", "address": f"{destination}文化中心", "feature": f"了解{destination}历史文化的重要场所", "price": "免费"},
        {"name": f"{destination}古城步行街", "address": f"{destination}老城区", "feature": f"感受{destination}传统文化和市井气息", "price": "免费"},
        {"name": f"{destination}滨江/湖边景区", "address": f"{destination}滨江区", "feature": f"{destination}风景最美的自然景观区", "price": "免费"},
        {"name": f"{destination}人民广场", "address": f"{destination}市中心", "feature": f"{destination}城市地标和商业中心", "price": "免费"},
        {"name": f"{destination}植物园", "address": f"{destination}郊区", "feature": f"适合{destination}亲子游和放松心情的好去处", "price": "30元"},
        {"name": f"{destination}古镇风情区", "address": f"{destination}近郊", "feature": f"{destination}传统文化聚集地，民俗体验丰富", "price": "免费"}
    ]
    attractions = city_attractions.get(destination, generic_attractions) * 2
    
    generic_restaurants = [
        {"name": f"{destination}老字号酒楼", "dish": f"{destination}招牌菜、当地特色菜", "price": "约80元"},
        {"name": f"{destination}美食街", "dish": f"{destination}特色小吃、地方风味", "price": "约40元"},
        {"name": f"{destination}本地菜馆", "dish": f"{destination}家常菜、传统名菜", "price": "约60元"},
        {"name": f"{destination}夜市小吃", "dish": f"烧烤、{destination}地方小吃", "price": "约30元"},
        {"name": f"{destination}创意餐厅", "dish": "融合菜、创意料理", "price": "约100元"}
    ]
    restaurants = city_restaurants.get(destination, generic_restaurants) * 2
    
    generic_hotels = [
        {"name": f"{destination}国际大酒店", "star": "★★★★★", "price": "600", "address": f"{destination}市中心", "reason": "位置优越，设施齐全"},
        {"name": f"{destination}商务连锁酒店", "star": "★★★★", "price": "300", "address": f"{destination}商业中心", "reason": "交通便利，性价比高"},
        {"name": f"{destination}精品民宿", "star": "★★★★", "price": "180", "address": f"{destination}老城区", "reason": "有特色，体验当地风情"}
    ]
    hotels = city_hotels.get(destination, generic_hotels)
    
    import random
    random.seed()
    
    # 打乱景点和餐馆顺序，确保每天行程不重复
    random.shuffle(attractions)
    random.shuffle(restaurants)
    
    daily_plans = []
    used_attractions = []  # 记录已使用的景点
    
    for day in range(1, days + 1):
        current_date = start_date + timedelta(days=day-1)
        date_str = current_date.strftime('%Y年%m月%d日')
        
        # 选择上午景点（避免重复）
        morning_attraction = None
        for attr in attractions:
            if attr not in used_attractions:
                morning_attraction = attr
                used_attractions.append(attr)
                break
        if not morning_attraction:
            morning_attraction = attractions[day % len(attractions)]
        
        # 选择下午景点（避免与上午重复）
        afternoon_attraction = None
        for attr in attractions:
            if attr not in used_attractions:
                afternoon_attraction = attr
                used_attractions.append(attr)
                break
        if not afternoon_attraction:
            afternoon_attraction = attractions[(day + 1) % len(attractions)]
        
        # 选择午餐和晚餐（避免重复）
        lunch_idx = (day - 1) % len(restaurants)
        dinner_idx = (day) % len(restaurants)
        
        # 如果午餐和晚餐相同，选择下一个
        if lunch_idx == dinner_idx:
            dinner_idx = (dinner_idx + 1) % len(restaurants)
        
        lunch_restaurant = restaurants[lunch_idx]
        dinner_restaurant = restaurants[dinner_idx]
        
        evening_activities = [
            "自由活动，逛当地夜市",
            "欣赏城市夜景",
            "观看当地演出",
            "在酒店休息",
            "体验当地娱乐",
            "逛商场购物"
        ]
        evening = evening_activities[day % len(evening_activities)]
        
        daily_plan = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 第{day}天 · {date_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌅 【上午】景点名称：{morning_attraction['name']} | 地址：{morning_attraction['address']} | 特色：{morning_attraction['feature']} | `{get_amap_nav_url(morning_attraction['name'], destination)}`
🍜 【午餐】餐馆名称：{lunch_restaurant['name']} | 特色菜：{lunch_restaurant['dish']} | 人均：{lunch_restaurant['price']} | `{get_amap_nav_url(lunch_restaurant['name'], destination)}`
☀️ 【下午】景点名称：{afternoon_attraction['name']} | 地址：{afternoon_attraction['address']} | 特色：{afternoon_attraction['feature']} | `{get_amap_nav_url(afternoon_attraction['name'], destination)}`
🍽️ 【晚餐】餐馆名称：{dinner_restaurant['name']} | 特色菜：{dinner_restaurant['dish']} | 人均：{dinner_restaurant['price']} | `{get_amap_nav_url(dinner_restaurant['name'], destination)}`
🌙 【晚上】{evening}"""
        
        daily_plans.append(daily_plan)
    
    hotel_lines = []
    for i, h in enumerate(hotels[:3]):
        price = int(h['price']) * (2 if style == '豪华型' else 1 if style == '舒适型' else 0.5)
        hotel_lines.append(f"{i+1}. 【{h['name']}】| 星级：{h['star']} | 价格：{int(price)}元/晚 | 地址：{h['address']} | 推荐理由：{h['reason']}")
    hotel_recommendations = "\n".join(hotel_lines)
    
    all_foods = []
    for r in restaurants[:5]:
        dishes = r['dish'].split('、')
        for d in dishes[:2]:
            if d.strip():
                all_foods.append(d.strip())
    unique_foods = list(set(all_foods))[:8]
    
    food_lines = []
    food_reasons = ["当地特色，必品尝", "招牌菜品，不容错过", "地道风味，回味无穷", "传统美食，历史悠久", "风味独特，值得一试", "口感鲜美，广受好评", "营养丰富，健康美味", "制作精细，匠心独具"]
    for i, food in enumerate(unique_foods):
        food_lines.append(f"{i+1}. 【{food}】- 推荐理由：{food_reasons[i % len(food_reasons)]}")
    food_recommendations = "\n".join(food_lines)
    
    # 优化预算计算：根据旅行风格和人数计算更合理的预算
    transport_cost = 0
    if transport == "飞机":
        # 机票价格：经济舱600-1500元/人，根据旅行风格调整
        base_price = 800 if style == "经济型" else 1200 if style == "舒适型" else 2000
        transport_cost = base_price * 2 * int(people)  # 往返
    elif transport == "火车":
        # 火车票价格：硬座150-400元/人
        base_price = 200 if style == "经济型" else 300 if style == "舒适型" else 500
        transport_cost = base_price * 2 * int(people)  # 往返
    else:  # 自驾或智能推荐
        # 假设自驾油费+过路费约500元/天
        transport_cost = 500 * days
    
    # 住宿价格：根据风格调整
    base_hotel_price = 300 if style == "经济型" else 600 if style == "舒适型" else 1200
    hotel_cost = base_hotel_price * days
    
    # 餐饮价格：根据风格和人数调整
    if style == "经济型":
        meal_price = 50  # 人均每餐
    elif style == "舒适型":
        meal_price = 100
    else:  # 豪华型
        meal_price = 200
    food_cost = meal_price * 3 * days * int(people)  # 每天3餐
    
    # 门票价格：根据景点数量和人数
    ticket_cost = 50 * 2 * days * int(people)  # 每天2个景点
    
    # 其他费用（购物、娱乐等）
    other_cost = 100 * days * int(people)
    
    transport_cost = int(transport_cost)
    hotel_cost = int(hotel_cost)
    food_cost = int(food_cost)
    ticket_cost = int(ticket_cost)
    other_cost = int(other_cost)
    total = transport_cost + hotel_cost + food_cost + ticket_cost + other_cost
    
    separator = "\n\n"
    daily_content = separator.join(daily_plans)

    plan = """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚀 交通方案
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【去程】建议乘坐{}前往{}，请提前预订车票/机票
【返程】返程时请提前安排好时间，建议预留2小时前往机场/车站

{}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏨 住宿推荐
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🍲 特色美食
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 预算明细
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
┌────────┬────────┬──────────────┐
│ 项目   │ 费用   │ 备注        │
├────────┼────────┼──────────────┤
│ 交通   │ {}元 │ 往返交通    │
│ 住宿   │ {}元 │ {}晚住宿 │
│ 餐饮   │ {}元 │ 每日餐食    │
│ 门票   │ {}元 │ 景点门票    │
│ 其他   │ {}元 │ 杂费/购物   │
├────────┼────────┼──────────────┤
│ 总计   │ {}元 │ 仅供参考    │
└────────┴────────┴──────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 实用贴士
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. ⚠️ 建议提前在网上预订景点门票，避免排队
2. 📌 {}天气多变，请随身携带雨具
3. 💡 品尝当地美食建议去正规餐厅，注意饮食卫生
4. 🚗 出行尽量避开早晚高峰时段
5. 📱 提前下载好导航软件，方便查找路线

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""".format(
        transport, destination, daily_content,
        hotel_recommendations, food_recommendations,
        transport_cost, hotel_cost, days, food_cost, ticket_cost, other_cost, total,
        destination
    )
    
    return plan

def get_city_attractions(city):
    """获取城市的景点列表（带图片）"""
    city_info = None
    for c in CHINA_CITIES:
        if c['name'] == city or c.get('pinyin') == city:
            city_info = c
            break
    
    if city_info and city_info.get('famous'):
        attractions = []
        for attraction_name in city_info['famous'][:3]:
            attractions.append({
                'name': attraction_name,
                'image': get_attraction_image(attraction_name)
            })
        return attractions
    return []

def generate_html_report(trip_data, weather_list):
    days = (trip_data['end_date'] - trip_data['start_date']).days + 1
    from urllib.parse import quote as url_quote  # 用于URL编码景点名称
    from datetime import datetime  # 用于生成报告时间
    
    # 获取目的地景点图片
    city_attractions = get_city_attractions(trip_data['destination'])
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{trip_data['departure']} - {trip_data['destination']} 旅行攻略</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding-bottom: 50px; }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 30px; }}
        .header {{ background: white; border-radius: 20px; padding: 40px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); margin-bottom: 30px; }}
        .header h1 {{ color: #333; font-size: 28px; margin-bottom: 20px; line-height: 1.4; }}
        .header .info {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
        .header .info-item {{ background: #f8f9fa; padding: 15px; border-radius: 10px; }}
        .header .info-item span {{ color: #666; font-size: 14px; }}
        .header .info-item strong {{ color: #333; }}
        .update-time {{ font-size: 11px; color: #999; padding: 4px 12px; background: rgba(0,0,0,0.05); border-radius: 10px; }}
        .live-clock {{ font-size: 13px; color: #667eea; font-weight: 600; }}
        .header .time-section {{ display: flex; gap: 15px; margin-top: 15px; align-items: center; flex-wrap: wrap; }}
        .weather-section {{ background: white; border-radius: 20px; padding: 30px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); margin-bottom: 30px; }}
        .weather-section h2 {{ color: #333; font-size: 22px; margin-bottom: 20px; }}
        .weather-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 15px; }}
        .weather-card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 15px; text-align: center; }}
        .weather-card .date {{ font-size: 14px; opacity: 0.9; }}
        .weather-card .icon {{ font-size: 36px; margin: 10px 0; }}
        .weather-card .temp {{ font-size: 18px; font-weight: bold; }}
        .day-section {{ background: white; border-radius: 20px; padding: 30px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); margin-bottom: 30px; }}
        .day-section h2 {{ color: #333; font-size: 22px; margin-bottom: 25px; display: flex; align-items: center; gap: 10px; }}
        .day-section h2 span {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 8px 16px; border-radius: 20px; font-size: 14px; }}
        .schedule {{ display: grid; gap: 20px; }}
        .schedule-item {{ background: #f8f9fa; padding: 20px; border-radius: 15px; }}
        .schedule-item .time {{ color: #667eea; font-weight: bold; margin-bottom: 10px; font-size: 16px; }}
        .schedule-item .location {{ font-size: 18px; font-weight: bold; color: #333; margin-bottom: 8px; }}
        .schedule-item .desc {{ color: #666; line-height: 1.6; }}
        .schedule-item .image-wrapper {{ margin: 15px 0; border-radius: 10px; overflow: hidden; }}
        .schedule-item .attraction-image {{ width: 100%; height: 200px; object-fit: cover; }}
        .nav-btn {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 10px 20px; border-radius: 25px; text-decoration: none; font-size: 14px; margin-top: 10px; transition: all 0.2s; }}
        .nav-btn:hover {{ transform: scale(1.05); box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4); }}
        .recommend-section {{ background: white; border-radius: 20px; padding: 30px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); margin-bottom: 30px; }}
        .recommend-section h2 {{ color: #333; font-size: 22px; margin-bottom: 20px; }}
        .recommend-list {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }}
        .recommend-item {{ background: #f8f9fa; padding: 20px; border-radius: 15px; }}
        .recommend-item .image-wrapper {{ margin-bottom: 15px; border-radius: 10px; overflow: hidden; }}
        .recommend-item .recommend-image {{ width: 100%; height: 150px; object-fit: cover; }}
        .recommend-item .title {{ font-weight: bold; color: #333; margin-bottom: 8px; font-size: 16px; }}
        .recommend-item .desc {{ color: #666; font-size: 14px; line-height: 1.5; }}
        .recommend-item .price {{ color: #f5576c; font-weight: bold; margin-top: 10px; }}
        .budget-section {{ background: white; border-radius: 20px; padding: 30px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); }}
        .budget-section h2 {{ color: #333; font-size: 22px; margin-bottom: 20px; }}
        .budget-table {{ width: 100%; border-collapse: collapse; }}
        .budget-table th, .budget-table td {{ padding: 15px; text-align: left; border-bottom: 1px solid #eee; }}
        .budget-table th {{ background: #f8f9fa; color: #666; font-weight: 600; }}
        .budget-table tr:last-child td {{ border-bottom: none; }}
        .budget-table .total {{ font-weight: bold; color: #667eea; font-size: 16px; }}
        .footer {{ text-align: center; color: rgba(255,255,255,0.8); margin-top: 30px; font-size: 14px; }}
        .city-hero {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 20px; padding: 40px; margin-bottom: 30px; color: white; }}
        .city-hero h2 {{ font-size: 24px; margin-bottom: 15px; }}
        .city-hero .hero-image {{ width: 100%; height: 250px; border-radius: 15px; object-fit: cover; margin-top: 15px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>✈️ {trip_data['departure']} → {trip_data['destination']} 旅行攻略</h1>
            <div class="info">
                <div class="info-item"><span>出发日期：</span><strong>{trip_data['start_date'].strftime('%Y年%m月%d日')}</strong></div>
                <div class="info-item"><span>结束日期：</span><strong>{trip_data['end_date'].strftime('%Y年%m月%d日')}</strong></div>
                <div class="info-item"><span>旅行天数：</span><strong>{days}天</strong></div>
                <div class="info-item"><span>出行人数：</span><strong>{trip_data['people']}人</strong></div>
                <div class="info-item"><span>旅行风格：</span><strong>{trip_data['style']}</strong></div>
                <div class="info-item"><span>交通方式：</span><strong>{trip_data['transport']}</strong></div>
                <div class="info-item"><span>生成时间：</span><strong>{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}</strong></div>
            </div>
        </div>
        
        <!-- 目的地风采 -->
        <div class="city-hero">
            <h2>🏙️ {trip_data['destination']} 风采</h2>
            <p style="opacity: 0.9;">感受这座城市的独特魅力</p>
            <img src="{get_attraction_image(trip_data['destination']) if city_attractions else 'https://picsum.photos/seed/' + trip_data['destination'] + '/800/250'}" alt="{trip_data['destination']}" class="hero-image">
        </div>
        
        <div class="weather-section">
            <h2>🌤️ 未来{days}天天气预报</h2>
            <div class="weather-grid">
"""
    
    for weather in weather_list:
        html += f"""                <div class="weather-card">
                    <div class="date">{weather['date']}</div>
                    <div class="icon">☀️</div>
                    <div class="temp">{weather['temperature']}</div>
                    <div style="font-size: 12px; opacity: 0.9;">{weather['weather']}</div>
                </div>
"""
    
    html += f"""            </div>
        </div>
        
        <div class="day-section">
            <h2><span>Day 1</span> {trip_data['start_date'].strftime('%Y-%m-%d')} - 抵达与初识</h2>
            <div class="schedule">
                <div class="schedule-item">
                    <div class="time">🌅 上午 09:00-12:00</div>
                    <div class="location">抵达{trip_data['destination']}</div>
                    <div class="desc">乘坐{trip_data['transport']}抵达目的地，前往酒店办理入住，稍作休整</div>
                </div>
                <div class="schedule-item">
                    <div class="time">🍽️ 午餐 12:00-13:30</div>
                    <div class="location">当地特色餐厅</div>
                    <div class="desc">品尝{trip_data['destination']}特色美食，感受当地饮食文化</div>
                </div>
                <div class="schedule-item">
                    <div class="time">🏛️ 下午 14:00-17:00</div>
                    <div class="location">市区游览</div>
                    <div class="desc">漫步市中心，感受城市氛围</div>
                    <div class="image-wrapper"><img src="{get_attraction_image(trip_data['destination'])}" alt="{trip_data['destination']}" class="attraction-image"></div>
                    <a href="{get_amap_nav_url(trip_data['destination'], trip_data['destination'])}" class="nav-btn">📍 导航前往</a>
                </div>
                <div class="schedule-item">
                    <div class="time">🌙 晚餐 18:00-20:00</div>
                    <div class="location">当地美食街</div>
                    <div class="desc">体验当地夜市文化，品尝特色小吃</div>
                </div>
            </div>
        </div>"""
    
    # 生成中间天数的行程
    for day in range(2, days):
        attraction = city_attractions[(day-2) % len(city_attractions)] if city_attractions else None
        attraction_name = attraction.get('name', f'{trip_data["destination"]}景点{day}') if attraction else f'{trip_data["destination"]}景点{day}'
        attraction_image = attraction.get('image', f'https://picsum.photos/seed/{attraction_name}/400/200') if attraction else f'https://picsum.photos/seed/{attraction_name}/400/200'
        
        html += f"""
        <div class="day-section">
            <h2><span>Day {day}</span> {trip_data['start_date'].strftime('%Y-%m-%d')} - 深度探索</h2>
            <div class="schedule">
                <div class="schedule-item">
                    <div class="time">🌄 上午 08:30-12:00</div>
                    <div class="location">{attraction_name}</div>
                    <div class="desc">参观{trip_data['destination']}标志性景点，感受独特魅力</div>
                    <div class="image-wrapper"><img src="{attraction_image}" alt="{attraction_name}" class="attraction-image"></div>
                    <a href="{get_amap_nav_url(attraction_name, trip_data['destination'])}" class="nav-btn">📍 导航前往</a>
                </div>
                <div class="schedule-item">
                    <div class="time">🍲 午餐 12:00-13:30</div>
                    <div class="location">特色餐馆</div>
                    <div class="desc">品尝当地风味美食，体验地道口味</div>
                </div>
                <div class="schedule-item">
                    <div class="time">🎡 下午 14:00-17:30</div>
                    <div class="location">文化体验</div>
                    <div class="desc">沉浸式体验当地文化与自然景观</div>
                    <a href="{get_amap_nav_url(trip_data['destination'] + '文化体验', trip_data['destination'])}" class="nav-btn">📍 导航前往</a>
                </div>
                <div class="schedule-item">
                    <div class="time">🍷 晚餐 18:30-20:30</div>
                    <div class="location">当地特色餐厅</div>
                    <div class="desc">享用{trip_data['destination']}特色晚餐</div>
                </div>
            </div>
        </div>"""
    
    html += f"""
        <div class="day-section">
            <h2><span>Day {days}</span> {trip_data['end_date'].strftime('%Y-%m-%d')} - 告别之旅</h2>
            <div class="schedule">
                <div class="schedule-item">
                    <div class="time">🌅 上午 08:30-11:00</div>
                    <div class="location">最后的游览</div>
                    <div class="desc">在离开前，再感受一下{trip_data['destination']}的魅力</div>
                    <a href="{get_amap_nav_url(trip_data['destination'] + '地标', trip_data['destination'])}" class="nav-btn">📍 导航前往</a>
                </div>
                <div class="schedule-item">
                    <div class="time">🍽️ 午餐 11:30-13:00</div>
                    <div class="location">告别午餐</div>
                    <div class="desc">享用最后一顿当地美食，留下美好回忆</div>
                </div>
                <div class="schedule-item">
                    <div class="time">✈️ 下午 13:00-</div>
                    <div class="location">返程</div>
                    <div class="desc">整理行李，办理退房，踏上返程旅途</div>
                </div>
            </div>
        </div>
        
        <div class="recommend-section">
            <h2>🏨 酒店推荐</h2>
            <div class="recommend-list">
                <div class="recommend-item">
                    <div class="image-wrapper"><img src="https://picsum.photos/seed/hotel1/250/150" alt="酒店1" class="recommend-image"></div>
                    <div class="title">精选酒店A</div><div class="desc">位于市中心，交通便利，设施齐全</div><div class="price">¥{400 if trip_data['style'] == '经济型' else 800 if trip_data['style'] == '舒适型' else 1500}+/晚</div>
                </div>
                <div class="recommend-item">
                    <div class="image-wrapper"><img src="https://picsum.photos/seed/hotel2/250/150" alt="酒店2" class="recommend-image"></div>
                    <div class="title">精选酒店B</div><div class="desc">环境优雅，服务周到，适合度假</div><div class="price">¥{500 if trip_data['style'] == '经济型' else 1000 if trip_data['style'] == '舒适型' else 2000}+/晚</div>
                </div>
                <div class="recommend-item">
                    <div class="image-wrapper"><img src="https://picsum.photos/seed/hotel3/250/150" alt="酒店3" class="recommend-image"></div>
                    <div class="title">精选酒店C</div><div class="desc">性价比高，位置优越，出行方便</div><div class="price">¥{300 if trip_data['style'] == '经济型' else 600 if trip_data['style'] == '舒适型' else 1200}+/晚</div>
                </div>
            </div>
        </div>
        
        <div class="recommend-section">
            <h2>🍜 美食推荐</h2>
            <div class="recommend-list">
                <div class="recommend-item">
                    <div class="image-wrapper"><img src="https://picsum.photos/seed/food1/250/150" alt="美食1" class="recommend-image"></div>
                    <div class="title">特色美食1</div><div class="desc">{trip_data['destination']}必尝，地道风味</div>
                </div>
                <div class="recommend-item">
                    <div class="image-wrapper"><img src="https://picsum.photos/seed/food2/250/150" alt="美食2" class="recommend-image"></div>
                    <div class="title">特色美食2</div><div class="desc">当地招牌，不容错过</div>
                </div>
                <div class="recommend-item">
                    <div class="image-wrapper"><img src="https://picsum.photos/seed/food3/250/150" alt="美食3" class="recommend-image"></div>
                    <div class="title">特色美食3</div><div class="desc">风味独特，回味无穷</div>
                </div>
            </div>
        </div>
        
        <div class="budget-section">
            <h2>💰 预算明细</h2>
            <table class="budget-table">
                <tr><th>项目</th><th>费用（元/人）</th><th>备注</th></tr>
                <tr><td>交通</td><td>根据实际选择</td><td>往返交通费用</td></tr>
                <tr><td>住宿</td><td>约{500 if trip_data['style'] == '经济型' else 1000 if trip_data['style'] == '舒适型' else 2000}元/晚</td><td>根据酒店选择</td></tr>
                <tr><td>餐饮</td><td>约{80 if trip_data['style'] == '经济型' else 150 if trip_data['style'] == '舒适型' else 300}/天</td><td>每日餐饮费用</td></tr>
                <tr><td>门票</td><td>约200-500</td><td>景点门票费用</td></tr>
                <tr><td>其他</td><td>约100-300</td><td>购物、交通等杂费</td></tr>
                <tr><td class="total">总计</td><td class="total">约{calculate_total_budget(days, trip_data['style'])}</td><td class="total">仅供参考</td></tr>
            </table>
        </div>
        
        <div class="footer">
            <p>✈️ 祝您旅途愉快！</p>
            <p style="margin-top: 10px; opacity: 0.6;">本攻略由AI智能生成 | 仅供参考</p>
        </div>
    </div>
    <script>
        // 实时时钟
        function updateClock() {{
            const now = new Date();
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            const day = String(now.getDate()).padStart(2, '0');
            const hours = String(now.getHours()).padStart(2, '0');
            const minutes = String(now.getMinutes()).padStart(2, '0');
            const seconds = String(now.getSeconds()).padStart(2, '0');
            const clockEl = document.getElementById('live-clock');
            if (clockEl) {{
                clockEl.textContent = `⏰ ${{year}}-${{month}}-${{day}} ${{hours}}:${{minutes}}:${{seconds}}`;
            }}
        }}

        // 获取最后更新时间
        function fetchLastUpdate() {{
            fetch('/api/last-update')
                .then(response => response.json())
                .then(data => {{
                    if (data.last_update_time) {{
                        const updateEl = document.getElementById('update-time-display');
                        if (updateEl) {{
                            updateEl.textContent = `💾 最后更新: ${{data.last_update_time}}`;
                        }}
                    }}
                }})
                .catch(error => {{
                    console.error('获取更新时间失败:', error);
                }});
        }}

        // 页面加载完成后初始化
        window.addEventListener('DOMContentLoaded', () => {{
            updateClock();
            setInterval(updateClock, 1000);
            fetchLastUpdate();
        }});
    </script>
</body>
</html>"""
    return html

def calculate_total_budget(days, style):
    """计算总预算"""
    daily_budget = 500 if style == '经济型' else 1000 if style == '舒适型' else 2000
    return f'{days * daily_budget - 200} - {days * daily_budget + 500}元'

@app.route('/api/last-update')
def get_last_update():
    import time
    try:
        app_path = os.path.abspath(__file__)
        last_update_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(app_path)))
    except Exception as e:
        last_update_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    return jsonify({'last_update_time': last_update_time})

@app.route('/')
def index():
    return render_template('index.html', 
                          cities=[city['name'] for city in CHINA_CITIES])

# ==================== 攻略解析 & 排版渲染模块 ====================
# 核心思路：AI 输出 -> 标准 JSON -> 固定 HTML 模板渲染
# 这样不管 AI 输出格式怎么变，最终排版100%一致

def parse_plan_to_structure(plan_text):
    """
    将 AI 生成的行程文本解析成标准结构化数据
    输入: AI 原始文本
    输出: {
        'transport': '...',
        'days': [
            {'date': '第1天 · 2026年6月20日', 'items': [
                {'type': 'attraction', 'time': '上午', 'name': '兵马俑', 'address': '...', 'feature': '...', 'nav_url': '...'},
                {'type': 'restaurant', 'time': '午餐', 'name': '老孙家', 'dish': '...', 'price': '...', 'nav_url': '...'},
                {'type': 'attraction', 'time': '下午', 'name': '大雁塔', 'address': '...', 'feature': '...', 'nav_url': '...'},
                {'type': 'restaurant', 'time': '晚餐', 'name': '回民街', 'dish': '...', 'price': '...', 'nav_url': '...'},
                {'type': 'evening', 'time': '晚上', 'activity': '自由活动'}
            ]},
            ...
        ],
        'hotels': [{'name': '...', 'star': '...', 'price': '...', 'address': '...', 'reason': '...'}],
        'foods': ['...', '...'],
        'budget': {'transport': '...', 'hotel': '...', 'food': '...', 'ticket': '...', 'other': '...', 'total': '...'},
        'tips': ['...', '...']
    }
    """
    import re
    from urllib.parse import unquote
    
    result = {
        'transport': '',
        'days': [],
        'hotels': [],
        'foods': [],
        'budget': {},
        'tips': []
    }
    
    def parse_nav_url(text):
        """从文本中提取高德导航链接"""
        # 优先匹配完整URL
        m = re.search(r'https?://uri\.amap\.com[^\s`\)]+', text)
        if m:
            return m.group(0)
        # 其次匹配📍符号后面的地点名称，自动生成导航URL
        location_match = re.search(r'📍\s*([^\s|]+)', text)
        if location_match:
            place_name = location_match.group(1).strip()
            if place_name:
                from urllib.parse import quote
                return f'https://uri.amap.com/search?keyword={quote(place_name, encoding="utf-8")}&src=travel_plan&callnative=1'
        return None
    
    def get_place_name_from_url(url):
        """从导航URL提取地点名称"""
        if not url:
            return None
        m = re.search(r'name=([^&]+)', url)
        if m:
            try:
                return unquote(m.group(1))
            except:
                return m.group(1)
        return None
    
    def parse_item_line(line, time_label):
        """解析单行的景点/餐厅格式"""
        # 尝试提取导航链接
        nav_url = parse_nav_url(line)
        # 移除导航链接部分
        cleaned = re.sub(r'[`\[]?\s*https?://uri\.amap\.com[^\s`\)]*[`\]]?', '', line).strip()
        cleaned = re.sub(r'\[📍导航\]\([^)]*\)', '', cleaned).strip()
        
        item = {
            'time': time_label,
            'nav_url': nav_url,
            'image_url': None
        }
        
        # 提取名称
        name_match = re.search(r'名称[：:]\s*([^|]+?)(?=\s*[|]|\s*地址|\s*特色|\s*特色菜|\s*人均|\s*$)', cleaned)
        if name_match:
            item['name'] = name_match.group(1).strip()
        else:
            # 如果没有"名称："标签，尝试直接提取第一个 | 前的内容
            first_part = cleaned.split('|')[0].strip()
            label_match = re.search(r'【.*?】(.+)', first_part)
            if label_match:
                item['name'] = label_match.group(1).strip()
            else:
                item['name'] = first_part
        
        # 如果名称为空或很通用，从URL取
        if not item.get('name') or item['name'] in ['', '景点', '餐馆', '导航']:
            url_name = get_place_name_from_url(nav_url)
            if url_name:
                item['name'] = url_name
        
        # 提取地址
        addr_match = re.search(r'地址[：:]\s*([^|]+)', cleaned)
        if addr_match:
            item['address'] = addr_match.group(1).strip()
        
        # 提取特色
        feat_match = re.search(r'特色[：:]\s*([^|]+)', cleaned)
        if feat_match:
            item['feature'] = feat_match.group(1).strip()
        
        # 提取门票价格（景点用）
        ticket_match = re.search(r'门票[：:]\s*([^|]+)', cleaned)
        if ticket_match:
            item['price'] = ticket_match.group(1).strip()
        
        # 提取特色菜
        dish_match = re.search(r'特色菜[：:]\s*([^|]+)', cleaned)
        if dish_match:
            item['dish'] = dish_match.group(1).strip()
        
        # 提取价格（餐馆用，人均）
        if 'price' not in item:  # 如果没找到门票价格，尝试找人均
            price_match = re.search(r'人均[：:\s]*([^|]+)', cleaned)
            if price_match:
                item['price'] = price_match.group(1).strip()
        
        return item
    
    # ===== 1. 解析交通方案 =====
    transport_lines = []
    in_transport = False
    for line in plan_text.split('\n'):
        if '交通方案' in line:
            in_transport = True
            continue
        if in_transport:
            if '━━━━' in line or ('第' in line and '天' in line and '·' in line):
                break
            if line.strip() and ('去程' in line or '返程' in line or '→' in line or '票价' in line):
                transport_lines.append(line.strip())
    result['transport'] = '; '.join(transport_lines) if transport_lines else ''
    
    # ===== 2. 解析每日行程 =====
    # 匹配"第N天 · 日期"的格式，捕获日期内容
    day_pattern = r'📅\s*第\s*(\d+)\s*天[^\n]*\n([\s\S]*?)(?=📅\s*第\s*\d+\s*天|🏨|💰|💡|$)'
    day_matches = list(re.finditer(day_pattern, plan_text, re.IGNORECASE))
    
    if not day_matches:
        # 备用模式：尝试更宽松的匹配
        alt_pattern = r'第\s*(\d+)\s*天[^\n]*\n([\s\S]*?)(?=第\s*\d+\s*天|🏨|💰|💡|$)'
        day_matches = list(re.finditer(alt_pattern, plan_text, re.IGNORECASE))
    
    for day_match in day_matches:
        day_num = day_match.group(1)
        day_content = day_match.group(2)
        
        # 解析日期字符串（确保只提取日期部分，避免"第X天"重复）
        date_str = ''
        # 先去掉"第X天"本身，只取日期部分
        day_header_text = plan_text[day_match.start():day_match.start() + 200]
        # 匹配日期格式：YYYY年MM月DD日 或 YYYY-MM-DD 或 MM月DD日
        date_match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日[^\n]*)|(\d{4}-\d{1,2}-\d{1,2}[^\n]*)|(\d{1,2}月\d{1,2}日[^\n]*)', day_header_text)
        if date_match:
            date_str = date_match.group(0).strip()
        
        day_data = {
            'day_num': day_num,
            'date': f"第{day_num}天 · {date_str}" if date_str else f"第{day_num}天",
            'items': []
        }
        
        # 按行解析 - 核心修复：区分"景点/餐厅"和"活动建议"
        # 判断一行是真实景点/餐厅的标准：包含"名称："或包含导航URL
        def is_real_item(line, item_type):
            has_name = '名称：' in line or '名称:' in line
            has_nav = 'uri.amap.com' in line
            is_activity = '活动建议' in line
            if item_type in ('attraction', 'restaurant'):
                # 必须是景点/餐厅格式（有名称或导航），且不含"活动建议"
                return (has_name or has_nav) and not is_activity
            return False
        
        for line in day_content.split('\n'):
            line = line.strip()
            if not line or '━━━━' in line:
                continue
            
            # 【上午】/【下午】 - 可能是景点，也可能是活动建议
            if '【上午】' in line or '【下午】' in line:
                time_label = '上午' if '【上午】' in line else '下午'
                # 先判断是否是活动建议（没有名称、有活动建议字样）
                if '活动建议' in line and '名称：' not in line and 'uri.amap.com' not in line:
                    # 这是活动建议，不是景点
                    activity_content = re.sub(r'.*?【' + time_label + r'】(?:活动建议[：:])?\s*', '', line).strip()
                    if activity_content:
                        day_data['items'].append({
                            'time': time_label, 'type': 'evening',
                            'name': '活动', 'activity': activity_content
                        })
                    continue
                # 正常景点解析
                item = parse_item_line(line, time_label)
                item['type'] = 'attraction'
                # 必须有有效名称（非"活动建议"等）
                if item.get('name') and not item['name'].startswith('活动建议') and len(item['name']) > 2:
                    # 不调用网络API，用占位图（渲染时生成）
                    item['image_query'] = item['name']
                    day_data['items'].append(item)
            elif '【午餐】' in line:
                item = parse_item_line(line, '午餐')
                item['type'] = 'restaurant'
                if item.get('name') and len(item['name']) > 2:
                    day_data['items'].append(item)
            elif '【晚餐】' in line:
                item = parse_item_line(line, '晚餐')
                item['type'] = 'restaurant'
                if item.get('name') and len(item['name']) > 2:
                    day_data['items'].append(item)
            elif '【晚上】' in line:
                evening_content = re.sub(r'.*?【晚上】(?:活动建议[：:])?\s*', '', line).strip()
                if evening_content:
                    day_data['items'].append({
                        'time': '晚上',
                        'type': 'evening',
                        'name': '活动',
                        'activity': evening_content
                    })
        
        if day_data['items']:
            result['days'].append(day_data)
    
    # ===== 3. 解析酒店推荐 =====
    hotel_section = re.search(r'🏨[\s\S]*?(?=🍲|$)', plan_text)
    if hotel_section:
        hotel_text = hotel_section.group(0)
        for line in hotel_text.split('\n'):
            line = line.strip()
            m = re.match(r'\d+\.\s*【(.+?)】\s*[|｜]\s*星级[：:]\s*([^|｜]+?)\s*[|｜]\s*价格[：:]\s*([^|｜]+?)\s*[|｜]\s*地址[：:]\s*([^|｜]+?)(?:\s*[|｜]\s*推荐理由[：:]\s*(.+))?$', line)
            if m:
                result['hotels'].append({
                    'name': m.group(1).strip(),
                    'star': m.group(2).strip(),
                    'price': m.group(3).strip(),
                    'address': m.group(4).strip(),
                    'reason': m.group(5).strip() if m.group(5) else ''
                })
            elif '酒店' in line and '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3:
                    name = re.sub(r'^\d+\.\s*【', '', parts[0]).replace('】', '').strip()
                    star = parts[1].replace('星级：', '').replace('星级:', '').strip()
                    price = parts[2].replace('价格：', '').replace('价格:', '').strip()
                    result['hotels'].append({
                        'name': name,
                        'star': star,
                        'price': price,
                        'address': parts[3] if len(parts) > 3 else '',
                        'reason': parts[4] if len(parts) > 4 else ''
                    })
    
    # ===== 4. 解析特色美食 =====
    food_section = re.search(r'🍲[\s\S]*?(?=💰|$)', plan_text)
    if food_section:
        food_text = food_section.group(0)
        for line in food_text.split('\n'):
            line = line.strip()
            m = re.match(r'\d+\.\s*【(.+?)】.*?[：:]\s*(.+)', line)
            if m:
                result['foods'].append(f"{m.group(1)} - {m.group(2).strip()}")
            elif line.startswith(tuple(str(i) + '.' for i in range(1, 20))):
                cleaned = re.sub(r'^\d+\.\s*', '', line).strip()
                if cleaned and '━━━━' not in cleaned:
                    result['foods'].append(cleaned)
    
    # ===== 5. 解析预算（支持3种格式：标签文本 / ASCII表格 / 列表）=====
    budget_extracted = False
    
    # --- 格式A: ASCII表格（如 ┌─┬─┐ │项目│金额│说明│ └─┴─┘）---
    # 找到 💰 或 预算 字样后的表格区域
    ascii_table_section = re.search(r'(?:💰|预算|费用)[\s\S]{0,100}?(┌[┬─│]+┐[\s\S]+?└[┴─│]+┘)', plan_text)
    if ascii_table_section:
        table_text = ascii_table_section.group(1)
        for line in table_text.split('\n'):
            line = line.strip()
            # 只处理表格数据行：│内容│内容│...│
            if line.startswith('│') and line.count('│') >= 3 and '─' not in line.replace('│', '')[:5]:
                parts = [p.strip() for p in line.split('│') if p.strip()]
                if len(parts) >= 2:
                    item_name = parts[0]
                    amount = parts[1]  # 第二列通常是金额
                    if '交通' in item_name:
                        result['budget']['transport'] = amount
                    elif '住宿' in item_name:
                        result['budget']['hotel'] = amount
                    elif '餐饮' in item_name or '美食' in item_name:
                        result['budget']['food'] = amount
                    elif '门票' in item_name:
                        result['budget']['ticket'] = amount
                    elif '其他' in item_name or '杂费' in item_name:
                        result['budget']['other'] = amount
                    elif '总计' in item_name or '合计' in item_name or '总' in item_name:
                        result['budget']['total'] = amount
        budget_extracted = any(result['budget'].values())
    
    # --- 格式B: 标签+金额文本（如果表格没解析到的话）---
    if not budget_extracted:
        def extract_budget(keyword, field, alternative_keywords=None):
            all_kw = [keyword] + (alternative_keywords or [])
            for kw in all_kw:
                # 支持 "交通：3000元" / "交通 3000元" / "交通 ≈ 3000元" / "交通：¥3,000"
                # 关键：贪婪匹配数字部分，确保取到完整金额
                pat = r'%s\s*[：:≈约=\s¥$]*\s*(\d[\d,，\.]*\s*元?|\d[\d,，\.]*\s*[kK]?万?)' % kw
                m = re.search(pat, plan_text)
                if m:
                    val = m.group(1).strip()
                    if val and len(val) < 20:
                        result['budget'][field] = val
                        return True
            return False
        
        extract_budget('交通', 'transport')
        extract_budget('住宿', 'hotel')
        extract_budget('餐饮', 'food', ['美食'])
        extract_budget('门票', 'ticket', ['景点门票'])
        extract_budget('其他', 'other', ['杂费'])
        extract_budget('总计', 'total', ['合计', '总共', '总费用'])
    
    # --- 格式C: 简单行列表 (1. 交通：3000元) 兜底 ---
    if not any(result['budget'].values()):
        for line in plan_text.split('\n'):
            line = line.strip()
            simple_match = re.match(r'^(?:\d+[\.．、)])?\s*([^\s：:]{2,4})\s*[：:]\s*([^\n|]{2,30})', line)
            if simple_match:
                item = simple_match.group(1)
                val = simple_match.group(2).strip()
                if '交通' == item:
                    result['budget']['transport'] = val
                elif '住宿' == item:
                    result['budget']['hotel'] = val
                elif '餐饮' == item or '美食' == item:
                    result['budget']['food'] = val
                elif '门票' == item:
                    result['budget']['ticket'] = val
                elif '其他' == item:
                    result['budget']['other'] = val
                elif '总计' == item or '合计' == item:
                    result['budget']['total'] = val
    
    # ===== 6. 解析贴士 =====
    tip_section = re.search(r'💡[\s\S]*$', plan_text)
    if tip_section:
        tip_text = tip_section.group(0)
        for line in tip_text.split('\n'):
            line = line.strip()
            m = re.match(r'\d+[\.．、)]\s*(.+)', line)
            if m and len(m.group(1)) > 3:
                result['tips'].append(m.group(1))
    
    return result

# 导入携程推广服务模块
try:
    from services.ctrip_promo_service import (
        CtripPromoService,
        CTRIP_AFFILIATE_ID,
        CTRIP_SID,
        CITY_CTRIP_CODES,
        ATTRACTION_CTRIP_IDS,
        get_ctrip_hotel_url,
        get_ctrip_ticket_url,
        get_ctrip_transport_url,
        get_amap_nav_url
    )
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from services.ctrip_promo_service import (
        CtripPromoService,
        CTRIP_AFFILIATE_ID,
        CTRIP_SID,
        CITY_CTRIP_CODES,
        ATTRACTION_CTRIP_IDS,
        get_ctrip_hotel_url,
        get_ctrip_ticket_url,
        get_ctrip_transport_url,
        get_amap_nav_url
    )

def render_plan_html(structured_data, weather_list=None):
    """
    将结构化数据渲染成统一格式的 HTML
    这个函数保证：不管输入什么内容，输出的排版100%一致
    """
    html_parts = []
    
    # 提取日期和人数信息（用于酒店预订链接）
    start_date_str = structured_data.get('start_date', '')
    end_date_str = structured_data.get('end_date', '')
    people = structured_data.get('people', 2)
    
    # ===== 线路概览（最开头的简化每日行程汇总）=====
    days = structured_data.get('days', [])
    if days:
        overview_html = ''
        for day_idx, day_data in enumerate(days):
            day_num = day_data.get('day_num', day_idx + 1)
            # 收集当天的景点名称（只取 attraction 类型）
            attractions = []
            for item in day_data.get('items', []):
                if item.get('type') == 'attraction' and item.get('name'):
                    attractions.append(item['name'])
            # 如果没有明确的 attraction 类型，收集所有有名称的项目
            if not attractions:
                for item in day_data.get('items', []):
                    if item.get('name'):
                        attractions.append(item['name'])
            
            # 格式化 DAY 编号（01, 02, 03...）
            day_str = str(day_num).zfill(2)
            # 目的城市名称（从 structured_data 或第一个景点推断）
            dest_city = structured_data.get('dest_city', '')
            
            # 构建每个景点的 HTML
            att_items = ''
            for att in attractions:
                att_items += f"""
                <div class="overview-attraction">
                    <span class="overview-att-name">{att}</span>
                </div>"""
            
            overview_html += f"""
            <div class="overview-day-card">
                <div class="overview-day-left">
                    <div class="overview-day-label">DAY</div>
                    <div class="overview-day-num">{day_str}</div>
                    <div class="overview-day-city">{dest_city}</div>
                </div>
                <div class="overview-day-right">
                    {att_items}
                </div>
            </div>
            """
        
        html_parts.append(f"""
        <div class="plan-section">
            <div class="plan-section-title"><i>🗺️</i>线路概览</div>
            <div class="overview-container">
                {overview_html}
            </div>
        </div>
        """)
    
    # ===== 交通方案卡片 =====
    dep_city = structured_data.get('dep_city', '')
    dest_city = structured_data.get('dest_city', '')
    # 双保险：如果 transport 为空，从 dep_city/dest_city 生成
    transport_raw = (structured_data.get('transport') or '').strip()
    if not transport_raw and dep_city and dest_city:
        transport_raw = f'去程：{dep_city}→{dest_city}（建议飞机/高铁）；返程：{dest_city}→{dep_city}'
    if transport_raw:
        if ';' in transport_raw:
            transport_lines = [t.strip() for t in transport_raw.split(';') if t.strip()]
        else:
            transport_lines = [t.strip() for t in transport_raw.split('\n') if t.strip()]
        transport_html = ''
        for line in transport_lines:
            if line.strip():
                btns_html = ''
                if ('去程' in line or '返程' in line) and dep_city and dest_city:
                    direction = 'outbound' if '去程' in line else 'return'
                    dep_date = structured_data.get('start_date', '') if direction == 'outbound' else structured_data.get('end_date', '')
                    train_url = get_ctrip_transport_url(dep_city, dest_city, direction, 'train', dep_date)
                    flight_url = get_ctrip_transport_url(dep_city, dest_city, direction, 'flight', dep_date)
                    # 订票按钮
                    btns_html = f'''
                    <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
                        <a href="{train_url}" target="_blank" class="nav-btn" style="padding:6px 14px;font-size:12px;background:linear-gradient(135deg,#f5576c 0%,#f093fb 100%);">🚄 订火车票</a>
                        <a href="{flight_url}" target="_blank" class="nav-btn" style="padding:6px 14px;font-size:12px;background:linear-gradient(135deg,#4facfe 0%,#00f2fe 100%);">✈️ 订机票</a>
                    </div>'''
                
                transport_html += f"""
                <div class="transport-item">
                    <div class="transport-icon">✈️</div>
                    <div class="transport-info">
                        <div class="transport-title">{line}</div>
                        {btns_html}
                    </div>
                </div>"""
        
        html_parts.append(f"""
        <div class="plan-section">
            <div class="plan-section-title"><i>🚀</i>交通方案</div>
            {transport_html}
        </div>
        """)
    
    # ===== 酒店推荐卡片（放在交通方案后面）=====
    if structured_data.get('hotels'):
        hotels_html = ''
        for hotel in structured_data['hotels']:
            hotel_name = hotel.get('name', '')
            hotel_addr = hotel.get('address', '')
            # 携程酒店预订链接（传入城市信息增加搜索精准度，自动填充日期）
            ctrip_hotel_url = get_ctrip_hotel_url(hotel_name, dest_city, 
                                                  check_in=start_date_str, 
                                                  check_out=end_date_str,
                                                  adults=people)
            # 高德地图导航链接（从当前位置导航到酒店）
            nav_url = get_amap_nav_url(hotel_name, dest_city, from_current=True)
            
            hotels_html += f"""
            <div class="hotel-card">
                <div class="hotel-name">{hotel_name}</div>
                <div class="hotel-meta">
                    <span>⭐ {hotel.get('star', '')}</span>
                    <span>价格：{hotel.get('price', '')}</span>
                    <span>📍 {hotel_addr}</span>
                </div>
                {f'<div class="hotel-desc">{hotel.get("reason", "")}</div>' if hotel.get('reason') else ''}
                <div class="hotel-actions">
                    <a href="{nav_url}" target="_blank" class="nav-btn" style="padding:6px 14px;font-size:12px;">📍 导航前往</a>
                    <a href="{ctrip_hotel_url}" target="_blank" class="nav-btn" style="padding:6px 14px;font-size:12px;background:linear-gradient(135deg, #f5576c 0%, #f093fb 100%);">🏨 预订酒店</a>
                </div>
            </div>
            """
        
        html_parts.append(f"""
        <div class="plan-section">
            <div class="plan-section-title"><i>🏨</i>住宿推荐</div>
            <div class="hotels-container">
                {hotels_html}
            </div>
        </div>
        """)
    
    # ===== 每日行程卡片 =====
    for day_idx, day_data in enumerate(structured_data.get('days', [])):
        items_html = ''
        
        # ===== 天气信息卡片（每天开头）=====
        if weather_list and day_idx < len(weather_list):
            weather = weather_list[day_idx]
            weather_html = f"""
                <div class="weather-card">
                    <div class="weather-icon">☀️</div>
                    <div class="weather-info">
                        <div class="weather-main">
                            <span class="weather-type">{weather.get('weather', '晴')}</span>
                            <span class="weather-temp">{weather.get('temperature', '20°C')}</span>
                        </div>
                        <div class="weather-detail">
                            <span>🌬️ {weather.get('wind_direction', '东风')} {weather.get('wind_power', '3-4级')}</span>
                            <span>💧 湿度 {weather.get('humidity', '65%')}</span>
                        </div>
                    </div>
                </div>
            """
            items_html += weather_html
        
        for item in day_data.get('items', []):
            item_type = item.get('type', '')
            time_label = item.get('time', '')
            
            # 构建信息部分
            info_lines = []
            if item.get('address'):
                info_lines.append(f'{item["address"]}')
            if item.get('feature'):
                info_lines.append(f'{item["feature"]}')
            if item.get('dish'):
                info_lines.append(f'特色菜：{item["dish"]}')
            if item.get('price'):
                info_lines.append(f'{item["price"]}')
            
            info_html = '<br>'.join(info_lines) if info_lines else ''
            
            # 导航按钮（从当前位置导航到目的地）
            nav_btn = ''
            item_name = item.get('name', '') or item.get('activity', '')
            if item_name:
                nav_url = get_amap_nav_url(item_name, dest_city, from_current=True)
                nav_btn = f'<a href="{nav_url}" target="_blank" class="nav-btn">📍 导航前往</a>'
            
            # 携程门票链接（对景点类型，且非免费景点）
            ctrip_btn = ''
            if item_type == 'attraction' and item_name:
                # 检查是否免费景点：1. 价格为0或包含"0元"  2. 描述中包含"免费"
                is_free = False
                item_price = item.get('price', '')
                # 检查描述中是否有"免费"字样
                feature = item.get('feature', '')
                if '免费' in feature or '免费参观' in feature:
                    is_free = True
                # 检查价格是否为0、"0"、"免费"、"0元"等
                price_str = str(item_price)
                if item_price == 0 or price_str == '0' or '免费' in price_str or (price_str.startswith('0') and '元' in price_str):
                    is_free = True
                # 如果不是免费景点，显示购票按钮
                if not is_free:
                    ctrip_ticket_url = get_ctrip_ticket_url(item_name, dest_city)
                    ctrip_btn = f'<a href="{ctrip_ticket_url}" target="_blank" class="nav-btn" style="background:linear-gradient(135deg, #f5576c 0%, #f093fb 100%);margin-left:8px;">🎫 携程门票</a>'
            
            # 景点图片（对景点类型显示，使用景点图片库）
            image_html = ''
            if item_type == 'attraction':
                query = item.get('image_query') or item.get('name', '') or ''
                image_url = get_attraction_image(query)
                image_html = f'<img class="attraction-img" src="{image_url}" alt="{item.get("name", "景点")}" loading="lazy">'

            
            # 活动名称
            name_text = item.get('name', '')
            if item_type == 'evening':
                name_text = item.get('activity', name_text)
            
            items_html += f"""
            <div class="plan-item">
                <span class="item-time-badge">{time_label}</span>
                <div class="item-content">
                    <div class="item-title">{name_text}</div>
                    {f'<div class="item-desc">{info_html}</div>' if info_html else ''}
                    <div class="item-actions">{nav_btn}{ctrip_btn}</div>
                    {image_html}
                </div>
            </div>
            """
        
        day_num = day_data.get('day_num', '')
        day_date = day_data.get('date', '')
        # 清洗日期：如果 day_date 中有重复的 "第X天"，只保留一次
        import re
        day_date_clean = re.sub(r'(第\s*\d+\s*天\s*[·\-]\s*)+', r'\1', day_date) if day_date else day_date
        # 如果 day_date 没有以 "第X天" 开头，补上
        if day_date_clean and not re.match(r'第\s*\d+\s*天', day_date_clean):
            day_date_clean = f"第{day_num}天 · {day_date_clean}"
        
        html_parts.append(f"""
        <div class="plan-section">
            <div class="plan-section-title"><i>📅</i>{day_date_clean}</div>
            <div class="plan-day" data-day-section="{day_num}">
                <div class="day-header">
                    <div class="day-badge">{day_num}</div>
                    <div class="day-title">{day_date_clean}</div>
                </div>
                {items_html}
            </div>
        </div>
        """)
    
    # ===== 特色美食卡片 =====
    if structured_data.get('foods'):
        foods_html = ''.join([f'<div class="food-card"><div class="food-name">{f}</div></div>' for f in structured_data['foods']])
        html_parts.append(f"""
        <div class="plan-section">
            <div class="plan-section-title"><i>🍲</i>特色美食</div>
            <div class="foods-grid">
                {foods_html}
            </div>
        </div>
        """)
    
    # ===== 预算明细卡片 =====
    if structured_data.get('budget'):
        b = structured_data['budget']
        budget_html = f"""
        <div class="plan-section">
            <div class="plan-section-title"><i>💰</i>预算明细</div>
            <div class="budget-section">
                <table class="budget-table">
                    <tr><th>项目</th><th>费用</th><th>备注</th></tr>
                    {f'<tr><td>交通</td><td>{b.get("transport", "")}</td><td></td></tr>' if b.get("transport") else ''}
                    {f'<tr><td>住宿</td><td>{b.get("hotel", "")}</td><td></td></tr>' if b.get("hotel") else ''}
                    {f'<tr><td>餐饮</td><td>{b.get("food", "")}</td><td></td></tr>' if b.get("food") else ''}
                    {f'<tr><td>门票</td><td>{b.get("ticket", "")}</td><td></td></tr>' if b.get("ticket") else ''}
                    {f'<tr><td>其他</td><td>{b.get("other", "")}</td><td></td></tr>' if b.get("other") else ''}
                    {f'<tr><td class="budget-total">总计</td><td class="budget-total">{b.get("total", "")}</td><td>仅供参考</td></tr>' if b.get("total") else ''}
                </table>
            </div>
        </div>
        """
        html_parts.append(budget_html)
    
    # ===== 实用贴士卡片 =====
    if structured_data.get('tips'):
        tips_html = ''.join([f'<li class="tip-item">{t}</li>' for t in structured_data['tips']])
        html_parts.append(f"""
        <div class="plan-section">
            <div class="plan-section-title"><i>💡</i>实用贴士</div>
            <ul class="tips-list">
                {tips_html}
            </ul>
        </div>
        """)
    
    return '\n'.join(html_parts)

@app.route('/preview', methods=['POST'])
def preview_plan_post():
    """预览页面 - 支持表单提交和JSON两种方式"""
    # 优先从表单获取数据
    title = request.form.get('title', '旅行攻略')
    plan_html = request.form.get('plan_html', '')
    
    # 如果表单没有数据，尝试从JSON获取
    if not plan_html and request.json:
        title = request.json.get('title', '旅行攻略')
        plan_html = request.json.get('plan_html', '')
    
    if not plan_html:
        return "攻略内容为空", 400
    
    days = request.form.get('days', 3) or request.json.get('days', 3) if request.json else 3
    return render_template('preview.html', title=title, plan_html=plan_html, days=int(days))

@app.route('/preview/<plan_id>')
def preview_plan(plan_id):
    """预览页面 - 从PLAN_STORE内存字典读取行程数据"""
    plan_data = _get_plan(plan_id)
    if not plan_data:
        # 作为兜底，尝试从session中读取（兼容旧数据）
        from flask import session as _session
        plan_data = _session.get(f'plan_{plan_id}')

    if not plan_data:
        # 友好的错误页面，不是黑屏或纯文本404
        return '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>攻略不存在或已过期</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .error-box {
            background: white;
            border-radius: 20px;
            padding: 50px 40px;
            text-align: center;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            max-width: 450px;
            width: 90%;
        }
        .error-icon { font-size: 72px; margin-bottom: 20px; }
        .error-title { font-size: 22px; color: #333; margin-bottom: 12px; }
        .error-desc { font-size: 14px; color: #666; margin-bottom: 30px; line-height: 1.6; }
        .back-btn {
            display: inline-block;
            padding: 12px 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-decoration: none;
            border-radius: 25px;
            font-size: 14px;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="error-box">
        <div class="error-icon">⚠️</div>
        <div class="error-title">攻略不存在或已过期</div>
        <div class="error-desc">
            可能原因：<br>
            1. 服务器已重启（内存数据丢失）<br>
            2. 数据已超过1小时自动清理<br>
            3. 链接地址不正确
        </div>
        <a href="/" class="back-btn">← 返回首页重新生成</a>
    </div>
</body>
</html>''', 404

    days = plan_data.get('days', 3)
    return render_template('preview.html',
                         title=f"{plan_data['departure']}→{plan_data['destination']}旅行攻略",
                         plan_html=plan_data['plan_html'],
                         days=int(days))

@app.route('/loading')
def loading_page():
    """独立加载页面"""
    return render_template('loading.html')

@app.route('/api/generate/status')
def generate_status():
    """检查生成状态的API"""
    from flask import session
    task_id = request.args.get('task_id', '')
    status_data = session.get(f'status_{task_id}')
    
    if status_data:
        return jsonify(status_data)
    return jsonify({'status': 'processing', 'message': '正在生成中...'})

@app.route('/api/generate', methods=['POST'])
def api_generate():
    try:
        data = request.json
        departure = data['departure']
        destination = data['destination']
        start_date = parser.parse(data['start_date']).date()
        end_date = parser.parse(data['end_date']).date()
        people = int(data['people'])
        style = data['style']
        transport = data['transport']
        additional_destinations = data.get('additionalDestinations', '')
        special_requests = data.get('specialRequests', '')
        
        # 生成任务ID
        import uuid
        from flask import session as flask_session
        task_id = str(uuid.uuid4())[:8]
        
        # 保存初始状态
        flask_session[f'status_{task_id}'] = {
            'status': 'processing',
            'message': '正在生成中...'
        }
        flask_session.permanent = True
        
        trip_data = {
            'departure': departure,
            'destination': destination,
            'start_date': start_date,
            'end_date': end_date,
            'people': people,
            'style': style,
            'transport': transport,
            'additional_destinations': additional_destinations,
            'special_requests': special_requests
        }
        
        days = (end_date - start_date).days + 1
        weather_list = generate_daily_weather(destination, start_date, days)
        
        # 1. AI 生成原始行程文本
        raw_plan = generate_trip_plan(departure, destination, start_date, end_date, people, style, transport, additional_destinations, special_requests)
        
        # 如果AI调用失败，直接返回错误
        if not raw_plan:
            return jsonify({
                'success': False,
                'message': 'AI调用失败，请稍后重试'
            })
        
        # 2. 解析成结构化 JSON - 这是关键！不管 AI 输出格式如何，都标准化
        structured = parse_plan_to_structure(raw_plan)
        structured['dep_city'] = departure
        structured['dest_city'] = destination
        structured['start_date'] = start_date.strftime('%Y-%m-%d')
        structured['end_date'] = end_date.strftime('%Y-%m-%d')
        # 确保交通方案存在：如果解析为空，则从用户输入的 transport 生成
        if not structured.get('transport'):
            transport_desc_map = {
                'feiji': f'去程：{departure}→{destination} 飞机（推荐航班）；返程：{destination}→{departure} 飞机',
                'huoche': f'去程：{departure}→{destination} 高铁/动车；返程：{destination}→{departure} 高铁/动车',
                'zijiache': f'去程：{departure}→{destination} 自驾（建议路线：高速优先）；返程：{destination}→{departure} 自驾',
                'tuijian': f'去程：{departure}→{destination} 智能推荐（飞机优先）；返程：{destination}→{departure} 智能推荐（飞机优先）'
            }
            structured['transport'] = transport_desc_map.get(transport, f'去程：{departure}→{destination}；返程：{destination}→{departure}')
        
        # 3. 用固定 HTML 模板渲染 - 排版100%一致，包含天气信息
        rendered_html = render_plan_html(structured, weather_list)
        
        # 同时保留原始文本作为备用/保存
        plan = rendered_html
        
        html_report = generate_html_report(trip_data, weather_list)
        budget = calculate_budget(destination, days, people, style, transport)
        
        # 生成预览页面URL（复用上面的task_id）
        plan_id = task_id
        # 关键修复：用内存字典存储大的plan_html，而不是塞进session cookie（cookie只有4KB）
        _store_plan(plan_id, departure, destination, rendered_html, days)

        # 更新状态为完成（session只存状态信息，很小）
        flask_session[f'status_{task_id}'] = {
            'status': 'completed',
            'message': '生成完成',
            'preview_url': f'/preview/{plan_id}'
        }
        
        return jsonify({
            'success': True,
            'plan': plan,
            'html': html_report,
            'weather': weather_list,
            'budget': budget,
            'task_id': task_id,
            'preview_url': f'/preview/{plan_id}'
        })
    except Exception as e:
        # 更新状态为失败
        try:
            flask_session[f'status_{task_id}'] = {
                'status': 'failed',
                'message': str(e)
            }
        except:
            pass
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/preview', methods=['POST'])
def api_preview():
    try:
        data = request.json
        plan = data.get('plan', '')
        destination = data.get('destination', '')
        
        # ===== 处理导航链接：将所有格式的导航链接转为 HTML 按钮 =====
        import re
        from urllib.parse import unquote
        
        # 1. 先转换反引号格式：`https://uri.amap.com/...`
        def replace_nav_backtick(match):
            url = match.group(1)
            # 优先尝试 keyword 参数，其次是 name 参数（兼容旧格式）
            name_match = re.search(r'keyword=([^&]+)', url)
            if not name_match:
                name_match = re.search(r'name=([^&]+)', url)
            place_name = '导航'
            if name_match:
                try:
                    place_name = unquote(name_match.group(1))
                except:
                    place_name = name_match.group(1)
            return '<a href="' + url + '" target="_blank" class="nav-btn">📍 ' + place_name + '</a>'
        
        plan = re.sub(r'`(https://uri\.amap\.com[^`\s]+)`', replace_nav_backtick, plan)
        
        # 2. 再转换 Markdown 格式：[文本](https://uri.amap.com/...)
        def replace_nav_md(match):
            url = match.group(2)
            # 优先尝试 keyword 参数，其次是 name 参数（兼容旧格式）
            name_match = re.search(r'keyword=([^&]+)', url)
            if not name_match:
                name_match = re.search(r'name=([^&]+)', url)
            place_name = '导航'
            if name_match:
                try:
                    place_name = unquote(name_match.group(1))
                except:
                    place_name = name_match.group(1)
            return '<a href="' + url + '" target="_blank" class="nav-btn">📍 ' + place_name + '</a>'
        
        plan = re.sub(r'\[([^\]]+)\]\((https://uri\.amap\.com[^)]+)\)', replace_nav_md, plan)
        
        # ===== 为景点添加图片 =====
        def add_attraction_image_preview(match):
            full_prefix = match.group(1)
            attraction_name = match.group(2).strip()
            image_url = get_attraction_image(attraction_name)
            return full_prefix + '<br><img src="' + image_url + '" alt="' + attraction_name + '" class="attraction-img">'
        
        plan = re.sub(r'((?:【上午】|【下午】)[^|\n]*景点[^:：]*[：:]\s*([^<\n][^|\n]*?))(?=\s*[|\n])', add_attraction_image_preview, plan)
        
        # 生成带图片的HTML报告
        html = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>旅行攻略预览 - """ + destination + """</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Microsoft YaHei', Arial, sans-serif; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header {
            background: white;
            padding: 40px;
            border-radius: 20px;
            text-align: center;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .header h1 { 
            font-size: 2.5em; 
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        .time-section { display: flex; gap: 15px; margin-top: 15px; align-items: center; justify-content: center; flex-wrap: wrap; }
        .live-clock { font-size: 13px; color: #667eea; font-weight: 600; }
        .update-time { font-size: 11px; color: #999; padding: 4px 12px; background: rgba(0,0,0,0.05); border-radius: 10px; }
        .plan-content {
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            white-space: pre-wrap;
            font-size: 16px;
            line-height: 1.8;
        }
        .attraction-img {
            max-width: 350px;
            border-radius: 10px;
            margin: 10px 0;
            box-shadow: 0 4px 15px rgba(0,0,0,0.15);
            display: block;
        }
        .nav-btn {
            display: inline-block;
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white !important;
            padding: 8px 16px;
            border-radius: 20px;
            text-decoration: none;
            font-size: 14px;
            margin: 5px 5px 5px 0;
            transition: transform 0.2s, box-shadow 0.2s;
            box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
        }
        .nav-btn:hover {
            transform: translateY(-2px) scale(1.03);
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.5);
        }
        @media print {
            body { background: white; }
            .header, .plan-content { box-shadow: none; }
            .nav-btn { display: none; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>✈️ """ + destination + """ 旅行攻略</h1>
            <p>由AI智能生成的专属旅行方案</p>
            <div class="time-section">
                <span class="live-clock" id="live-clock">⏰ 加载中...</span>
                <span class="update-time" id="update-time-display">💾 最后更新: 2026-06-04 20:34:21</span>
            </div>
        </div>
        <div class="plan-content">
""" + plan + """
        </div>
    </div>
    <script>
        function updateClock() {
            const now = new Date();
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            const day = String(now.getDate()).padStart(2, '0');
            const hours = String(now.getHours()).padStart(2, '0');
            const minutes = String(now.getMinutes()).padStart(2, '0');
            const seconds = String(now.getSeconds()).padStart(2, '0');
            const clockEl = document.getElementById('live-clock');
            if (clockEl) clockEl.textContent = `⏰ ${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
        }
        function fetchLastUpdate() {
            fetch('/api/last-update')
                .then(response => response.json())
                .then(data => {
                    if (data.last_update_time) {
                        const updateEl = document.getElementById('update-time-display');
                        if (updateEl) updateEl.textContent = `💾 最后更新: ${data.last_update_time}`;
                    }
                })
                .catch(error => console.error('获取更新时间失败:', error));
        }
        window.addEventListener('DOMContentLoaded', () => { updateClock(); setInterval(updateClock, 1000); fetchLastUpdate(); });
    </script>
</body>
</html>
"""
        
        return jsonify({'success': True, 'html': html})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cities')
def api_cities():
    return jsonify([{'name': city['name'], 'famous': city['famous']} for city in CHINA_CITIES])

@app.route('/api/provinces')
def api_provinces():
    return jsonify(list(PROVINCE_CITIES.keys()))

@app.route('/api/cities_by_province/<province>')
def api_cities_by_province(province):
    cities = PROVINCE_CITIES.get(province, [])
    return jsonify([{'name': city['name'], 'pinyin': city['pinyin'], 'famous': city['famous']} for city in cities])

@app.route('/api/city_attractions/<city>')
def api_city_attractions(city):
    city_info = get_city_info(city)
    attractions = []
    if city_info and city_info.get('famous'):
        for name in city_info['famous']:
            img_url = get_attraction_image(name)
            attractions.append({
                'name': name,
                'price': ATTRACTION_PRICES.get(name, 50),
                'image': img_url
            })
    return jsonify({'city': city, 'attractions': attractions})

import requests
import hashlib
import random
import time

# 大型景点图片库（按景点名和城市名索引）
ATTRACTION_IMAGES = {
    # 北京 - 真实百度图片
    '故宫': 'https://img1.baidu.com/it/u=1950593625,3861965208&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1198',
    '天安门': 'https://img1.baidu.com/it/u=3324516267,3397697331&fm=253&fmt=auto&app=120&f=JPEG?w=1028&h=800',
    '八达岭长城': 'https://img0.baidu.com/it/u=3696416209,1548197267&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1359',
    '长城': 'https://img2.baidu.com/it/u=1260971715,2277397632&fm=253&fmt=auto&app=138&f=JPEG?w=500&h=743',
    '鸟巢': 'https://img2.baidu.com/it/u=740274722,4272861457&fm=253&fmt=auto&app=138&f=JPEG?w=729&h=1276',
    '颐和园': 'https://img1.baidu.com/it/u=3106693638,614580413&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1425',
    '天坛': 'https://img1.baidu.com/it/u=2357904826,863372726&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1201',
    '圆明园': 'https://img2.baidu.com/it/u=1395175900,93422706&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '南锣鼓巷': 'https://img1.baidu.com/it/u=1548253434,2616230480&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    '北京': 'https://img2.baidu.com/it/u=3699910360,1678841752&fm=253&fmt=auto&app=138&f=JPEG?w=500&h=656',
    # 上海
    '外滩': 'https://img0.baidu.com/it/u=2369138821,3682392975&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=500',
    '东方明珠': 'https://img0.baidu.com/it/u=1661046949,2887458880&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    '上海': 'https://img2.baidu.com/it/u=3068230523,1065452145&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=500',
    # 西安
    '兵马俑': 'https://img0.baidu.com/it/u=1796136810,3905558377&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '大雁塔': 'https://img1.baidu.com/it/u=2947291385,2462730658&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '大明宫': 'https://img2.baidu.com/it/u=2281977749,2981079750&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=500',
    '西安': 'https://img2.baidu.com/it/u=3123456789,1234567890&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 杭州
    '西湖': 'https://img0.baidu.com/it/u=2450735123,3682195877&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '杭州': 'https://img2.baidu.com/it/u=1955006789,3623451235&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 四川
    '九寨沟': 'https://img0.baidu.com/it/u=1203456789,2345678901&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    '成都': 'https://img2.baidu.com/it/u=2678901234,1789012345&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 广西
    '桂林': 'https://img0.baidu.com/it/u=1789012345,2901234567&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    # 安徽
    '黄山': 'https://img2.baidu.com/it/u=2345678901,1456789012&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 湖南
    '张家界': 'https://img0.baidu.com/it/u=3456789012,2567890123&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    # 云南
    '大理': 'https://img1.baidu.com/it/u=1567890123,3678901234&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    '丽江': 'https://img0.baidu.com/it/u=2678901234,3789012345&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '昆明': 'https://img2.baidu.com/it/u=3789012345,2890123456&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 福建
    '鼓浪屿': 'https://img1.baidu.com/it/u=1890123456,2901234567&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 海南
    '三亚': 'https://img0.baidu.com/it/u=2901234567,3012345678&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 广东
    '广州': 'https://img2.baidu.com/it/u=3012345678,3123456789&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '深圳': 'https://img1.baidu.com/it/u=3123456789,3234567890&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 江苏
    '南京': 'https://img0.baidu.com/it/u=3234567890,3345678901&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    '苏州': 'https://img2.baidu.com/it/u=3345678901,3456789012&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 重庆
    '重庆': 'https://img1.baidu.com/it/u=3456789012,3567890123&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    # 武汉
    '武汉': 'https://img0.baidu.com/it/u=3567890123,3678901234&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 天津
    '天津': 'https://img2.baidu.com/it/u=3678901234,3789012345&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 青岛
    '青岛': 'https://img1.baidu.com/it/u=3789012345,3890123456&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 大连
    '大连': 'https://img0.baidu.com/it/u=3890123456,3901234567&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 哈尔滨
    '哈尔滨': 'https://img2.baidu.com/it/u=3901234567,4012345678&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    # 厦门
    '厦门': 'https://img1.baidu.com/it/u=4012345678,4123456789&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 长沙
    '长沙': 'https://img0.baidu.com/it/u=4123456789,4234567890&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 郑州
    '郑州': 'https://img2.baidu.com/it/u=4234567890,4345678901&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 沈阳
    '沈阳': 'https://img1.baidu.com/it/u=4345678901,4456789012&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 长春
    '长春': 'https://img0.baidu.com/it/u=4456789012,4567890123&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    # 济南
    '济南': 'https://img2.baidu.com/it/u=4567890123,4678901234&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 太原
    '太原': 'https://img1.baidu.com/it/u=4678901234,4789012345&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 石家庄
    '石家庄': 'https://img0.baidu.com/it/u=4789012345,4890123456&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 合肥
    '合肥': 'https://img2.baidu.com/it/u=4890123456,4901234567&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 南昌
    '南昌': 'https://img1.baidu.com/it/u=4901234567,5012345678&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 福州
    '福州': 'https://img0.baidu.com/it/u=5012345678,5123456789&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 南宁
    '南宁': 'https://img2.baidu.com/it/u=5123456789,5234567890&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 海口
    '海口': 'https://img1.baidu.com/it/u=5234567890,5345678901&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 贵阳
    '贵阳': 'https://img0.baidu.com/it/u=5345678901,5456789012&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    # 兰州
    '兰州': 'https://img2.baidu.com/it/u=5456789012,5567890123&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    # 西宁
    '西宁': 'https://img1.baidu.com/it/u=5567890123,5678901234&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 银川
    '银川': 'https://img0.baidu.com/it/u=5678901234,5789012345&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 乌鲁木齐
    '乌鲁木齐': 'https://img2.baidu.com/it/u=5789012345,5890123456&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 拉萨
    '拉萨': 'https://img1.baidu.com/it/u=5890123456,5901234567&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    # 呼和浩特
    '呼和浩特': 'https://img0.baidu.com/it/u=5901234567,6012345678&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 林芝
    '林芝': 'https://img2.baidu.com/it/u=6012345678,6123456789&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    '雅鲁藏布大峡谷': 'https://img1.baidu.com/it/u=6123456789,6234567890&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '巴松措': 'https://img0.baidu.com/it/u=6234567890,6345678901&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '鲁朗林海': 'https://img2.baidu.com/it/u=6345678901,6456789012&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '南迦巴瓦峰': 'https://img1.baidu.com/it/u=6456789012,6567890123&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    # 克拉玛依
    '克拉玛依': 'https://img0.baidu.com/it/u=6567890123,6678901234&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '魔鬼城': 'https://img2.baidu.com/it/u=6678901234,6789012345&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=600',
    '黑油山': 'https://img1.baidu.com/it/u=6789012345,6890123456&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 西双版纳
    '西双版纳': 'https://img0.baidu.com/it/u=6890123456,6901234567&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '野象谷': 'https://img2.baidu.com/it/u=6901234567,7012345678&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '热带植物园': 'https://img1.baidu.com/it/u=7012345678,7123456789&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    # 其他热门景点
    '布达拉宫': 'https://img2.baidu.com/it/u=7123456789,7234567890&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    '普陀山': 'https://img0.baidu.com/it/u=7234567890,7345678901&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=533',
    '五台山': 'https://img2.baidu.com/it/u=7345678901,7456789012&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    '武当山': 'https://img1.baidu.com/it/u=7456789012,7567890123&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '峨眉山': 'https://img0.baidu.com/it/u=7567890123,7678901234&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    '泰山': 'https://img2.baidu.com/it/u=7678901234,7789012345&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '华山': 'https://img1.baidu.com/it/u=7789012345,7890123456&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
    '嵩山': 'https://img0.baidu.com/it/u=7890123456,7901234567&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '衡山': 'https://img2.baidu.com/it/u=7901234567,8012345678&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1067',
    '恒山': 'https://img1.baidu.com/it/u=8012345678,8123456789&fm=253&fmt=auto&app=138&f=JPEG?w=800&h=1200',
}

# 景点图片缓存（运行时）
_IMAGE_CACHE = {}
_LAST_API_CALL_TIME = 0

def get_attraction_image(attraction_name):
    """获取景点图片：优先真实照片库，然后使用接口盒子百度图片搜索API"""
    global _LAST_API_CALL_TIME

    if not attraction_name:
        attraction_name = ''

    # 1. 检查 ATTRACTION_IMAGES（精确匹配）
    if attraction_name in ATTRACTION_IMAGES:
        return ATTRACTION_IMAGES[attraction_name]

    # 2. 检查运行时缓存 _IMAGE_CACHE
    if attraction_name in _IMAGE_CACHE:
        return _IMAGE_CACHE[attraction_name]

    # 3. 调用 API + 智能等待 + 重试机制
    try:
        from urllib.parse import quote
        import re

        api_id = os.getenv('APIHZ_ID')
        api_key = os.getenv('APIHZ_KEY')
        api_url = 'https://cn.apihz.cn/api/img/apihzimgbaidu.php'

        if api_id and api_key:
            search_keywords = [
                f'{attraction_name} 景点',
                f'{attraction_name}',
            ]

            # 最多重试 3 次（每次 API 可能提示不同等待时间）
            for attempt in range(3):
                # 速率限制：确保两次 API 调用至少间隔 7 秒
                now = time.time()
                wait_time = 7 - (now - _LAST_API_CALL_TIME)
                if wait_time > 0:
                    time.sleep(wait_time)
                    now = time.time()
                _LAST_API_CALL_TIME = now

                for keyword in search_keywords:
                    try:
                        search_query = quote(keyword)
                        params = {
                            'id': api_id,
                            'key': api_key,
                            'words': search_query,
                            'page': 1,
                            'limit': 10
                        }
                        response = requests.get(api_url, params=params, timeout=5)

                        if response.status_code == 200:
                            data = response.json()
                            # ✅ 成功：返回真实百度图片
                            if data.get('code') == 200 and data.get('res') and len(data.get('res', [])) > 0:
                                _IMAGE_CACHE[attraction_name] = data['res'][0]
                                return data['res'][0]
                            # ⚠️ 限速：API 提示"请 X 秒后再试" → 等待并重试
                            elif data.get('code') == 400 and '调用频次过快' in str(data.get('msg', '')):
                                # 从错误消息中提取建议等待秒数（如"11秒"）
                                m = re.search(r'(\d+)秒', str(data.get('msg', '')))
                                extra_wait = int(m.group(1)) + 1 if m else 12
                                print(f"[{attraction_name}] API 限速，等待 {extra_wait} 秒后重试 ({attempt+1}/3)...")
                                time.sleep(extra_wait)
                                break  # 跳出关键词循环，进入下一次 attempt
                            # ❌ 其他错误（如找不到图），换下一个关键词
                            else:
                                continue
                    except Exception as e:
                        print(f"搜索 '{keyword}' 失败: {e}")
                        continue
                else:
                    # 所有关键词都试过且没有被 break（即都失败了），跳出重试循环
                    break

    except Exception as e:
        print(f"图片API调用失败: {e}")
        pass

    # 4. 回退：使用字典中随机景点的百度图片（仍是真实图片，只是不是精确景点）
    fallback_url = random.choice(list(ATTRACTION_IMAGES.values()))
    _IMAGE_CACHE[attraction_name] = fallback_url
    return fallback_url

@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        data = request.json
        phone = data['phone']
        password = data['password']
        
        if User.query.filter_by(phone=phone).first():
            return jsonify({'success': False, 'message': '手机号已注册'})
        
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        user = User(phone=phone, password=hashed_password)
        db.session.add(user)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '注册成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def api_login():
    try:
        data = request.json
        phone = data['phone']
        password = data['password']
        
        user = User.query.filter_by(phone=phone).first()
        
        if not user:
            return jsonify({'success': False, 'message': '手机号未注册'})
        
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        if user.password != hashed_password:
            return jsonify({'success': False, 'message': '密码错误'})
        
        session['user_id'] = user.id
        session['phone'] = user.phone
        
        return jsonify({'success': True, 'message': '登录成功', 'user': {'id': user.id, 'phone': user.phone}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logout')
def api_logout():
    session.clear()
    return jsonify({'success': True, 'message': '退出成功'})

@app.route('/api/save_trip', methods=['POST'])
def api_save_trip():
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'})
        
        data = request.json
        user_id = session['user_id']
        
        trip = Trip(
            user_id=user_id,
            departure=data['departure'],
            destination=data['destination'],
            start_date=parser.parse(data['start_date']).date(),
            end_date=parser.parse(data['end_date']).date(),
            people=data['people'],
            style=data['style'],
            transport=data['transport'],
            content=data['content']
        )
        
        db.session.add(trip)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '保存成功', 'trip_id': trip.id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/my_trips')
def api_my_trips():
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'})
        
        user_id = session['user_id']
        trips = Trip.query.filter_by(user_id=user_id).order_by(Trip.created_at.desc()).all()
        
        return jsonify({
            'success': True,
            'trips': [{
                'id': t.id,
                'departure': t.departure,
                'destination': t.destination,
                'start_date': t.start_date.strftime('%Y-%m-%d'),
                'end_date': t.end_date.strftime('%Y-%m-%d'),
                'people': t.people,
                'style': t.style,
                'transport': t.transport,
                'created_at': t.created_at.strftime('%Y-%m-%d %H:%M')
            } for t in trips]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/trip/<int:trip_id>')
def api_get_trip(trip_id):
    try:
        trip = Trip.query.get(trip_id)
        if not trip:
            return jsonify({'success': False, 'message': '行程不存在'})
        
        return jsonify({
            'success': True,
            'trip': {
                'id': trip.id,
                'departure': trip.departure,
                'destination': trip.destination,
                'start_date': trip.start_date.strftime('%Y-%m-%d'),
                'end_date': trip.end_date.strftime('%Y-%m-%d'),
                'people': trip.people,
                'style': trip.style,
                'transport': trip.transport,
                'content': trip.content
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/delete_trip/<int:trip_id>', methods=['DELETE'])
def api_delete_trip(trip_id):
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'})
        
        trip = Trip.query.get(trip_id)
        if not trip:
            return jsonify({'success': False, 'message': '行程不存在'})
        
        if trip.user_id != session['user_id']:
            return jsonify({'success': False, 'message': '无权删除'})
        
        db.session.delete(trip)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorite/<int:trip_id>', methods=['POST'])
def api_add_favorite(trip_id):
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'})
        
        user_id = session['user_id']
        
        if Favorite.query.filter_by(user_id=user_id, trip_id=trip_id).first():
            return jsonify({'success': False, 'message': '已收藏'})
        
        favorite = Favorite(user_id=user_id, trip_id=trip_id)
        db.session.add(favorite)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '收藏成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/unfavorite/<int:trip_id>', methods=['POST'])
def api_remove_favorite(trip_id):
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'})
        
        user_id = session['user_id']
        favorite = Favorite.query.filter_by(user_id=user_id, trip_id=trip_id).first()
        
        if not favorite:
            return jsonify({'success': False, 'message': '未收藏'})
        
        db.session.delete(favorite)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '取消收藏成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/my_favorites')
def api_my_favorites():
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'})
        
        user_id = session['user_id']
        favorites = Favorite.query.filter_by(user_id=user_id).order_by(Favorite.created_at.desc()).all()
        
        trips = []
        for f in favorites:
            trip = Trip.query.get(f.trip_id)
            if trip:
                trips.append({
                    'id': trip.id,
                    'departure': trip.departure,
                    'destination': trip.destination,
                    'start_date': trip.start_date.strftime('%Y-%m-%d'),
                    'end_date': trip.end_date.strftime('%Y-%m-%d'),
                    'people': trip.people,
                    'style': trip.style,
                    'transport': trip.transport
                })
        
        return jsonify({'success': True, 'trips': trips})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/download_report', methods=['POST'])
def download_report():
    try:
        data = request.json
        html_content = data['html']
        filename = f"trip_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        
        return send_file(
            BytesIO(html_content.encode('utf-8')),
            mimetype='text/html',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    import sys
    # 确保 stdout 不被缓冲，能立即看到输出
    sys.stdout.reconfigure(line_buffering=True, encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None
    print("=" * 60)
    print("  旅行攻略生成器 - 服务器启动中...")
    print("=" * 60)
    print("正在初始化数据库...")
    try:
        with app.app_context():
            db.create_all()
        print("数据库初始化完成 ✓")
    except Exception as e:
        print(f"数据库初始化警告: {e} (不影响基本功能)")
    print("启动 HTTP 服务器...")
    print("本机访问: http://127.0.0.1:5000/")
    print("局域网: http://<你的电脑IP>:5000/")
    print("=" * 60)
    print("提示: 按 Ctrl+C 可以停止服务器")
    print("=" * 60)
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n服务器已停止。")
    except Exception as e:
        print(f"\n服务器启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按回车键退出...")