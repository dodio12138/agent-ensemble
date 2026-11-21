"""Interpret natural-language musical direction as playable traits."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, get_args

from band.music import (
    HARMONY_MODES,
    ConductorPlan,
    FrozenModel,
    Genre,
    Groove,
    Harmony,
    Mode,
    MusicProfile,
)

STYLE_PROMPT_VERSION = "style-direction-v2"
STYLE_PROMPT = """Interpret a request for original instrumental accompaniment.
Classify recognizable artists, film scores, hybrid styles, subgenres and imagery
by musical traits, in English or Chinese. Pick the closest allowed genre family
and choose groove, mode and harmony independently. For blends use the leading
family and borrow the other one's groove or harmony. Examples: 薄雾般的氛围 sound
can be ambient with sustained harmony; a dusty beat with offbeat accents can be
hip_hop with half_time; 电子舞曲 can be electronic with four_on_floor; a slowly
growing orchestral theme can be cinematic. Use the typical recipe as a starting
point when provided. Do not invent an artist's traits if you do not recognize the
name. Unknown names with no musical context or unrelated commands mean all keep.
Example: '后天的天气如何' or 'Silver Penguin Cartography Quartet 曲风' means
{"genre":"keep","groove":"keep","mode":"keep","harmony":"keep"}.
For rhythm only set genre, mode and harmony to keep. If the chords must stay, use
keep for harmony and mode. A style change may reharmonize; do not choose a random
chord formula.
Grooves: straight_eighth (even eighths), shuffle (triplet feel), one_drop (reggae),
four_on_floor (kick on every quarter), sixteenth (funk subdivisions), half_time
(snare on 3), bossa (Latin syncopation), swing (jazz eighths).
Harmony: pop_1564 I-V-vi-IV; major_145 I-IV-V; mixolydian_174 I-bVII-IV;
minor_1645 i-bVI-iv-V; minor_1767 i-bVII-bVI-bVII; blues_8 dominant 7th;
dorian_vamp i7-IV7; soul_251 ii7-V7-Imaj7. Output JSON only."""


class StyleChoice(FrozenModel):
    genre: Genre | Literal["keep"]
    groove: Groove | Literal["keep"]
    mode: Mode | Literal["keep"]
    harmony: Harmony

    @classmethod
    def output_schema(cls) -> dict:
        schema = cls.model_json_schema()
        for field, values in (("genre", Genre), ("groove", Groove), ("mode", Mode)):
            schema["properties"][field] = {"type": "string", "enum": ["keep", *get_args(values)]}
        return schema


@dataclass(frozen=True, slots=True)
class GenreDefaults:
    style: Literal["swing", "bossa", "funk", "ballad"]
    groove: Groove
    harmony: Harmony
    drums: Literal["balanced", "sparse", "driving"]
    bass: Literal["walking", "root", "syncopated"]
    piano: Literal["comp", "sustained", "syncopated"]

    def arrange(self, current: ConductorPlan, profile: MusicProfile) -> ConductorPlan:
        return ConductorPlan(
            style=self.style,
            intensity=current.intensity,
            drums=self.drums,
            bass=self.bass,
            piano=self.piano,
            profile=profile,
        )


_DEFAULTS: dict[Genre, GenreDefaults] = {
    "cinematic": GenreDefaults(
        "ballad", "straight_eighth", "minor_1767", "sparse", "root", "sustained"
    ),
    "ambient": GenreDefaults(
        "ballad", "straight_eighth", "dorian_vamp", "sparse", "root", "sustained"
    ),
    "electronic": GenreDefaults(
        "funk", "four_on_floor", "minor_1645", "driving", "syncopated", "syncopated"
    ),
    "latin": GenreDefaults("bossa", "bossa", "major_145", "balanced", "syncopated", "syncopated"),
    "country": GenreDefaults("swing", "shuffle", "major_145", "balanced", "root", "comp"),
    "hip_hop": GenreDefaults("funk", "half_time", "minor_1767", "sparse", "syncopated", "comp"),
    "classical": GenreDefaults("ballad", "straight_eighth", "major_145", "sparse", "root", "comp"),
    "rock": GenreDefaults("funk", "straight_eighth", "mixolydian_174", "driving", "root", "comp"),
    "hard_rock": GenreDefaults(
        "funk", "straight_eighth", "mixolydian_174", "driving", "root", "comp"
    ),
    "alternative_rock": GenreDefaults(
        "funk", "half_time", "minor_1767", "balanced", "root", "comp"
    ),
    "pop": GenreDefaults("ballad", "straight_eighth", "pop_1564", "balanced", "root", "sustained"),
    "blues": GenreDefaults("swing", "shuffle", "blues_8", "balanced", "walking", "comp"),
    "reggae": GenreDefaults("bossa", "one_drop", "major_145", "sparse", "syncopated", "syncopated"),
    "disco": GenreDefaults(
        "funk", "four_on_floor", "pop_1564", "driving", "syncopated", "syncopated"
    ),
    "soul": GenreDefaults("ballad", "sixteenth", "soul_251", "balanced", "syncopated", "comp"),
    "swing": GenreDefaults("swing", "swing", "soul_251", "balanced", "walking", "comp"),
    "bossa": GenreDefaults("bossa", "bossa", "soul_251", "sparse", "root", "syncopated"),
    "funk": GenreDefaults(
        "funk", "sixteenth", "dorian_vamp", "driving", "syncopated", "syncopated"
    ),
    "ballad": GenreDefaults("ballad", "straight_eighth", "pop_1564", "sparse", "root", "sustained"),
    # Game scores: bright loops for 8-bit and platformers, a lyrical town theme, a
    # driving minor boss fight, a modal open world and a hushed stealth pulse.
    "chiptune": GenreDefaults(
        "funk", "straight_eighth", "pop_1564", "driving", "syncopated", "syncopated"
    ),
    "platformer": GenreDefaults(
        "funk", "straight_eighth", "major_145", "balanced", "syncopated", "comp"
    ),
    "rpg": GenreDefaults("ballad", "straight_eighth", "pop_1564", "sparse", "root", "comp"),
    "battle": GenreDefaults(
        "funk", "straight_eighth", "minor_1645", "driving", "root", "syncopated"
    ),
    "exploration": GenreDefaults(
        "ballad", "half_time", "dorian_vamp", "sparse", "root", "sustained"
    ),
    "stealth": GenreDefaults("ballad", "half_time", "minor_1767", "sparse", "root", "sustained"),
}
_ALTERNATE_GROOVES: dict[Genre, tuple[Groove, ...]] = {
    "cinematic": ("half_time", "sixteenth"),
    "ambient": ("half_time",),
    "electronic": ("sixteenth", "half_time", "straight_eighth"),
    "latin": ("sixteenth", "straight_eighth"),
    "country": ("straight_eighth", "swing"),
    "hip_hop": ("swing", "straight_eighth", "sixteenth", "shuffle"),
    "classical": ("sixteenth", "shuffle"),
    "rock": ("half_time", "shuffle"),
    "hard_rock": ("half_time", "shuffle"),
    "alternative_rock": ("straight_eighth",),
    "pop": ("half_time", "four_on_floor", "sixteenth"),
    "blues": ("straight_eighth",),
    "disco": ("sixteenth",),
    "soul": ("straight_eighth", "half_time", "shuffle"),
    "swing": ("shuffle",),
    "ballad": ("half_time",),
    "chiptune": ("sixteenth", "four_on_floor"),
    "platformer": ("sixteenth", "shuffle"),
    "rpg": ("shuffle", "half_time"),
    "battle": ("sixteenth", "half_time"),
    "exploration": ("straight_eighth",),
    "stealth": ("straight_eighth",),
}
_LEGACY = {"swing", "bossa", "funk", "ballad"}
_GENRES: dict[Genre, str] = {
    "cinematic": r"电影配乐|電影配樂|影视配乐|影視配樂|科幻配乐|科幻配樂|电影感|電影感|\b(?:cinematic|film score)\b",
    "ambient": r"氛围音乐|氛圍音樂|环境音乐|環境音樂|空灵氛围|空靈氛圍|\b(?:ambient|drone music)\b",
    "electronic": r"电子(?:舞曲|乐|音[乐樂])|電子(?:舞曲|樂|音[乐樂])|合成器流行|\b(?:electronic|edm|techno|house music|synthpop|synthwave|trance)\b",
    "latin": r"拉丁(?:乐|樂|音乐|音樂|律动|律動|舞曲)?|萨尔萨|薩爾薩|桑巴|伦巴|倫巴|\b(?:latin|salsa|samba|rumba|mambo|cumbia)\b",
    "country": r"乡村(?:乐|音[乐樂])|鄉村(?:樂|音[乐樂])|蓝草|藍草|\b(?:country(?: music)?|bluegrass|americana)\b",
    "hip_hop": r"嘻哈|说唱|說唱|\b(?:hip[ -]?hop|trip.hop|boom bap|trap beat|lo[ -]?fi hip.hop)\b",
    "classical": r"古典音乐|古典音樂|古典风格|古典風格|巴洛克|新古典|\b(?:neoclassical|classical music|baroque|classical)\b",
    "rock": r"摇滚|搖滾|\b(?:rock(?: and roll| ?& ?roll)?|rock'n'roll)\b",
    "hard_rock": r"硬[式质]?摇滚|硬[式質]?搖滾|重摇滚|重搖滾|\bhard[ -]rock\b",
    "alternative_rock": r"(?:另类|另類|独立|獨立|朋克|后|後|英伦)摇滚|(?:另類|獨立|龐克|後|英倫)搖滾|"
    r"垃圾摇滚|垃圾搖滾|\b(?:(?:alt(?:ernative)?|indie|punk|post)[ -]rock|grunge|britpop)\b",
    "pop": r"流行(?:音乐|音樂|乐|樂|曲)?|\bpop(?: music)?\b",
    "blues": r"布鲁斯|布魯斯|蓝调|藍調|\bblues\b",
    "reggae": r"雷鬼|雷盖|雷蓋|\breggae\b",
    "disco": r"迪斯科|迪士高|\bdisco\b",
    "soul": r"灵魂乐|靈魂樂|灵魂音乐|靈魂音樂|节奏布鲁斯|節奏布魯斯|摩城|"
    r"\b(?:soul|neo[ -]soul|motown|r[&n]b)\b",
    "swing": r"摇摆|搖擺|爵士|\bswing\b|\bjazz\b",
    "bossa": r"[波巴][萨薩沙莎](?:诺瓦|諾瓦)?|\bbossa(?:[ -]nova)?\b",
    "funk": r"放克|\bfunk(?:y)?\b",
    "ballad": r"抒情|叙事曲|敘事曲|\bballad\b",
    "chiptune": r"8[ -]?bit|8[ -]?位(?:机|機)?(?:音乐|音樂|风格|風格)?|芯片(?:音乐|音樂)|红白机|紅白機"
    r"|\b(?:chiptunes?|chip music)\b",
    "platformer": r"平台跳[跃躍]|横版过关|橫版過關|像素(?:游戏|遊戲)|\b(?:platformer|side[ -]scroller)\b",
    "rpg": r"rpg|角色扮演|(?:游戏|遊戲)(?:配乐|配樂|音乐|音樂)|村庄|村莊|城镇主题|城鎮主題"
    r"|冒险(?:游戏|遊戲)?(?:配乐|配樂|音乐|音樂)|冒險(?:遊戲)?(?:配樂|音樂)"
    r"|\b(?:jrpg|town theme|overworld|video game music)\b",
    "battle": r"战斗|戰鬥|boss ?(?:战|戰|曲|音乐|音樂)|史诗(?:战斗|对决)|史詩(?:戰鬥|對決)"
    r"|\b(?:battle(?: theme| music)?|boss (?:fight|battle))\b",
    "exploration": r"探索|地下城|迷宫|迷宮|开放世界|開放世界|\b(?:exploration|dungeon|open world)\b",
    "stealth": r"潜行|潛行|悬疑|懸疑|潜入|潛入|\b(?:stealth|suspense)\b",
}
_GROOVES: dict[Groove, str] = {
    "straight_eighth": r"直[的线線拍]?[八8]分|直拍|\bstraight[ -](?:eighths?|8ths?)\b",
    "shuffle": r"三连音[八8]分|三連音[八8]分|\bshuffle\b",
    "sixteenth": r"十六分(?:音符)?(?:律动|律動|节奏|節奏|拍感)?|\b(?:sixteenth|16th)s?\b",
    "one_drop": r"\bone[ -]drop\b",
    "four_on_floor": r"四拍底鼓|四拍踢鼓|底鼓每拍(?:都打|一下)?|\bfour[ -]on[ -](?:the[ -])?floor\b",
    "half_time": r"半拍感|半速(?:律动|律動|节奏|節奏|拍感)|军鼓(?:放|打)在第?三拍|\bhalf[ -]time\b",
}
_MODES: dict[Mode, str] = {
    "minor": r"小调|小調|\b(?:natural[ -])?minor(?: key| mode)?\b",
    "major": r"大调|大調|\bmajor(?: key| mode)?\b",
    "mixolydian": r"[混米][合克][利里][底第]亚|[混米][合克][利里][底第]亞|麦克索利迪安|\bmixolydian\b",
    "dorian": r"[多杜]利[亚安亞]|[多杜]里[亚安亞]|\bdorian\b",
}
_MOOD_MODES: dict[Mode, str] = {
    "minor": r"阴郁|陰鬱|暗一点|暗一點|忧郁|憂鬱|\b(?:darker|moody|melancholic)\b",
    "major": r"明亮一点|明亮一點|明快一点|明快一點|\b(?:brighter|cheerful)\b",
}
_SEP = r"\s*[\-−–—→>/、]\s*"
_FLAT = r"[b♭]?"
_HARMONIES: dict[Harmony, str] = {
    "pop_1564": rf"(?<!\w)(?:i{_SEP}v{_SEP}vi{_SEP}iv|1{_SEP}5{_SEP}6{_SEP}4)(?!\w)",
    "minor_1645": rf"(?<!\w)(?:i{_SEP}{_FLAT}vi{_SEP}iv{_SEP}v|1{_SEP}[b♭]6{_SEP}4{_SEP}5)(?!\w)",
    "mixolydian_174": rf"(?<!\w)(?:i{_SEP}[b♭]vii{_SEP}iv|1{_SEP}[b♭]7{_SEP}4)(?!\w)",
    "minor_1767": rf"(?<!\w)(?:i{_SEP}[b♭]vii{_SEP}[b♭]vi{_SEP}[b♭]vii|1{_SEP}[b♭]7{_SEP}[b♭]6{_SEP}[b♭]7)(?!\w)",
    "soul_251": rf"(?<!\w)(?:ii(?:7)?{_SEP}v(?:7)?{_SEP}i(?:maj7)?|2{_SEP}5{_SEP}1)(?!\w)",
    "major_145": rf"(?<!\w)(?:i{_SEP}iv{_SEP}v|1{_SEP}4{_SEP}5)(?!\w)",
    "blues_8": r"(?:八|8)小节(?:蓝调|藍調|布鲁斯|布魯斯)|\b(?:eight|8)[ -]bar blues\b",
    "dorian_vamp": rf"(?:\b(?:dorian vamp)\b|多[里利][亚亞]循环|(?<!\w)i7{_SEP}iv7(?!\w))",
}
_TONIC = re.compile(
    r"(?<![a-z])([a-g][#b♭♯]?)(?=\s*(?:大调|大調|小调|小調|调(?:式)?|調(?:式)?|"
    r"多[利里][亚亞]|混合利底[亚亞]|\b(?:major|minor|mixolydian|dorian)\b))"
    r"|\b(?:key of|in the key of)\s+([a-g][#b♭♯]?)(?![a-z])",
    re.IGNORECASE | re.ASCII,
)
_KEEP_HARMONY = re.compile(
    r"(?:不要|别|別|不用|无需|無需|不必|先不)(?:更改|修改|改动?|改變|改变?|变|變|动|動|换|換)(?:原来的?|原來的?|原|现在的?|現在的?)?(?:和弦|和[声聲])"
    r"|(?:和弦|和[声聲])(?:进行|進行)?(?:保持|全部|都|先)?(?:不变|不變|不改|不换|不換|照旧|照舊)"
    r"|(?:保留|保持|沿用|继续|繼續|照用)(?:现有|現有|当前|當前|原有|原来|原來|原|现在|現在|我)?的?(?:和弦|和[声聲])"
    r"|\b(?:keep|preserve|leave) (?:the |my |current |original )*(?:chord(?:s| progression)|harmony)\b"
    r"|\b(?:without changing|do not change|don't change) (?:the )?(?:chords|harmony)\b",
    re.IGNORECASE,
)
_ONLY_RHYTHM = re.compile(
    r"只(?:改变?|換|换|調整|调整|改一下)(?:拍感|节奏|節奏|律动|律動)|\b(?:only|just) (?:change )?(?:the )?(?:rhythm|groove)\b"
)
_HARMONIC_INTENT = re.compile(
    r"(?:和弦|和[声聲]|进行|進行)(?:改用|改成|换成|換成|走向|色彩)|"
    r"(?:换|換|改|不同|丰富|豐富|不一样|不一樣)[^,.，。；;]{0,6}(?:和弦|和[声聲])(?:进行|進行)?|"
    r"\b(?:change (?:the )?chord progression|reharmonize|reharmonise)\b"
)
_CLAUSE = re.compile(r"[,.!?;，。！？；\n]|而是|(?:就|请|請)?改成|\binstead\b|\bbut\b")
_NEGATION = re.compile(
    r"不要|不想|不需要|不用|无需|無需|别|別|避免|\b(?:no|not|without|don't|avoid)\b"
)
_REFERENCE = re.compile(
    r"(?<!不)(?:像|类似(?:于)?|類似(?:於)?|参考|參考|模仿|致敬)\s*(?P<cn>[^,.!?;，。！？；\n]{1,72}?)"
    r"(?=的?(?:风格|風格|那种|那種|一样|一樣|一点|一點|味道|风味|風味)|[,.!?;，。！？；\n]|$)"
    r"|\b(?:in the style of|inspired by|sounds? like|style to|like)\s+(?P<en>[^,.!?;，。！？；\n]{1,72}?)(?=\bbut\b|[,.!?;，。！？；\n]|$)",
    re.IGNORECASE,
)
_TRAILING_STYLE = re.compile(
    r"^(?:来点|來點|试试|試試|改成|切换成|切換成)?\s*([^,.!?;，。！？；\n]{2,60}?)"
    r"(?:的(?:音乐|音樂)?|式)?(?:风格|風格|曲风|曲風|配乐风格|配樂風格)"
    r"(?=\s*(?:[,.!?;，。！？；\n]|$))"
)
_TITLE = re.compile(
    r"^[《〈『「](?P<title>[^》〉』」\n]{1,70})[》〉』」](?:\s*(?:配[乐樂]|原声|原聲))?$"
)
_STYLE_CUE = re.compile(
    r"风格|風格|曲风|曲風|配乐|配樂|律动|律動|质感|質感|音乐|音樂|曲调|曲調|脉冲|脈衝"
    r"|氛围|氛圍|空灵|空靈|编曲|編曲|\b(?:style|vibe|soundtrack|groove|music)\b",
    re.IGNORECASE,
)
_RHYTHM_LABEL = re.compile(
    r"\s*(?:的)?(?:律动|律動|鼓点|鼓點|节奏|節奏|beat|groove)", re.IGNORECASE
)
_ARTIST_NAME = re.compile(r"(?:[A-Z][\w'’&/.-]*)(?:\s+(?:[A-Z][\w'’&/.-]*|&|\+)){1,4}")


@dataclass(frozen=True, slots=True)
class Reference:
    name: str
    genre: Genre
    harmony: Harmony
    aliases: str
    groove: Groove | None = None


_REFERENCES = (
    Reference(
        "《星际穿越》配乐",
        "cinematic",
        "minor_1767",
        r"星[际際]穿越|星[际際]效[应應]|\binterstellar\b",
    ),
    Reference(
        "The Beatles",
        "rock",
        "major_145",
        r"披头士|披頭士|甲壳虫(?:乐队)?|甲殼蟲(?:樂隊)?|\bbeatles\b",
    ),
    Reference("Queen", "rock", "pop_1564", r"皇后(?:乐队|樂隊)?|\bqueen\b"),
    Reference(
        "Nirvana",
        "alternative_rock",
        "minor_1767",
        r"涅[槃盘盤](?:乐队|樂隊)?|\bnirvana\b",
        "straight_eighth",
    ),
    Reference("AC/DC", "hard_rock", "mixolydian_174", r"\bac\s*[/\-]?\s*dc\b"),
    Reference("Beyond", "rock", "pop_1564", r"\bbeyond\b"),
    Reference(
        "Bob Marley",
        "reggae",
        "major_145",
        r"鲍勃[·・. ]?马利|鮑勃[·・. ]?馬利|\b(?:bob marley|the wailers)\b",
    ),
    Reference("Daft Punk", "disco", "dorian_vamp", r"蠢朋克|傻瓜庞克|傻瓜龐克|\bdaft punk\b"),
    Reference("Radiohead", "alternative_rock", "minor_1767", r"电台司令|電台司令|\bradiohead\b"),
    Reference("Led Zeppelin", "hard_rock", "blues_8", r"齐柏林飞船|齊柏林飛船|\bled zeppelin\b"),
    Reference("Pink Floyd", "rock", "dorian_vamp", r"平克·?弗洛伊德|\bpink floyd\b", "half_time"),
    Reference(
        "The Clash",
        "alternative_rock",
        "mixolydian_174",
        r"冲撞乐队|衝撞樂隊|\bthe clash\b",
        "straight_eighth",
    ),
    Reference("Oasis", "rock", "pop_1564", r"绿洲(?:乐队|樂隊)|綠洲(?:乐队|樂隊)|\boasis\b"),
    Reference("Coldplay", "pop", "pop_1564", r"酷玩(?:乐队|樂隊)?|\bcoldplay\b"),
    Reference("Metallica", "hard_rock", "minor_1767", r"金属乐队|金屬樂隊|\bmetallica\b"),
    Reference(
        "Green Day",
        "alternative_rock",
        "major_145",
        r"绿日(?:乐队|樂隊)?|綠日(?:乐队|樂隊)?|\bgreen day\b",
        "straight_eighth",
    ),
    Reference("Billie Holiday", "swing", "soul_251", r"比莉[·・. ]?霍利[戴代]|\bbillie holiday\b"),
    Reference(
        "Stevie Wonder",
        "soul",
        "soul_251",
        r"史蒂维[·・. ]?旺德|史蒂維[·・. ]?旺德|\bstevie wonder\b",
    ),
    Reference("Jamiroquai", "funk", "dorian_vamp", r"杰米罗奎|\bjamiroquai\b"),
    Reference("万能青年旅店", "alternative_rock", "minor_1645", r"[万萬]能青年旅店"),
    Reference("五条人", "rock", "major_145", r"五[条條]人"),
    Reference("黑豹乐队", "hard_rock", "mixolydian_174", r"黑豹(?:乐队|樂隊)"),
    Reference("唐朝乐队", "hard_rock", "minor_1767", r"唐朝(?:乐队|樂隊)"),
)


@dataclass(frozen=True, slots=True)
class StyleIntent:
    genre: Genre | None
    groove: Groove | None
    mode: Mode | None
    harmony: Harmony | None
    tonic: str | None
    reference: str | None
    curated: Reference | None
    preserve_harmony: bool
    change_harmony: bool
    rich: bool
    descriptive: bool = False
    blend_families: tuple[Genre, ...] = ()
    rhythm_family: Genre | None = None

    @property
    def blended(self) -> bool:
        return len(self.blend_families) > 1

    def context(self) -> dict:
        genre = self.genre or (self.curated.genre if self.curated else None)
        recipe = None
        if genre:
            defaults = _DEFAULTS[genre]
            harmony = self.curated.harmony if self.curated else defaults.harmony
            recipe = {
                "genre": genre,
                "groove": defaults.groove,
                "harmony": harmony,
                "mode": HARMONY_MODES[harmony],
            }
        return {
            "reference": self.reference,
            "groove": self.groove,
            "mode": self.mode,
            "harmony": "keep" if self.preserve_harmony else self.harmony,
            "tonic": self.tonic,
            "typical_recipe": recipe,
            "genre_blend": self.blend_families or None,
        }


def _allowed(text: str, start: int) -> bool:
    return not _NEGATION.search(_CLAUSE.split(text[:start])[-1])


def _last_match[T](text: str, patterns: dict[T, str]) -> T | None:
    found = [
        (match.start(), match.end(), value)
        for value, pattern in patterns.items()
        for match in re.finditer(pattern, text, re.ASCII)
        if _allowed(text, match.start())
    ]
    if not found:
        return None
    # Longest match wins where genre descriptions overlap (e.g. 'hard rock').
    return max(found, key=lambda entry: (entry[1], entry[1] - entry[0]))[2]


def style_intent(instruction: str) -> StyleIntent:
    original = unicodedata.normalize("NFKC", instruction).strip()
    text = original.casefold()
    genre = _last_match(text, _GENRES)
    groove = _last_match(text, _GROOVES)
    mode = _last_match(text, _MODES)
    harmony = _last_match(text, _HARMONIES)
    tonics = [match for match in _TONIC.finditer(text) if _allowed(text, match.start())]
    tonic = next(value for value in tonics[-1].groups() if value) if tonics else None
    curated = next(
        (
            item
            for item in _REFERENCES
            if any(
                _allowed(text, match.start()) for match in re.finditer(item.aliases, text, re.ASCII)
            )
        ),
        None,
    )
    reference = curated.name if curated else None
    if reference is None:
        if named := _REFERENCE.search(original):
            if _allowed(text, named.start()):
                candidate = (named["cn"] or named["en"]).strip(" 『』「」‘’“”\"'")
                if candidate and not any(
                    re.fullmatch(pattern, candidate.casefold(), re.ASCII)
                    for pattern in _GENRES.values()
                ):
                    reference = candidate
        elif genre is None:
            if named := _TRAILING_STYLE.search(original):
                if _allowed(text, named.start(1)):
                    reference = named[1].strip(" 《》〈〉『』「」‘’“”\"'")
            elif quoted := _TITLE.fullmatch(original):
                reference = quoted["title"]
            elif re.fullmatch(
                r"[\u4e00-\u9fff]{1,24}(?:乐队|樂隊)", original
            ) or _ARTIST_NAME.fullmatch(original):
                reference = original
    descriptive = bool(_STYLE_CUE.search(text))
    # Discard shorter overlapping labels such as "rock" inside "hard rock".
    matches = [
        (match.start(), match.end(), family)
        for family, pattern in _GENRES.items()
        for match in re.finditer(pattern, text, re.ASCII)
        if _allowed(text, match.start())
    ]
    distinct_matches = sorted(
        (start, end, family)
        for start, end, family in matches
        if not any(
            left <= start <= end <= right and (right - left) > end - start
            for left, right, _ in matches
        )
    )
    families = tuple(dict.fromkeys(family for _, _, family in distinct_matches))
    rhythm_family = (
        next(
            (
                family
                for _, end, family in reversed(distinct_matches)
                if _RHYTHM_LABEL.match(text[end:])
            ),
            None,
        )
        if len(families) > 1
        else None
    )
    preserve = bool(
        _ONLY_RHYTHM.search(text)
        or any(_allowed(text, match.start()) for match in _KEEP_HARMONY.finditer(text))
    )
    change_harmony = any(_allowed(text, match.start()) for match in _HARMONIC_INTENT.finditer(text))
    if genre or reference or change_harmony:
        mode = mode or _last_match(text, _MOOD_MODES)
    rich = bool(
        (genre is not None and genre not in _LEGACY)
        or groove
        or mode
        or harmony
        or tonic
        or reference
        or change_harmony
        or len(families) > 1
        or descriptive
        and genre is None
    )
    return StyleIntent(
        genre,
        groove,
        mode,
        harmony,
        tonic,
        reference,
        curated,
        preserve,
        change_harmony,
        rich,
        descriptive,
        families if len(families) > 1 else (),
        rhythm_family,
    )


def _default_harmony(genre: Genre, mode: Mode | None) -> Harmony:
    harmony = _DEFAULTS[genre].harmony
    if mode is None or HARMONY_MODES[harmony] == mode:
        return harmony
    if mode == "minor":
        return (
            "minor_1767"
            if genre in {"rock", "hard_rock", "alternative_rock", "reggae"}
            else "minor_1645"
        )
    if mode == "major":
        return (
            "major_145"
            if genre in {"rock", "hard_rock", "alternative_rock", "reggae"}
            else "pop_1564"
        )
    return "dorian_vamp" if mode == "dorian" else "mixolydian_174"


def _tonic_name(tonic: str) -> str:
    return tonic[0].upper() + tonic[1:].translate(str.maketrans("♭♯", "b#"))


def arrange_style(
    intent: StyleIntent,
    current: ConductorPlan,
    choice: StyleChoice | None,
) -> tuple[ConductorPlan, Harmony]:
    active = current.profile
    artist = intent.curated
    model_genre = choice.genre if choice and choice.genre != "keep" else None
    inferred = model_genre if not artist and (intent.reference or intent.descriptive) else None
    blend_lead = (
        next((family for family in intent.blend_families if family != intent.rhythm_family), None)
        if intent.rhythm_family
        else model_genre
        if model_genre in intent.blend_families
        else None
    )
    new_genre = blend_lead or intent.genre or (artist.genre if artist else None) or inferred
    target = new_genre or (active.genre if active else current.style)
    defaults = _DEFAULTS[target]
    typical_grooves = (defaults.groove, *_ALTERNATE_GROOVES.get(target, ()))
    model_groove = choice.groove if choice and choice.groove != "keep" else None
    if new_genre and not intent.blended and model_groove not in typical_grooves:
        model_groove = None
    groove = (
        intent.groove
        or (_DEFAULTS[intent.rhythm_family].groove if intent.rhythm_family else None)
        or ((artist.groove or defaults.groove) if artist and not intent.blended else None)
        or model_groove
        or (active.groove if active and new_genre is None else defaults.groove)
    )
    model_harmony = choice.harmony if choice and choice.harmony != "keep" else None
    want_model_harmony = bool(new_genre or intent.change_harmony) and not artist
    mode = intent.mode or (
        choice.mode if choice and choice.mode != "keep" and want_model_harmony else None
    )
    harmony = intent.harmony or (artist.harmony if artist else None)
    harmony = harmony or (model_harmony if want_model_harmony else None)
    if intent.mode and harmony and HARMONY_MODES[harmony] != intent.mode and not intent.harmony:
        harmony = _default_harmony(target, intent.mode)
    if not harmony and (new_genre or mode or intent.tonic):
        current_harmony = active.harmony if active and active.harmony != "keep" else None
        if intent.tonic and not (new_genre or mode):
            harmony = current_harmony
        harmony = harmony or _default_harmony(target, mode)
    if intent.preserve_harmony:
        harmony = None
    # Keep existing music for an unknown reference unless the user specified playable traits.
    if not (new_genre or intent.groove or model_groove or harmony):
        return current, "keep"

    prior_harmony = active.harmony if active else "keep"
    model_selected = model_genre is not None and (
        (intent.blended and not artist) or (inferred and not intent.genre)
    )
    profile = MusicProfile(
        genre=target,
        groove=groove,
        mode=HARMONY_MODES[harmony]
        if harmony
        else (active.mode if active else HARMONY_MODES[defaults.harmony]),
        harmony=harmony or ("keep" if intent.preserve_harmony else prior_harmony),
        tonic=_tonic_name(intent.tonic)
        if intent.tonic and harmony
        else (active.tonic if active else "C"),
        reference=intent.reference
        if artist or inferred
        else (active.reference if active and not new_genre else None),
        basis="reference"
        if artist
        else "inferred"
        if model_selected
        else "genre"
        if intent.genre
        else "theory",
    )
    plan = (
        defaults.arrange(current, profile)
        if new_genre
        else current.model_copy(update={"profile": profile})
    )
    return plan, harmony or "keep"
