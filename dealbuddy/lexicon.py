"""Keyword lists used by the offline extractor and offline chat mode (no API key).
With an API key, Claude does this understanding; these lists are only the fallback."""

CATEGORY_KEYWORDS = {
    "delivery": ["外送", "外賣", "外卖", "delivery", "doordash", "uber eats", "ubereats", "skip the dishes", "免運", "免运"],
    "dining": ["吃", "餐廳", "餐厅", "restaurant", "拉麵", "拉面", "ramen", "日料", "壽司", "寿司", "sushi", "火鍋", "火锅",
               "brunch", "晚餐", "午餐", "餐", "燒肉", "烧肉", "韓式", "韩式", "buffet", "自助餐", "dim sum", "早茶", "點心"],
    "grocery": ["超市", "grocery", "買菜", "买菜", "生鮮", "生鲜", "supermarket"],
    "cafe": ["咖啡", "coffee", "奶茶", "bubble tea", "手搖", "手摇", "latte", "拿鐵", "donut", "甜甜圈"],
    "healthcare": ["牙醫", "牙医", "洗牙", "dentist", "dental", "看醫生", "看医生", "眼科", "驗光", "验光", "optometrist",
                   "physio", "物理治療", "按摩治療", "massage therapy", "體檢", "体检", "clinic", "診所", "诊所"],
    "beauty": ["美甲", "美髮", "美发", "剪頭髮", "剪头发", "護膚", "护肤", "化妝", "化妆", "保養", "保养", "spa", "nail", "hair salon"],
    "shopping": ["衣服", "購物", "购物", "outlet", "鞋", "包包", "clothing", "fashion"],
    "electronics": ["手機", "手机", "電腦", "电脑", "耳機", "耳机", "electronics", "iphone", "laptop", "switch", "相機", "相机"],
    "travel": ["機票", "机票", "旅遊", "旅游", "飯店", "酒店", "hotel", "flight", "回台", "出國", "出国", "trip", "旅行", "度假", "airbnb"],
    "entertainment": ["電影", "电影", "演唱會", "演唱会", "movie", "concert", "遊樂園", "游乐园", "展覽", "展览", "門票", "门票"],
    "transport": ["加油", "gas", "presto", "停車", "停车", "油價", "油价"],
    "finance": ["信用卡", "開戶", "开户", "credit card", "bank account", "返現卡", "返现卡", "開卡", "开卡"],
    "home": ["家具", "傢俱", "ikea", "家電", "家电", "搬家"],
}

MECHANIC_KEYWORDS = {
    "free_delivery": ["免運", "免运", "free delivery", "$0 delivery", "0 delivery fee", "免外送費", "免配送費"],
    "gift_card": ["gift card", "禮卡", "礼卡", "禮品卡", "礼品卡", "giftcard"],
    "bogo": ["買一送一", "买一送一", "bogo", "buy one get one", "買1送1", "第二杯半價", "第二件半價"],
    "sweepstakes": ["抽獎", "抽奖", "滿額抽", "满额抽", "消費抽", "抽機票", "抽机票", "抽台灣", "giveaway", "sweepstakes", "contest", "chance to win", "win a"],
    "cashback": ["cashback", "cash back", "返現", "返现", "回饋", "回馈", "返利"],
    "points": ["points", "積分", "积分", "點數", "点数", "pc optimum", "scene+", "air miles"],
    "free_item": ["免費送", "免费送", "送一", "free gift", "free item", "贈品", "赠品", "免費", "免费"],
}

# Offline intent templates: phrase → (intent id, label, categories, keywords, sensitive)
INTENT_TEMPLATES = [
    (["洗牙", "牙醫", "牙医", "dentist", "dental", "看牙"], "dental_cleaning", "洗牙 / 看牙醫", ["healthcare"],
     ["牙", "dental", "dentist", "洗牙"], True),
    (["眼鏡", "眼镜", "驗光", "验光", "隱形眼鏡", "optometrist"], "eye_care", "配眼鏡 / 驗光", ["healthcare"],
     ["眼", "optometr", "glasses", "contact"], True),
    (["機票", "机票", "回台", "flight", "飛", "訂票"], "book_flight", "訂機票", ["travel"],
     ["機票", "机票", "flight", "airline", "航空", "長榮", "華航"], False),
    (["搬家", "moving"], "moving_home", "搬家", ["home"], ["搬家", "moving", "家具", "ikea"], False),
    (["剪頭髮", "剪头发", "haircut"], "haircut", "剪頭髮", ["beauty"], ["hair", "剪", "髮"], False),
    (["買手機", "换手机", "換手機", "new phone"], "new_phone", "換手機", ["electronics", "finance"],
     ["phone", "手機", "iphone", "plan"], False),
    (["生日", "birthday"], "birthday", "生日", ["dining"], ["birthday", "生日"], False),
]

DONE_WORDS = ["already done", "all done", "got it done", "booked it", "洗完了", "看完了", "訂好了", "订好了", "買好了", "买好了", "弄好了", "搞定了", "已經訂", "已经订", "結束了", "不用了"]
DISLIKE_WORDS = ["不喜歡", "不喜欢", "討厭", "讨厌", "不要推", "別推", "别推", "不愛", "不爱", "難吃", "难吃", "hate", "don't like"]
LIKE_WORDS = ["喜歡", "喜欢", "常吃", "常去", "常用", "常點", "常点", "最愛", "最爱", "愛吃", "爱吃", "love", "常買", "常买",
              "i like", "usually eat", "favorite", "favourite"]

INTENT_LABEL_EN = {
    "dental_cleaning": "a dental cleaning", "eye_care": "an eye exam / glasses", "book_flight": "booking flights",
    "moving_home": "moving", "haircut": "a haircut", "new_phone": "a new phone", "birthday": "a birthday",
}
