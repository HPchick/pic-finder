"""图片按内容分类。

四类：人体姿势、服装、道具、场景建筑，与 content_category 表里的四行一致。
图库页按这里的结论分组，方便按用途筛选参考图。

分类依据
    分两级，按可信度从高到低。

    第一优先是**下载时记录的检索类别**（`library_file.content_category_id`）。
    用户在检索页明确选过类别，那是最准的依据。图库卡片上写的那一段
    「作者_类别_分辨率」里的类别，用的就是这一栏。

    只有那一栏为空时才按图片自带的文字信息猜，也就是标题与标签一起看。

    为什么不只读标题：实测安装版图库里那 50 张 Danbooru 图片，标题一栏
    全部为空，标签一栏 50 张全有。只读标题的话，整个图库都会落到
    「未分类」，分类功能等于不存在。

匹配规则（只用于没有类别的那一批）
    标签是英文小写加下划线，例如 arms_at_sides、holding_sword。这里对每个
    标签做带边界的子串匹配，命中关键词即计分。

    「带边界」这一条不能省。直接做子串匹配时，rainbow 会命中 bow、
    sunglasses 会命中 glass，道具类会被一堆无关的图灌满。匹配时要求关键词
    落在标签的开头或紧跟下划线，结尾同理，并允许一个复数 s。

    关键词分两级。核心词本身就能给图片定性，例如 sitting、kimono、sword。
    辅助词只是旁证，例如 sky、looking_at_viewer、holding。判定顺序是：
    先只在有核心命中的类别之间比，核心命中一样多时比辅助命中，再一样多时
    按固定顺序取前者，顺序为 pose、outfit、prop、scene。

    过于通用的词不进表。outdoors、solo、1girl 这类标签几乎每张图都有，
    放进任何一类都会把整个图库染成那一类，分类就失去意义了。

词表可以改
    下面两张表就是全部规则，没有藏在别的文件里的第二份。觉得哪张图分错了，
    改词表比改算法有用。

    有一个词是故意不进的：弓。标签 bow 与发饰 hair_bow 共用了 bow 这三个
    字母，而这里的匹配只看关键词边界，hair_bow 会被 bow 命中。实测 50 张图
    里有 2 张因此被误判成道具类。去掉 bow 之后，真正的弓靠 arrow 与 quiver
    两个词仍然认得出来。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

# 类别与中文名。顺序即判定平手时的优先级。
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("pose", "人体姿势"),
    ("outfit", "服装"),
    ("prop", "道具"),
    ("scene", "场景建筑"),
)

CATEGORY_SLUGS: tuple[str, ...] = tuple(slug for slug, _name in CATEGORIES)

LABELS: dict[str, str] = dict(CATEGORIES)

# 分不出类别时的取值。它不是一个真实的类别，不写进 content_category 表。
UNCLASSIFIED = "other"
UNCLASSIFIED_LABEL = "未分类"

# 核心词：命中即基本定性。
CORE_WORDS: dict[str, tuple[str, ...]] = {
    "pose": (
        "standing", "sitting", "lying", "kneeling", "squatting", "crouching",
        "walking", "running", "jumping", "straddling", "all_fours",
        "contrapposto", "stretching", "bent_over", "leaning_forward",
        "leaning_back", "on_back", "on_stomach", "on_side", "fetal_position",
        "spread_legs", "crossed_legs", "legs_up", "legs_apart", "arms_up",
        "arms_at_sides", "arms_crossed", "arms_behind_back",
        "arms_behind_head", "hand_on_hip", "hands_on_hips", "hand_up",
        "head_tilt", "head_down", "looking_back", "looking_up", "looking_down",
        "tiptoes", "standing_on_one_leg", "wariza", "seiza", "indian_style",
        "arched_back", "arm_support", "leg_support", "knees_up",
        "knees_together", "hugging_own_legs", "self_hug",
    ),
    "outfit": (
        "dress", "kimono", "yukata", "uniform", "school_uniform",
        "sailor_dress", "serafuku", "suit", "necktie", "bowtie", "coat",
        "jacket", "hoodie", "sweater", "cardigan", "shirt", "blouse",
        "skirt", "pleated_skirt", "shorts", "pants", "jeans", "overalls",
        "apron", "maid", "swimsuit", "bikini", "school_swimsuit", "lingerie",
        "underwear", "bra", "panties", "thighhighs", "stockings", "pantyhose",
        "socks", "boots", "shoes", "sneakers", "sandals", "high_heels",
        "gloves", "mittens", "scarf", "hat", "cap", "beret", "helmet",
        "ribbon", "hair_ribbon", "hairband", "hair_bow", "cape", "cloak",
        "robe", "armor", "vest", "belt", "sash", "obi", "necklace",
        "earrings", "bracelet", "choker", "glasses", "sunglasses", "goggles",
        "mask", "veil", "tuxedo", "wedding_dress", "fur_trim",
    ),
    "prop": (
        "sword", "katana", "blade", "dagger", "knife", "gun", "rifle",
        "pistol", "revolver", "shotgun", "arrow", "quiver", "spear",
        "lance", "halberd", "axe", "hammer", "mace", "staff", "wand",
        "scepter", "shield", "scythe", "chainsaw", "whip", "chain",
        "umbrella", "parasol", "book", "notebook", "bottle", "teacup", "mug",
        "teapot", "kettle", "bouquet", "rose", "lantern", "candle", "torch",
        "guitar", "violin", "piano", "flute", "trumpet", "microphone",
        "headset", "camera", "cellphone", "controller", "balloon", "kite",
        "basket", "backpack", "handbag", "briefcase", "teddy_bear", "doll",
        "plush", "onigiri", "lollipop", "cigarette", "syringe", "potion",
        "scroll", "telescope", "binoculars", "fishing_rod", "bucket",
        "shovel", "broom", "scissors", "palette", "easel", "playing_card",
    ),
    "scene": (
        "cityscape", "city", "street", "road", "building", "skyscraper",
        "architecture", "house", "castle", "temple", "shrine", "church",
        "cathedral", "ruins", "bridge", "tower", "landscape", "mountain",
        "forest", "beach", "ocean", "lake", "river", "waterfall", "desert",
        "cave", "island", "cliff", "farm", "garden", "park", "room",
        "interior", "bedroom", "kitchen", "bathroom", "classroom", "office",
        "cafe", "restaurant", "market", "museum", "station", "airport",
        "harbour", "pier", "alley", "alleyway", "staircase", "corridor",
        "rooftop", "balcony", "veranda", "tatami", "fireplace", "bookshelf",
        "shop", "store",
    ),
}

# 辅助词：只作旁证。故意收进一批通用词，因为它们不能单独定性，
# 但能在两张图的核心命中一样多时帮忙分出高下。
AUX_WORDS: dict[str, tuple[str, ...]] = {
    "pose": (
        "from_behind", "from_above", "from_below", "from_side",
        "looking_at_viewer", "looking_to_the_side", "dynamic_pose", "pose",
        "full_body", "upper_body", "cowboy_shot", "close-up", "portrait",
        "eye_contact", "facing_viewer", "dutch_angle", "foreshortening",
        "perspective",
    ),
    "outfit": (
        "clothing", "clothes", "outfit", "attire", "costume", "cosplay",
        "fashion", "accessory", "accessories", "jewelry", "jewellery",
        "hair_ornament", "wet_clothes", "torn_clothes", "detached_sleeves",
    ),
    "prop": (
        "weapon", "weapons", "holding", "holds", "wield", "tool",
        "instrument", "equipment", "gear", "item", "object", "prop",
        "container", "drink", "food",
    ),
    "scene": (
        "outdoors", "indoor", "sky", "cloud", "moon", "sun", "star", "tree",
        "grass", "night", "sunset", "sunrise", "snow", "rain", "water",
        "window", "wall", "floor", "ceiling", "curtain", "carpet",
        "streetlight", "traffic_light", "fence", "horizon", "scenery",
        "background", "view", "neon", "cherry_blossoms", "bamboo", "nature",
        "sea", "mountain_range",
    ),
}


def _compile(words: Iterable[str]) -> re.Pattern[str]:
    """把一组关键词编成一个正则。

    长词放前面，避免短词先匹配上而长词被跳过。前后要求是开头、下划线或结尾，
    并允许一个复数 s，这就是模块说明里那条「带边界」。
    """
    body = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?:^|_)(?:{body})s?(?:_|$)")


_CORE = {slug: _compile(CORE_WORDS[slug]) for slug in CATEGORY_SLUGS}
_AUX = {slug: _compile(AUX_WORDS[slug]) for slug in CATEGORY_SLUGS}

# 切分文字。标签之间是空格，标题可能是句子，中文标题里用全角标点。
_SPLIT = re.compile(r"[\s,;、，；。:：/|()（）\[\]【】]+")


@lru_cache(maxsize=16384)
def _token_hits(token: str) -> tuple[tuple[str, int, str], ...]:
    """一个标签命中了哪些类别。

    返回 (类别, 级别, 命中的关键词) 的元组，级别 2 为核心，1 为辅助。同一个
    标签在同一类里既命中核心又命中辅助时只记核心，因此每类最多出现一次。

    加缓存是因为图库里的标签重复率极高，一万张图可能只有几千个不同的标签。
    缓存之后实际参与正则匹配的次数与图片张数几乎无关。
    """
    hits: list[tuple[str, int, str]] = []
    for slug in CATEGORY_SLUGS:
        found = _CORE[slug].search(token)
        if found:
            hits.append((slug, 2, found.group(0).strip("_")))
            continue
        found = _AUX[slug].search(token)
        if found:
            hits.append((slug, 1, found.group(0).strip("_")))
    return tuple(hits)


def _tokens(*texts: str) -> list[str]:
    joined = " ".join(text for text in texts if text)
    return _SPLIT.split(joined.lower())


def score(title: str = "", tags: str = "") -> dict[str, tuple[int, int]]:
    """给出每类的（核心命中数，辅助命中数）。调试与测试用。"""
    core = {slug: 0 for slug in CATEGORY_SLUGS}
    aux = {slug: 0 for slug in CATEGORY_SLUGS}
    for token in _tokens(title, tags):
        if not token:
            continue
        for slug, level, _word in _token_hits(token):
            if level == 2:
                core[slug] += 1
            else:
                aux[slug] += 1
    return {slug: (core[slug], aux[slug]) for slug in CATEGORY_SLUGS}


def explain(title: str = "", tags: str = "") -> dict[str, list[str]]:
    """给出每类各命中了哪些关键词。

    只用来看「为什么这张图被分到这一类」。分错了的时候，先看这里，
    再决定是改词表还是改算法。
    """
    reasons: dict[str, list[str]] = {slug: [] for slug in CATEGORY_SLUGS}
    for token in _tokens(title, tags):
        if not token:
            continue
        for slug, _level, word in _token_hits(token):
            reasons[slug].append(word)
    return {slug: words for slug, words in reasons.items() if words}


def classify(title: str = "", tags: str = "") -> str:
    """判断一张图属于哪一类。分不出时返回 UNCLASSIFIED。"""
    table = score(title, tags)
    with_core = [slug for slug in CATEGORY_SLUGS if table[slug][0] > 0]
    if with_core:
        return max(
            with_core,
            key=lambda slug: (table[slug][0], table[slug][1], -CATEGORY_SLUGS.index(slug)),
        )
    with_aux = [slug for slug in CATEGORY_SLUGS if table[slug][1] > 0]
    if not with_aux:
        return UNCLASSIFIED
    return max(
        with_aux, key=lambda slug: (table[slug][1], -CATEGORY_SLUGS.index(slug))
    )


def row_title(row) -> str:
    """从数据库行里取标题。缺列与空值都当空字符串。"""
    return _row_text(row, "title")


def row_tags(row) -> str:
    return _row_text(row, "tag_string")


def _row_text(row, name: str) -> str:
    try:
        value = row[name]
    except (IndexError, KeyError, TypeError):
        return ""
    return "" if value is None else str(value)


def classify_row(row) -> str:
    """按数据库行分类。

    第一优先是**下载时记录的检索类别**，也就是用户在图库卡片上看到的那一段。
    用户在检索页明确选过类别，那是最准的依据，比任何文字猜测都强。

    这一条是补上的，原因值得记下来：最初的实现只看标题与标签，而用户在检索页
    选了「人体姿势」下载下来的图，标签里全是 bra、panties、bikini 这类服装词，
    于是被归到了服装类。用户看到的卡片上明明写着「人体姿势」，分类却给出服装，
    结论是分类器完全不工作。实测那 50 张里 41 张被判成服装，就是这个原因。

    只有类别一栏为空（例如从别处手工放进图库的文件）时才按标题与标签猜。
    """
    slug = _row_text(row, "category_slug")
    if slug in CATEGORY_SLUGS:
        return slug
    return classify(row_title(row), row_tags(row))


def label_for(slug: str) -> str:
    """类别的中文名。认不出来的一律叫未分类。"""
    return LABELS.get(slug, UNCLASSIFIED_LABEL)


def count_by_category(rows: Iterable) -> dict[str, int]:
    """统计每类各有多少张。返回的字典一定包含全部类别键与未分类键。"""
    counts = {slug: 0 for slug in CATEGORY_SLUGS}
    counts[UNCLASSIFIED] = 0
    for row in rows:
        counts[classify_row(row)] += 1
    return counts
