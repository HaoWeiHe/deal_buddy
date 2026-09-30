"""Demo data mirroring the spec's examples (§1, §7). Run:  python -m dealbuddy.seed [user_id]"""
import sys
from datetime import timedelta

from . import db, deals, profile, tools


def _in(days: int) -> str:
    return (deals.today() + timedelta(days=days)).isoformat()


def sample_deals() -> list[dict]:
    return [
        {"title": "新病人洗牙送 $150 Amazon 禮卡", "title_en": "New patient cleaning, free $150 Amazon gift card", "conditions_en": ["New patients only", "Dental insurance required"],
         "merchant": "Smile Dental Markham", "entities": ["Amazon"],
         "category": "healthcare", "mechanic": "gift_card", "face_value": 150, "est_value": 150,
         "conditions": ["限新病人", "需有牙科保險"], "locations": ["Markham"], "valid_until": _in(5),
         "description": "新病人完成洗牙與檢查，送 $150 Amazon gift card", "confidence": 0.9},
        {"title": "一風堂現金付款 8 折", "title_en": "Ippudo 20% off when paying cash", "conditions_en": [],
         "merchant": "Ippudo", "entities": [], "category": "dining",
         "mechanic": "percent_off", "percent_off": 0.2, "conditions": [], "locations": ["Toronto"],
         "valid_until": _in(20), "description": "拉麵店一風堂現金付款享 20% off", "confidence": 0.9},
        {"title": "DoorDash 本週免運", "title_en": "DoorDash free delivery this week", "conditions_en": ["Orders over $15"],
         "merchant": "DoorDash", "entities": [], "category": "delivery",
         "mechanic": "free_delivery", "est_value": 7, "conditions": ["訂單滿 $15"], "locations": ["Toronto", "Online"],
         "valid_until": _in(6), "description": "DoorDash 指定餐廳 $0 delivery fee", "confidence": 0.9},
        {"title": "T&T 消費滿 $100 抽長榮台灣來回機票", "title_en": "Spend $100 at T&T for a chance to win EVA Air flights to Taiwan", "conditions_en": ["Single purchase over $100"],
         "merchant": "T&T", "entities": ["EVA Air", "台灣"],
         "category": "grocery", "mechanic": "sweepstakes", "est_value": 5, "conditions": ["單筆滿 $100"],
         "locations": ["Toronto"], "valid_until": _in(25), "description": "大統華滿額抽台灣機票", "confidence": 0.9},
        {"title": "Tim Hortons 咖啡買一送一", "title_en": "Tim Hortons coffee buy one get one", "conditions_en": ["App orders only"],
         "merchant": "Tim Hortons", "entities": [], "category": "cafe",
         "mechanic": "bogo", "conditions": ["限 app 下單"], "locations": ["Toronto", "Online"], "valid_until": _in(3),
         "description": "Tim Hortons 中杯咖啡 BOGO", "confidence": 0.9},
        {"title": "溫哥華列治文夜市門票半價", "title_en": "Richmond Night Market tickets 50% off", "conditions_en": ["Online tickets only"],
         "merchant": "Richmond Night Market", "entities": [],
         "category": "entertainment", "mechanic": "percent_off", "percent_off": 0.5, "conditions": ["限線上購票"],
         "locations": ["Richmond"], "valid_until": _in(40), "description": "Richmond Night Market 門票 50% off",
         "confidence": 0.8},
        {"title": "Uber Eats 新用戶 5 折", "title_en": "Uber Eats 50% off for new users", "conditions_en": ["New users only", "Up to $20 off"],
         "merchant": "Uber Eats", "entities": [], "category": "delivery",
         "mechanic": "percent_off", "percent_off": 0.5, "conditions": ["限新用戶", "最多折 $20"],
         "locations": ["Online"], "valid_until": _in(10), "description": "Uber Eats 首單 50% off", "confidence": 0.8},
    ]


def seed_deals() -> list[int]:
    return [deals.save(d, source_type="seed")[0] for d in sample_deals()]


def seed_demo_user(user_id: str = "demo") -> None:
    """The spec's example user: likes Ippudo, DoorDash and Taiwan, dislikes Tim Hortons, needs a dental cleaning."""
    db.ensure_user(user_id, city="Toronto", onboarded=1)
    up = lambda **a: tools.update_profile(user_id, a)  # noqa: E731
    up(action="like", target="Ippudo", strength=0.9)
    up(action="like", target="DoorDash", strength=0.7)
    up(action="like", target="台灣", strength=0.9)
    up(action="like", target="Amazon", strength=0.2)
    up(action="dislike", target="Tim Hortons")
    up(action="set_context", target="dining", strength=0.9)
    up(action="set_context", target="delivery", strength=0.8)
    up(action="set_context", target="travel", strength=0.5)
    up(action="set_context", target="cafe", strength=0.5)
    up(action="set_mechanic", target="percent_off", strength=0.6)
    up(action="set_mechanic", target="free_delivery", strength=0.8)
    up(action="set_mechanic", target="gift_card", strength=0.5)
    up(action="set_mechanic", target="sweepstakes", strength=0.2)
    up(action="set_mechanic", target="bogo", strength=0.3)
    profile.add_intent(user_id, "dental_cleaning", label="洗牙", strength=0.9, categories=["healthcare"],
                       keywords=["洗牙", "dental", "dentist", "牙"], sensitive=True)


if __name__ == "__main__":
    db.init_db()
    ids = seed_deals()
    seed_demo_user(sys.argv[1] if len(sys.argv) > 1 else "demo")
    print(f"seeded {len(ids)} deals and demo user")
