from urllib.parse import quote
import logging
import time
import json
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# ---- 坐标缓存：避免重复查询同一个地点 ----
_GEOCODE_CACHE = {}  # key: "城市|地点名" -> (lng, lat, timestamp)
_CACHE_TTL = 7 * 24 * 3600  # 7 天内缓存有效
_AMAP_API_KEY = None  # 由 app.py 初始化，可在后台配置页面设置

class CtripPromoService:
    """携程推广链接服务类 - 支持自动填充和导航"""
    
    _PROMO_PARAMS = {
        'ouid': 'kfptpctool',
        'allianceid': '8212891',
        'sid': '310488046'
    }
    
    # 城市拼音映射（用于携程URL参数）
    _CITY_PINYIN = {
        '北京': 'beijing', '上海': 'shanghai', '广州': 'guangzhou', '深圳': 'shenzhen',
        '杭州': 'hangzhou', '南京': 'nanjing', '苏州': 'suzhou', '无锡': 'wuxi',
        '成都': 'chengdu', '重庆': 'chongqing', '西安': 'xian', '武汉': 'wuhan',
        '长沙': 'changsha', '青岛': 'qingdao', '大连': 'dalian', '天津': 'tianjin',
        '厦门': 'xiamen', '福州': 'fuzhou', '昆明': 'kunming', '大理': 'dali',
        '丽江': 'lijiang', '桂林': 'guilin', '阳朔': 'yangshuo', '三亚': 'sanya',
        '海口': 'haikou', '哈尔滨': 'haerbin', '长春': 'changchun', '沈阳': 'shenyang',
        '济南': 'jinan', '郑州': 'zhengzhou', '合肥': 'hefei', '南昌': 'nanchang',
        '张家界': 'zhangjiajie', '九寨沟': 'jiuzhaigou', '峨眉山': 'emeishan',
        '呼和浩特': 'huhehaote', '敦煌': 'dunhuang', '拉萨': 'lhasa',
        '乌鲁木齐': 'wulumuqi', '兰州': 'lanzhou', '香港': 'xianggang',
        '澳门': 'aomen', '台北': 'taibei', '黄山': 'huangshan', '泰安': 'taian',
        '洛阳': 'luoyang', '开封': 'kaifeng', '太原': 'taiyuan', '石家庄': 'shijiazhuang',
        '南宁': 'nanning', '贵阳': 'guiyang', '南昌': 'nanchang', '合肥': 'hefei',
        '宁波': 'ningbo', '温州': 'wenzhou', '绍兴': 'shaoxing', '嘉兴': 'jiaxing',
        '金华': 'jinhua', '台州': 'taizhou', '湖州': 'huzhou', '衢州': 'quzhou',
    }
    
    _CITY_CODES = {
        '北京': 'beijing1', '上海': 'shanghai2', '广州': 'guangzhou4', '深圳': 'shenzhen12',
        '杭州': 'hangzhou14', '南京': 'nanjing9', '苏州': 'suzhou11', '无锡': 'wuxi10',
        '成都': 'chengdu33', '重庆': 'chongqing53', '西安': 'xian7', '武汉': 'wuhan18',
        '长沙': 'changsha22', '青岛': 'qingdao27', '大连': 'dalian16', '天津': 'tianjin3',
        '厦门': 'xiamen21', '福州': 'fuzhou17', '昆明': 'kunming37', '大理': 'dali58',
        '丽江': 'lijiang42', '桂林': 'guilin28', '阳朔': 'yangshuo', '三亚': 'sanya61',
        '海口': 'haikou60', '哈尔滨': 'haerbin6', '长春': 'changchun23', '沈阳': 'shenyang8',
        '济南': 'jinan13', '泰安': 'taian', '郑州': 'zhengzhou29', '洛阳': 'luoyang30',
        '合肥': 'hefei24', '黄山': 'huangshan', '南昌': 'nanchang19', '九江': 'jiujiang',
        '张家界': 'zhangjiajie', '凤凰': 'fenghuang', '九寨沟': 'jiuzhaigou',
        '峨眉山': 'emeishan', '乐山': 'leshan', '呼伦贝尔': 'hulunbeier',
        '呼和浩特': 'huhehaote15', '敦煌': 'dunhuang', '拉萨': 'lhasa',
        '乌鲁木齐': 'wulumuqi25', '兰州': 'lanzhou26', '香港': 'xianggang38',
        '澳门': 'aomen', '台北': 'taibei45',
    }
    
    _ATTRACTION_IDS = {
        '故宫': 229, '故宫博物院': 229, '紫禁城': 229,
        '长城': 230, '八达岭长城': 230, '八达岭': 230,
        '颐和园': 231, '天坛': 233, '天坛公园': 233,
        '天安门广场': 5265, '天安门': 5265, '恭王府': 5174, '恭王府花园': 5174,
        '北京环球度假区': 2045497, '环球影城': 2045497,
        '北京动物园': 25505, '动物园': 25505, '香山': 5274, '香山公园': 5274,
        '北海公园': 5266, '北海': 5266, '圆明园': 5267, '圆明园遗址公园': 5267,
        '雍和宫': 5269, '雍和': 5269, '国家博物馆': 55229, '鸟巢': 5278,
        '水立方': 5279, '欢乐谷': 14400, '北京欢乐谷': 14400,
        '南锣鼓巷': 5281, '什刹海': 5282, '十三陵': 2826, '慕田峪长城': 232,
        '古北水镇': 147925, '奥林匹克公园': 5277, '鸟巢体育馆': 5278,
        '奥林匹克森林公园': 105642, '北京植物园': 5273, '玉渊潭公园': 5276,
        '景山公园': 5264, '八大处': 5275, '潭柘寺': 5283, '戒台寺': 5284,
        '北京自然博物馆': 55230, '中国科学技术馆': 55231, '国家图书馆': 55232,
        '三里屯': 55233, 'SKP': 55234, '蓝色港湾': 55235, '合生汇': 55236,
        
        '上海迪士尼': 1446648, '迪士尼乐园': 1446648, '外滩': 4766, '东方明珠': 762,
        '豫园': 4768, '城隍庙': 4768, '上海海昌海洋公园': 4651499, '上海动物园': 25506,
        '上海科技馆': 4770, '朱家角': 4771, '七宝古镇': 4772, '南京路': 4773,
        '人民广场': 4774, '陆家嘴': 4775, '上海中心': 1141388, '金茂大厦': 4776,
        '环球金融中心': 4777, '上海欢乐谷': 14401, '锦江乐园': 4779, '广富林': 111500,
        '辰山植物园': 107383, '田子坊': 4780, '上海博物馆': 4781, '上海美术馆': 4782,
        '上海大剧院': 4783, '上海音乐厅': 4784, '世纪公园': 4785, '静安寺': 4786,
        '淮海路': 4787, '徐家汇': 4788, '五角场': 4789, '上海野生动物园': 25507,
        '滴水湖': 107384, '南汇嘴观海公园': 107385, '上海薰衣草公园': 107386,
        
        '西湖': 757, '杭州西湖': 757, '灵隐寺': 758, '灵隐': 758, '千岛湖': 763,
        '宋城': 759, '雷峰塔': 761, '岳王庙': 764, '六和塔': 765,
        '乌镇': 155, '西塘': 153, '南浔': 154, '周庄': 152, '同里': 156,
        '拙政园': 5888, '留园': 5889, '虎丘': 5885, '寒山寺': 5887, '狮子林': 5886,
        '苏州博物馆': 111554, '平江路': 5890, '山塘街': 5891, '金鸡湖': 5892,
        '网师园': 5893, '沧浪亭': 5894, '耦园': 5895, '环秀山庄': 5896,
        '无锡鼋头渚': 4320, '鼋头渚': 4320, '灵山胜境': 4321, '三国城': 4322, '水浒城': 4322,
        '惠山古镇': 4323, '锡惠公园': 4324, '南长街': 4325, '拈花湾': 147926,
        
        '九寨沟': 4588, '黄龙': 4589, '峨眉山': 2200, '乐山大佛': 2201,
        '都江堰': 2206, '青城山': 2207, '宽窄巷子': 56668, '锦里': 56669,
        '武侯祠': 2204, '杜甫草堂': 2205, '大熊猫基地': 2209, '成都博物馆': 56670,
        '青羊宫': 2210, '文殊院': 2211, '春熙路': 56671, '太古里': 56672,
        'IFS': 56673, '都江堰景区': 2206, '青城山景区': 2207, '西岭雪山': 2212,
        '安仁古镇': 107387, '洛带古镇': 107388, '黄龙溪古镇': 107389,
        
        '兵马俑': 1886, '秦始皇兵马俑': 1886, '华清宫': 1887, '大雁塔': 1888,
        '小雁塔': 1889, '西安城墙': 1890, '回民街': 1891, '大唐不夜城': 111501,
        '大唐芙蓉园': 1892, '华山': 2015, '陕西历史博物馆': 1893, '碑林博物馆': 1894,
        '大明宫': 1895, '兴庆宫': 1896, '寒窑': 1897, '曲江池': 1898,
        '楼观台': 1899, '终南山': 1900, '法门寺': 2016, '太白山': 2017,
        
        '张家界': 1673, '天门山': 1674, '凤凰古城': 1678, '橘子洲': 111530, '岳麓山': 111531,
        '张家界国家森林公园': 1673, '武陵源': 1673, '袁家界': 1675, '天子山': 1676,
        '黄龙洞': 1677, '宝峰湖': 1679, '南岳衡山': 1680, '韶山': 1681,
        '岳阳楼': 1682, '洞庭湖': 1683, '东江湖': 1684, '崀山': 1685,
        
        '丽江古城': 3292, '丽江': 3292, '大理古城': 3295, '大理': 3295,
        '洱海': 3296, '苍山': 3297, '玉龙雪山': 3293, '香格里拉': 3298,
        '泸沽湖': 3299, '石林': 3302, '西双版纳': 3301, '滇池': 3303,
        '翠湖': 3304, '滇池海埂公园': 3303, '云南民族村': 3305, '九乡': 3306,
        '普达措': 3307, '松赞林寺': 3308, '梅里雪山': 3309, '虎跳峡': 3310,
        
        '桂林山水': 3208, '桂林': 3208, '漓江': 3209, '阳朔': 3210, '象鼻山': 3212,
        '两江四湖': 3213, '龙脊梯田': 3214, '德天瀑布': 3215, '银子岩': 3216,
        '遇龙河': 3217, '西街': 3218, '兴坪古镇': 3219, '世外桃源': 3220,
        '独秀峰王城': 3221, '靖江王城': 3221, '芦笛岩': 3222, '七星公园': 3223,
        
        '三亚': 3587, '亚龙湾': 3587, '天涯海角': 3588, '南山寺': 3589,
        '蜈支洲岛': 3590, '分界洲岛': 3591, '呀诺达': 3592, '亚龙湾热带天堂': 3593,
        '槟榔谷': 3594, '西岛': 3595, '大东海': 3596, '三亚湾': 3597,
        '海棠湾': 3598, '亚特兰蒂斯': 2045498, '免税店': 3599, '鹿回头': 3600,
        
        '黄山': 268, '宏村': 269, '西递': 270, '九华山': 271, '天柱山': 272,
        '齐云山': 273, '徽州古城': 274, '呈坎': 275, '唐模': 276, '棠樾牌坊': 277,
        
        '泰山': 2000, '曲阜三孔': 2003, '孔庙': 2003, '趵突泉': 2005, '大明湖': 2006,
        '千佛山': 2007, '崂山': 2015, '蓬莱阁': 2008, '刘公岛': 2009, '威海国际海水浴场': 2010,
        '烟台山': 2011, '金沙滩': 2012, '青州古城': 2013, '沂蒙山': 2014,
        
        '黄鹤楼': 1648, '东湖': 1649, '武汉大学': 1650, '三峡': 1655, '神农架': 1658,
        '武当山': 1651, '丹江口水库': 1652, '古隆中': 1653, '明显陵': 1654,
        '恩施大峡谷': 1656, '腾龙洞': 1657, '九宫山': 1659, '赤壁古战场': 1660,
        
        '庐山': 1330, '井冈山': 1331, '婺源': 1332, '三清山': 1333,
        '龙虎山': 1334, '滕王阁': 1335, '鄱阳湖': 1336, '景德镇': 1337,
        '瑶里古镇': 1338, '西海': 1339, '武功山': 1340, '明月山': 1341,
        
        '鼓浪屿': 1895, '武夷山': 1898, '厦门大学': 1896, '南普陀寺': 1897,
        '土楼': 1901, '永定土楼': 1901, '南靖土楼': 1902, '清源山': 1903,
        '开元寺': 1904, '崇武古城': 1905, '太姥山': 1906, '白水洋': 1907,
        
        '长隆': 5588, '小蛮腰': 5589, '广州塔': 5589, '白云山': 5590,
        '越秀公园': 5591, '沙面': 5592, '陈家祠': 5593, '世界之窗': 5655,
        '欢乐谷': 14402, '大梅沙': 5656, '小梅沙': 5657, '长隆野生动物世界': 5588,
        '长隆欢乐世界': 5587, '长隆水上乐园': 5586, '白水寨': 5594, '沙湾古镇': 5595,
        '圣心大教堂': 5596, '永庆坊': 5597, '珠江夜游': 5598, '天河城': 5599,
        
        '哈尔滨冰雪大世界': 522, '冰雪大世界': 522, '中央大街': 523, '索菲亚教堂': 524,
        '太阳岛': 525, '长白山': 485, '镜泊湖': 486, '五大连池': 487,
        '伪满皇宫': 545, '净月潭': 546, '亚布力滑雪场': 547, '雪乡': 548,
        '漠河北极村': 549, '黑河口岸': 550, '扎龙湿地': 551, '兴凯湖': 552,
        
        '布达拉宫': 4428, '大昭寺': 4429, '八角街': 4430, '纳木错': 4431,
        '羊卓雍错': 4432, '青海湖': 4300, '茶卡盐湖': 4301, '塔尔寺': 4302,
        '可可西里': 4303, '唐古拉山': 4304, '那曲草原': 4305, '林芝': 4306,
        '鲁朗林海': 4307, '雅鲁藏布大峡谷': 4308, '日喀则': 4309, '珠峰大本营': 4310,
        
        '莫高窟': 4256, '鸣沙山': 4257, '嘉峪关': 4258, '天山天池': 4500,
        '喀纳斯': 4501, '那拉提': 4502, '赛里木湖': 4503, '吐鲁番': 4504,
        '火焰山': 4505, '坎儿井': 4506, '喀什古城': 4507, '帕米尔高原': 4508,
        '巴音布鲁克': 4509, '独库公路': 4510, '博斯腾湖': 4511, '艾提尕尔清真寺': 4512,
        
        '承德避暑山庄': 2800, '避暑山庄': 2800, '外八庙': 2801, '木兰围场': 2802,
        '塞罕坝': 2802, '少林寺': 2200, '龙门石窟': 2021, '云台山': 2025,
        '清明上河园': 2026, '开封府': 2027, '包公祠': 2028, '嵩山': 2029,
        '尧山': 2030, '殷墟': 2031, '红旗渠': 2032, '太行山大峡谷': 2033,
        
        '夫子庙': 1150, '南京夫子庙': 1150, '中山陵': 1151, '明孝陵': 1152,
        '秦淮河': 1153, '玄武湖': 1154, '总统府': 1155, '栖霞山': 1156,
        '牛首山': 1157, '老门东': 1158, '雨花台': 1159, '莫愁湖': 1160,
        '阅江楼': 1161, '紫金山': 1162, '美龄宫': 1163, '南京博物院': 1164,
        
        '天津之眼': 2850, '天津眼': 2850, '五大道': 2851, '意式风情区': 2852,
        '古文化街': 2853, '海河': 2854, '盘山': 2855, '黄崖关长城': 2856,
        '瓷房子': 2857, '天津博物馆': 2858, '水上公园': 2859, '东丽湖': 2860,
        
        '维多利亚港': 5800, '迪士尼': 1446648, '海洋公园': 5801, '太平山顶': 5802,
        '大三巴': 5850, '澳门塔': 5851, '威尼斯人': 5852, '澳门历史城区': 5853,
        '妈祖庙': 5854, '官也街': 5855, '黑沙滩': 5856, '澳门科技馆': 5857,
    }
    
    @classmethod
    def _safe_encode(cls, text):
        if not text:
            return ''
        try:
            return quote(text, encoding='utf-8')
        except Exception as e:
            logger.warning(f"URL encode error: {e}")
            return str(text)
    
    @classmethod
    def _get_promo_params(cls):
        return '&'.join([f'{k}={v}' for k, v in cls._PROMO_PARAMS.items()])
    
    @classmethod
    def _get_city_code(cls, city_name):
        if not city_name:
            return None
        for cn_city, ctrip_code in cls._CITY_CODES.items():
            if cn_city in city_name or city_name in cn_city:
                return ctrip_code
        return None
    
    @classmethod
    def _get_city_pinyin(cls, city_name):
        if not city_name:
            return ''
        for cn_city, pinyin in cls._CITY_PINYIN.items():
            if cn_city in city_name or city_name in cn_city:
                return pinyin
        return cls._safe_encode(city_name)
    
    @classmethod
    def _find_attraction_id(cls, keyword):
        if not keyword:
            return None
        for name, ctrip_id in cls._ATTRACTION_IDS.items():
            if name in keyword or keyword in name:
                return ctrip_id
        return None
    
    # ======== 高德地图地理编码：将"地点名+城市"转换为经纬度 ========
    # 官方文档: https://lbs.amap.com/api/webservice/guide/api/georegeo
    # 调用示例: https://restapi.amap.com/v3/geocode/geo?key=KEY&address=故宫&city=北京
    @classmethod
    def set_amap_api_key(cls, key):
        """设置高德地图 Web 服务 API Key（可选，不填则降级为搜索页）"""
        global _AMAP_API_KEY
        _AMAP_API_KEY = key.strip() if key and key.strip() else None
        logger.info(f"高德地图 API Key 已设置: {'是' if _AMAP_API_KEY else '否'}")

    @classmethod
    def geocode(cls, destination, city=''):
        """
        将地点名转换为高德坐标(lng, lat)。
        - 优先使用本地缓存避免重复查询
        - 查询失败或无 API Key 时返回 None，由调用方降级为 keyword 搜索
        """
        global _GEOCODE_CACHE, _AMAP_API_KEY
        cache_key = f"{city or ''}|{destination}"

        # 1) 检查缓存
        cached = _GEOCODE_CACHE.get(cache_key)
        if cached and (time.time() - cached[2]) < _CACHE_TTL:
            logger.debug(f"[geocode] 命中缓存: {destination} -> {cached[0]},{cached[1]}")
            return (cached[0], cached[1])

        # 2) 没有 API Key，直接降级
        if not _AMAP_API_KEY:
            logger.debug(f"[geocode] 未配置高德 API Key，跳过坐标转换: {destination}")
            return None

        # 3) 调用高德地理编码 API
        try:
            full_address = f"{city}{destination}" if city and city not in destination else destination
            params = f"key={_AMAP_API_KEY}&address={quote(full_address, encoding='utf-8')}"
            if city:
                params += f"&city={quote(city, encoding='utf-8')}"
            url = f"https://restapi.amap.com/v3/geocode/geo?{params}"

            logger.debug(f"[geocode] 调用高德 API: {url[:120]}...")
            req = urllib.request.Request(url, headers={'User-Agent': 'TravelPlanner/1.0'})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            if str(data.get('status', '0')) == '1' and data.get('geocodes'):
                location = data['geocodes'][0].get('location', '')
                if location and ',' in location:
                    lng, lat = location.split(',')
                    lng = float(lng); lat = float(lat)
                    # 写入缓存
                    _GEOCODE_CACHE[cache_key] = (lng, lat, time.time())
                    logger.debug(f"[geocode] 成功: {destination} -> {lng},{lat}")
                    return (lng, lat)

            logger.warning(f"[geocode] 未找到 '{destination}' 的坐标，info={data.get('info')}")
            return None
        except Exception as e:
            logger.warning(f"[geocode] 高德 API 调用失败: {e}")
            return None

    @classmethod
    def get_amap_nav_url(cls, destination_name, city='', from_current=True):
        """
        生成高德地图导航链接 - 点击后自动在高德地图网页版中打开路线规划页面，
        终点已自动填入，用户只需输入起点（或使用当前位置）即可看到路线。

        策略（按优先级）:
          1) ditu.amap.com/dir?to[name]=名称&to[city]=城市 → 直接打开"路线规划"页，
             终点自动填入。完全无需 API Key、无需经纬度。（当前最优方案）
          2) （可选）如配置了高德 API Key → 地理编码后用 uri.amap.com/navigation
             跳转到经纬度导航（更精确，但 Key 需有效且为 Web 服务类型）
          3) 降级：uri.amap.com/search?keyword=名称 → 打开搜索页，搜索框自动填入
        """
        enc_name = cls._safe_encode(destination_name)
        enc_city = cls._safe_encode(city) if city else ''

        # ---- 策略 1: ditu.amap.com/dir 路线规划页（无需 Key、无需经纬度，推荐） ----
        # 实测可在浏览器中打开: https://ditu.amap.com/dir?to[name]=故宫博物院&to[city]=北京&type=car
        # 效果: 终点框已填入"故宫博物院"，标题为"路线规划 - 高德地图"
        dir_url = f"https://ditu.amap.com/dir?to[name]={enc_name}&type=car"
        if enc_city:
            dir_url += f"&to[city]={enc_city}"
        logger.debug(f"[nav] 路线规划: {destination_name} -> {dir_url[:120]}")
        return dir_url

    @classmethod
    def get_amap_nav_url_legacy(cls, destination_name, city='', from_current=True):
        """
        （备用）基于地理编码的导航链接。当 ditu.amap.com 接口变动时使用。
        """
        enc_name = cls._safe_encode(destination_name)
        enc_city = cls._safe_encode(city) if city else ''

        coord = cls.geocode(destination_name, city)
        if coord:
            lng, lat = coord
            nav_url = (f"https://uri.amap.com/navigation?"
                       f"from=&to={lng},{lat},{enc_name}"
                       f"&mode=car&policy=1&src=travel_plan&callnative=1")
            return nav_url

        search_url = f"https://uri.amap.com/search?keyword={enc_name}"
        if enc_city:
            search_url += f"&city={enc_city}"
        search_url += "&src=travel_plan&callnative=1"
        return search_url
    
    @classmethod
    def get_hotel_url(cls, keyword, city='', check_in='', check_out='', rooms=1, adults=2):
        """
        生成携程酒店预订链接（支持自动填充）
        :param keyword: 酒店名称或关键词
        :param city: 城市名称
        :param check_in: 入住日期（格式：YYYY-MM-DD）
        :param check_out: 离店日期（格式：YYYY-MM-DD）
        :param rooms: 房间数（默认1）
        :param adults: 成人数量（默认2）
        :return: 酒店预订URL
        """
        try:
            promo_params = cls._get_promo_params()
            url = f'https://m.ctrip.com/webapp/hotel?{promo_params}&keyword={cls._safe_encode(keyword)}'
            
            if city:
                city_code = cls._get_city_code(city)
                if city_code:
                    url += f'&city={city_code}'
                else:
                    url += f'&city={cls._safe_encode(city)}'
            
            # 添加日期参数
            if check_in:
                url += f'&checkIn={check_in}'
            if check_out:
                url += f'&checkOut={check_out}'
            
            # 添加人数和房间数
            url += f'&rooms={rooms}&adults={adults}'
            
            logger.debug(f"Generated hotel URL for '{keyword}' in '{city}': {url}")
            return url
        except Exception as e:
            logger.error(f"Failed to generate hotel URL: {e}")
            return f'https://m.ctrip.com/webapp/hotel?{promo_params}&keyword={cls._safe_encode(keyword)}'
    
    @classmethod
    def get_ticket_url(cls, keyword):
        """
        生成携程门票预订链接（自动跳转景点）
        策略:
          1) 优先查找景点ID → 使用 piao.ctrip.com/ticket/dest/t{ID}.html 直接跳景点详情
          2) 否则使用 m.ctrip.com 的景点搜索页，自动填入关键词搜索
        """
        try:
            promo_params = cls._get_promo_params()
            
            ctrip_id = cls._find_attraction_id(keyword)
            if ctrip_id:
                # 方案1: 直接跳景点详情页（最佳体验）
                url = f'https://piao.ctrip.com/ticket/dest/t{ctrip_id}.html?{promo_params}'
                logger.debug(f"Generated ticket URL for '{keyword}' (ID: {ctrip_id}): {url}")
                return url
            
            # 方案2: 跳景点搜索页，自动填入关键词
            # 使用 m.ctrip.com 的景点搜索页面，比 piao.ctrip.com 更稳定
            enc_keyword = cls._safe_encode(keyword)
            url = f'https://m.ctrip.com/webapp/you/sight/search.html?{promo_params}&keyword={enc_keyword}'
            logger.debug(f"Generated ticket search URL for '{keyword}': {url}")
            return url
        except Exception as e:
            logger.error(f"Failed to generate ticket URL: {e}")
            # 降级：基础搜索链接
            return f'https://m.ctrip.com/webapp/you/sight/search.html?keyword={cls._safe_encode(keyword)}'
    
    @classmethod
    def get_transport_url(cls, departure, destination, direction='outbound', transport_type='train', dep_date=''):
        try:
            promo_params = cls._get_promo_params()
            
            if direction == 'outbound':
                from_city = cls._safe_encode(departure)
                to_city = cls._safe_encode(destination)
            else:
                from_city = cls._safe_encode(destination)
                to_city = cls._safe_encode(departure)
            
            if transport_type == 'flight':
                url = f'https://m.ctrip.com/html5/flight/home?{promo_params}&dcity={from_city}&acity={to_city}'
                if dep_date:
                    url += f'&date={dep_date}'
            else:
                url = f'https://m.ctrip.com/webapp/train?{promo_params}&fromCityName={from_city}&toCityName={to_city}'
                if dep_date:
                    url += f'&depDate={dep_date}'
            
            logger.debug(f"Generated {transport_type} URL: {url}")
            return url
        except Exception as e:
            logger.error(f"Failed to generate transport URL: {e}")
            base = 'https://m.ctrip.com/html5/flight/home' if transport_type == 'flight' else 'https://m.ctrip.com/webapp/train'
            return f'{base}?{promo_params}'

CTRIP_AFFILIATE_ID = ''
CTRIP_SID = ''
CITY_CTRIP_CODES = CtripPromoService._CITY_CODES
ATTRACTION_CTRIP_IDS = CtripPromoService._ATTRACTION_IDS

def get_ctrip_hotel_url(keyword, city='', check_in='', check_out='', rooms=1, adults=2):
    return CtripPromoService.get_hotel_url(keyword, city, check_in, check_out, rooms, adults)

def get_ctrip_ticket_url(keyword, city=''):
    return CtripPromoService.get_ticket_url(keyword)

def get_ctrip_transport_url(departure, destination, direction='outbound', transport_type='train', dep_date=''):
    return CtripPromoService.get_transport_url(departure, destination, direction, transport_type, dep_date)

def get_amap_nav_url(destination_name, city='', from_current=True):
    """
    生成高德地图导航链接（从当前位置导航到目的地）
    :param destination_name: 目的地名称
    :param city: 城市名称（可选）
    :param from_current: 是否从当前位置导航（默认True）
    :return: 导航URL
    """
    return CtripPromoService.get_amap_nav_url(destination_name, city, from_current)