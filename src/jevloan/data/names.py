"""Synthetic Indian names, employers, businesses, cities and street fragments, native-script renderings for a
subset of them, and format-valid synthetic identifiers (PAN, Aadhaar, phone, account, GSTIN, email).

Everything here is invented. Company names are fictional; personal names are combinations of common given names and
surnames, so a generated name can coincide with a real person, and random phone or Aadhaar numbers can coincide with
real ones. None of it is a real borrower. See docs/DATA_CARD.md.

This module has no dependency on the PII package: the name lexicon here is the generator's own.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# ------------------------------------------------------------------------------------------------ person names

REGIONS = ("north", "tamil", "bengali", "marathi", "telugu")

# Names that also have a native-script rendering (used for the lang_doc_script subset). script_lang -> gender -> latin -> native
NATIVE_FIRST: dict[str, dict[str, dict[str, str]]] = {
    "ta": {
        "M": {
            "Karthik": "கார்த்திக்", "Murugan": "முருகன்", "Selvam": "செல்வம்", "Ramesh": "ரமேஷ்",
            "Suresh": "சுரேஷ்", "Saravanan": "சரவணன்", "Arun": "அருண்", "Vijay": "விஜய்",
            "Senthil": "செந்தில்", "Gopal": "கோபால்", "Prakash": "பிரகாஷ்", "Manikandan": "மணிகண்டன்",
            "Ganesh": "கணேஷ்", "Mohan": "மோகன்",
        },
        "F": {
            "Lakshmi": "லட்சுமி", "Meenakshi": "மீனாட்சி", "Priya": "பிரியா", "Divya": "திவ்யா",
            "Kavitha": "கவிதா", "Anitha": "அனிதா", "Deepa": "தீபா", "Revathi": "ரேவதி", "Uma": "உமா",
            "Saranya": "சரண்யா", "Nithya": "நித்யா", "Janani": "ஜனனி", "Geetha": "கீதா",
        },
    },
    "bn": {
        "M": {
            "Sourav": "সৌরভ", "Debashis": "দেবাশীষ", "Anirban": "অনির্বাণ", "Subrata": "সুব্রত",
            "Partha": "পার্থ", "Arindam": "অরিন্দম", "Tapas": "তাপস", "Sumit": "সুমিত", "Rajib": "রাজীব",
            "Abhijit": "অভিজিৎ", "Soumya": "সৌম্য", "Tanmoy": "তন্ময়", "Indranil": "ইন্দ্রনীল",
        },
        "F": {
            "Sunita": "সুনীতা", "Moumita": "মৌমিতা", "Ananya": "অনন্যা", "Priyanka": "প্রিয়াঙ্কা",
            "Sanchita": "সঞ্চিতা", "Rina": "রীনা", "Shreya": "শ্রেয়া", "Mitali": "মিতালি",
            "Nandini": "নন্দিনী", "Piyali": "পিয়ালী", "Sharmila": "শর্মিলা", "Debjani": "দেবযানী",
            "Madhumita": "মধুমিতা",
        },
    },
    "hi": {
        "M": {
            "Rahul": "राहुल", "Amit": "अमित", "Sanjay": "संजय", "Vikram": "विक्रम", "Anil": "अनिल",
            "Rajesh": "राजेश", "Manoj": "मनोज", "Deepak": "दीपक", "Ashok": "अशोक", "Vinod": "विनोद",
            "Suresh": "सुरेश", "Rohit": "रोहित", "Ajay": "अजय", "Nitin": "नितिन",
        },
        "F": {
            "Pooja": "पूजा", "Sunita": "सुनीता", "Neha": "नेहा", "Anjali": "अंजलि", "Kavita": "कविता",
            "Rekha": "रेखा", "Sneha": "स्नेहा", "Priya": "प्रिया", "Meena": "मीना", "Anita": "अनीता",
            "Seema": "सीमा", "Ritu": "रितु", "Shweta": "श्वेता", "Nisha": "निशा",
        },
    },
    "mr": {
        "M": {
            "Sachin": "सचिन", "Prashant": "प्रशांत", "Sandip": "संदीप", "Nilesh": "नीलेश", "Amol": "अमोल",
            "Vishal": "विशाल", "Rohan": "रोहन", "Santosh": "संतोष", "Ajit": "अजित", "Pravin": "प्रवीण",
            "Sameer": "समीर", "Ganesh": "गणेश", "Tushar": "तुषार",
        },
        "F": {
            "Sayali": "सायली", "Manasi": "मानसी", "Aarti": "आरती", "Snehal": "स्नेहल", "Pallavi": "पल्लवी",
            "Vaishali": "वैशाली", "Mayuri": "मयुरी", "Trupti": "तृप्ती", "Smita": "स्मिता",
            "Rupali": "रुपाली", "Madhuri": "माधुरी", "Sonali": "सोनाली", "Vidya": "विद्या",
        },
    },
}

NATIVE_SURNAMES: dict[str, dict[str, str]] = {
    "ta": {
        "Subramanian": "சுப்பிரமணியன்", "Iyer": "ஐயர்", "Krishnan": "கிருஷ்ணன்", "Raman": "ராமன்",
        "Venkatesan": "வெங்கடேசன்", "Natarajan": "நடராஜன்", "Pillai": "பிள்ளை", "Chandran": "சந்திரன்",
        "Rajan": "ராஜன்", "Sekar": "சேகர்", "Annamalai": "அண்ணாமலை", "Ramasamy": "ராமசாமி",
        "Sundaram": "சுந்தரம்", "Narayanan": "நாராயணன்",
    },
    "bn": {
        "Banerjee": "ব্যানার্জী", "Chatterjee": "চ্যাটার্জী", "Mukherjee": "মুখার্জী", "Ghosh": "ঘোষ",
        "Das": "দাস", "Sen": "সেন", "Roy": "রায়", "Dutta": "দত্ত", "Bose": "বসু", "Chakraborty": "চক্রবর্তী",
        "Saha": "সাহা", "Mondal": "মণ্ডল", "Biswas": "বিশ্বাস", "Sarkar": "সরকার", "Ganguly": "গাঙ্গুলী",
    },
    "hi": {
        "Sharma": "शर्मा", "Verma": "वर्मा", "Gupta": "गुप्ता", "Singh": "सिंह", "Yadav": "यादव",
        "Mishra": "मिश्रा", "Tiwari": "तिवारी", "Pandey": "पांडेय", "Chauhan": "चौहान", "Agarwal": "अग्रवाल",
        "Kapoor": "कपूर", "Saxena": "सक्सेना", "Joshi": "जोशी", "Thakur": "ठाकुर",
    },
    "mr": {
        "Patil": "पाटील", "Deshmukh": "देशमुख", "Kulkarni": "कुलकर्णी", "Jadhav": "जाधव", "Pawar": "पवार",
        "Shinde": "शिंदे", "More": "मोरे", "Kale": "काळे", "Gaikwad": "गायकवाड", "Bhosale": "भोसले",
        "Chavan": "चव्हाण", "Kadam": "कदम", "Deshpande": "देशपांडे", "Sawant": "सावंत",
    },
}

# native-script language -> the name region its names belong to
NATIVE_LANG_REGION = {"ta": "tamil", "bn": "bengali", "hi": "north", "mr": "marathi"}
LANG_SCRIPT = {"ta": "tamil", "bn": "bengali", "hi": "devanagari", "mr": "devanagari"}

_EXTRA_FIRST: dict[str, dict[str, list[str]]] = {
    "north": {
        "M": ["Varun", "Mohit", "Gaurav", "Harish", "Naveen", "Kunal", "Yogesh", "Dinesh", "Arvind", "Mukesh", "Pradeep", "Sandeep", "Pankaj"],
        "F": ["Komal", "Swati", "Preeti", "Renu", "Sarita", "Manju", "Geeta", "Poonam", "Radha", "Divya", "Ankita", "Kiran"],
    },
    "tamil": {
        "M": ["Bala", "Sundar", "Dinesh", "Harish", "Kumar", "Rajesh", "Sathish", "Venkat", "Ashwin", "Naveen"],
        "F": ["Bhuvana", "Mythili", "Vasanthi", "Sudha", "Kalyani", "Padma", "Shanthi", "Indhu", "Keerthana", "Abirami", "Gayathri"],
    },
    "bengali": {
        "M": ["Ritwik", "Sayan", "Prasenjit", "Biswajit", "Amitava", "Kaushik", "Subhajit"],
        "F": ["Sudipta", "Tanushree", "Rupa", "Paromita", "Swagata", "Ipsita", "Mousumi"],
    },
    "marathi": {
        "M": ["Mangesh", "Swapnil", "Omkar", "Rajendra", "Sagar", "Vaibhav", "Yogesh"],
        "F": ["Rutuja", "Shubhangi", "Kalyani", "Anuja", "Jyoti", "Sunanda", "Namrata"],
    },
    "telugu": {
        "M": ["Venkatesh", "Srinivas", "Ramakrishna", "Naresh", "Satish", "Praveen", "Chaitanya", "Harsha", "Mahesh", "Ravi", "Kiran", "Sai", "Pavan", "Anand", "Bharath", "Vamsi", "Nagesh", "Prasad", "Raju", "Hemanth"],
        "F": ["Sravani", "Lavanya", "Swathi", "Harika", "Sowmya", "Bhavani", "Padmaja", "Anusha", "Madhavi", "Sirisha", "Vasavi", "Deepthi", "Pravallika", "Mounika", "Sandhya", "Rajitha", "Jyothi", "Aparna", "Keerthi", "Suma"],
    },
}
_EXTRA_SURNAMES: dict[str, list[str]] = {
    "north": ["Bansal", "Mehra", "Arora", "Rathore", "Chopra", "Khanna", "Bhatia", "Jain", "Goyal", "Dubey", "Trivedi", "Sinha", "Malhotra", "Srivastava"],
    "tamil": ["Murthy", "Balasubramanian", "Palanisamy", "Thangavel", "Govindarajan", "Ganesan", "Muthu", "Selvaraj", "Arumugam", "Swaminathan"],
    "bengali": ["Majumdar", "Bhattacharya", "Dey", "Halder", "Paul", "Nandi", "Pal", "Chowdhury"],
    "marathi": ["Phadke", "Lokhande", "Ghadge", "Thorat", "Salunkhe", "Kamble", "Mane", "Bhide"],
    "telugu": ["Reddy", "Naidu", "Rao", "Chowdary", "Sastry", "Raju", "Goud", "Babu", "Nagaraju", "Setty", "Varma", "Prasad"],
}


def _build_pools() -> tuple[dict[str, dict[str, list[str]]], dict[str, list[str]]]:
    first: dict[str, dict[str, list[str]]] = {}
    last: dict[str, list[str]] = {}
    lang_of_region = {v: k for k, v in NATIVE_LANG_REGION.items()}
    for region in REGIONS:
        first[region] = {}
        for g in ("M", "F"):
            names = list(_EXTRA_FIRST[region][g])
            lang = lang_of_region.get(region)
            if lang:
                names = list(NATIVE_FIRST[lang][g]) + names
            first[region][g] = sorted(set(names))
        surnames = list(_EXTRA_SURNAMES[region])
        lang = lang_of_region.get(region)
        if lang:
            surnames = list(NATIVE_SURNAMES[lang]) + surnames
        last[region] = sorted(set(surnames))
    return first, last


FIRST_NAMES, SURNAMES = _build_pools()

ALL_PERSON_TOKENS = frozenset(
    {n for r in FIRST_NAMES.values() for g in r.values() for n in g} | {n for r in SURNAMES.values() for n in r}
)


@dataclass(frozen=True)
class Person:
    name: str
    native: str | None
    gender: str
    region: str

    @property
    def first(self) -> str:
        return self.name.split(" ", 1)[0]

    @property
    def last(self) -> str:
        return self.name.split(" ", 1)[1]


def draw_person(rng: random.Random, region: str, gender: str, native_lang: str | None = None) -> Person:
    """A synthetic person. With ``native_lang`` (ta/bn/hi/mr) the name comes from the subset that has a
    native-script rendering, and ``native`` is filled in."""
    g = gender if gender in ("M", "F") else rng.choice(["M", "F"])
    if native_lang:
        first_latin = rng.choice(sorted(NATIVE_FIRST[native_lang][g]))
        last_latin = rng.choice(sorted(NATIVE_SURNAMES[native_lang]))
        native = f"{NATIVE_FIRST[native_lang][g][first_latin]} {NATIVE_SURNAMES[native_lang][last_latin]}"
        return Person(f"{first_latin} {last_latin}", native, gender, NATIVE_LANG_REGION[native_lang])
    return Person(f"{rng.choice(FIRST_NAMES[region][g])} {rng.choice(SURNAMES[region])}", None, gender, region)


def native_form(name: str, native_lang: str) -> str | None:
    """Native-script rendering of a Latin 'First Last' name if both parts are in the native tables."""
    first, _, last = name.partition(" ")
    for g in ("M", "F"):
        if first in NATIVE_FIRST[native_lang][g] and last in NATIVE_SURNAMES[native_lang]:
            return f"{NATIVE_FIRST[native_lang][g][first]} {NATIVE_SURNAMES[native_lang][last]}"
    return None


# ------------------------------------------------------------------------------------------------ organisations

# Words that never appear in the person pools (asserted by a test), so an org name cannot be mistaken for a person.
ORG_PREFIXES = [
    "Vardhan", "Kaveri", "Himadri", "Sahyadri", "Aravali", "Nilgiri", "Vindhya", "Konark", "Meridian", "Zenith",
    "Horizon", "Apex", "Pinnacle", "Summit", "Crestline", "Bluepeak", "Ironwood", "Silverline", "Goldcrest",
    "Brightpath", "Truenorth", "Evergreen", "Oakridge", "Riverbend", "Stonebridge", "Northstar", "Lotus", "Banyan",
    "Peepal", "Chinar", "Deodar", "Teak", "Mangrove", "Cedarwood", "Trident", "Compass", "Anchor", "Beacon",
    "Cascade", "Delta", "Ember", "Falcon", "Granite", "Harbor", "Indus", "Juniper", "Keystone", "Lantern",
    "Monsoon", "Nimbus", "Orchid", "Pearl", "Quartz", "Radiant", "Sapphire", "Titan", "Umbra", "Vertex",
    "Willow", "Zephyr",
]
EMPLOYER_SECTORS = [
    "Infotech", "Logistics", "Textiles", "Pharma", "Foods", "Motors", "Engineering", "Constructions", "Retail",
    "Healthcare", "Agro", "Polymers", "Steel", "Packaging", "Energy", "Telecom", "Analytics", "Chemicals",
    "Hospitality", "Shipping", "Electricals", "Fabrication", "Biotech", "Consulting", "Media",
]
EMPLOYER_SUFFIXES = [("Pvt Ltd", 60), ("Ltd", 15), ("LLP", 10), ("Private Limited", 10), ("Industries", 5)]
BUSINESS_TRADES = [
    "Traders", "Enterprises", "Fabricators", "Precision Components", "Agro Products", "Print House", "Stationers",
    "Bakers", "Auto Parts", "Plastics", "Electricals", "Textiles", "Food Products", "Packaging", "Hardware",
    "Furniture", "Engineering Works", "Dairy", "Garments", "Chemicals", "Cold Storage", "Tools", "Castings",
]
BUSINESS_SUFFIXES = ["", "", "& Sons", "Co", "Works", "Industries", "Associates"]
DESIGNATIONS = [
    "Senior Executive", "Assistant Manager", "Team Lead", "Software Engineer", "Accounts Officer", "Sales Manager",
    "Operations Executive", "Project Engineer", "Business Analyst", "Quality Inspector", "HR Executive",
    "Branch Coordinator", "Senior Associate", "Deputy Manager", "Technical Specialist",
]
LOW_DESIGNATIONS = ["Office Attendant", "Peon", "Security Guard", "Helper", "Driver", "Data Entry Operator"]


def _weighted(rng: random.Random, pairs: list[tuple[str, int]]) -> str:
    total = sum(w for _, w in pairs)
    x = rng.random() * total
    for item, w in pairs:
        x -= w
        if x < 0:
            return item
    return pairs[-1][0]


def draw_employer(rng: random.Random) -> str:
    return f"{rng.choice(ORG_PREFIXES)} {rng.choice(EMPLOYER_SECTORS)} {_weighted(rng, EMPLOYER_SUFFIXES)}"


def draw_business(rng: random.Random, entity: str | None = None) -> str:
    """A fictional business name. ``entity`` may add a legal suffix (Pvt Ltd, LLP)."""
    base = f"{rng.choice(ORG_PREFIXES)} {rng.choice(BUSINESS_TRADES)}"
    suffix = entity if entity else rng.choice(BUSINESS_SUFFIXES)
    return f"{base} {suffix}".strip()


def org_slug(name: str) -> str:
    return "".join(ch for ch in name.lower().split(" ")[0] + name.lower().split(" ")[1][:12] if ch.isalnum())


# ------------------------------------------------------------------------------------------------ cities and addresses


@dataclass(frozen=True)
class City:
    name: str
    state: str
    gst_code: str
    pin_prefixes: tuple[str, ...]
    tier: int
    region: str  # which name pool residents draw from
    langs: tuple[str, ...]  # languages of residents (en is always allowed)
    native: dict[str, str]  # script_lang -> city name in that script


STATE_NATIVE = {
    "Tamil Nadu": {"ta": "தமிழ்நாடு"},
    "West Bengal": {"bn": "পশ্চিমবঙ্গ"},
    "Maharashtra": {"mr": "महाराष्ट्र", "hi": "महाराष्ट्र"},
    "Delhi": {"hi": "दिल्ली"},
    "Uttar Pradesh": {"hi": "उत्तर प्रदेश"},
    "Rajasthan": {"hi": "राजस्थान"},
    "Madhya Pradesh": {"hi": "मध्य प्रदेश"},
    "Bihar": {"hi": "बिहार"},
}

CITIES: list[City] = [
    City("Mumbai", "Maharashtra", "27", ("400",), 1, "marathi", ("mr", "hi"), {"mr": "मुंबई", "hi": "मुंबई"}),
    City("Delhi", "Delhi", "07", ("110",), 1, "north", ("hi",), {"hi": "दिल्ली"}),
    City("Bengaluru", "Karnataka", "29", ("560",), 1, "telugu", (), {}),
    City("Chennai", "Tamil Nadu", "33", ("600",), 1, "tamil", ("ta",), {"ta": "சென்னை"}),
    City("Kolkata", "West Bengal", "19", ("700",), 1, "bengali", ("bn",), {"bn": "কলকাতা"}),
    City("Hyderabad", "Telangana", "36", ("500",), 1, "telugu", ("te",), {}),
    City("Pune", "Maharashtra", "27", ("411",), 1, "marathi", ("mr",), {"mr": "पुणे", "hi": "पुणे"}),
    City("Ahmedabad", "Gujarat", "24", ("380",), 1, "north", (), {}),
    City("Jaipur", "Rajasthan", "08", ("302",), 2, "north", ("hi",), {"hi": "जयपुर"}),
    City("Lucknow", "Uttar Pradesh", "09", ("226",), 2, "north", ("hi",), {"hi": "लखनऊ"}),
    City("Coimbatore", "Tamil Nadu", "33", ("641",), 2, "tamil", ("ta",), {"ta": "கோயம்புத்தூர்"}),
    City("Madurai", "Tamil Nadu", "33", ("625",), 2, "tamil", ("ta",), {"ta": "மதுரை"}),
    City("Nagpur", "Maharashtra", "27", ("440",), 2, "marathi", ("mr",), {"mr": "नागपूर", "hi": "नागपुर"}),
    City("Indore", "Madhya Pradesh", "23", ("452",), 2, "north", ("hi",), {"hi": "इंदौर"}),
    City("Bhopal", "Madhya Pradesh", "23", ("462",), 2, "north", ("hi",), {"hi": "भोपाल"}),
    City("Patna", "Bihar", "10", ("800",), 2, "north", ("hi",), {"hi": "पटना"}),
    City("Visakhapatnam", "Andhra Pradesh", "37", ("530",), 2, "telugu", ("te",), {}),
    City("Siliguri", "West Bengal", "19", ("734",), 2, "bengali", ("bn",), {"bn": "শিলিগুড়ি"}),
    City("Durgapur", "West Bengal", "19", ("713",), 2, "bengali", ("bn",), {"bn": "দুর্গাপুর"}),
    City("Nashik", "Maharashtra", "27", ("422",), 2, "marathi", ("mr",), {"mr": "नाशिक", "hi": "नाशिक"}),
    City("Tiruchirappalli", "Tamil Nadu", "33", ("620",), 2, "tamil", ("ta",), {"ta": "திருச்சிராப்பள்ளி"}),
    City("Salem", "Tamil Nadu", "33", ("636",), 3, "tamil", ("ta",), {"ta": "சேலம்"}),
    City("Kolhapur", "Maharashtra", "27", ("416",), 3, "marathi", ("mr",), {"mr": "कोल्हापूर", "hi": "कोल्हापुर"}),
    City("Vijayawada", "Andhra Pradesh", "37", ("520",), 3, "telugu", ("te",), {}),
    City("Kanpur", "Uttar Pradesh", "09", ("208",), 3, "north", ("hi",), {"hi": "कानपुर"}),
    City("Erode", "Tamil Nadu", "33", ("638",), 3, "tamil", ("ta",), {"ta": "ஈரோடு"}),
    City("Tirunelveli", "Tamil Nadu", "33", ("627",), 3, "tamil", ("ta",), {"ta": "திருநெல்வேலி"}),
    City("Warangal", "Telangana", "36", ("506",), 3, "telugu", ("te",), {}),
    City("Asansol", "West Bengal", "19", ("713",), 3, "bengali", ("bn",), {"bn": "আসানসোল"}),
    City("Guntur", "Andhra Pradesh", "37", ("522",), 3, "telugu", ("te",), {}),
]
CITY_BY_NAME = {c.name: c for c in CITIES}


def cities_for_language(lang: str) -> list[City]:
    """Cities whose residents can speak ``lang``. English speakers can live anywhere."""
    if lang == "en":
        return CITIES
    return [c for c in CITIES if lang in c.langs]


def draw_city(rng: random.Random, lang: str) -> City:
    pool = cities_for_language(lang)
    weights = [{1: 5, 2: 3, 3: 1.5}[c.tier] for c in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


# street fragment: Latin -> {ta, bn, dev}
STREETS: list[tuple[str, dict[str, str]]] = [
    ("Gandhi Nagar", {"ta": "காந்தி நகர்", "bn": "গান্ধী নগর", "dev": "गांधी नगर"}),
    ("Nehru Street", {"ta": "நேரு தெரு", "bn": "নেহরু স্ট্রিট", "dev": "नेहरू स्ट्रीट"}),
    ("Temple Road", {"ta": "கோயில் சாலை", "bn": "মন্দির রোড", "dev": "मंदिर रोड"}),
    ("Station Road", {"ta": "ரயில் நிலைய சாலை", "bn": "স্টেশন রোড", "dev": "स्टेशन रोड"}),
    ("Main Road", {"ta": "பிரதான சாலை", "bn": "মেইন রোড", "dev": "मुख्य मार्ग"}),
    ("Lake View Colony", {"ta": "ஏரிக்கரை காலனி", "bn": "লেক ভিউ কলোনি", "dev": "लेक व्यू कॉलोनी"}),
    ("Shivaji Nagar", {"ta": "சிவாஜி நகர்", "bn": "শিবাজি নগর", "dev": "शिवाजी नगर"}),
    ("Patel Nagar", {"ta": "பட்டேல் நகர்", "bn": "প্যাটেল নগর", "dev": "पटेल नगर"}),
    ("Market Street", {"ta": "சந்தை தெரு", "bn": "মার্কেট স্ট্রিট", "dev": "मार्केट स्ट्रीट"}),
    ("Green Park", {"ta": "கிரீன் பார்க்", "bn": "গ্রিন পার্ক", "dev": "ग्रीन पार्क"}),
    ("Subhash Nagar", {"ta": "சுபாஷ் நகர்", "bn": "সুভাষ নগর", "dev": "सुभाष नगर"}),
    ("Bazaar Road", {"ta": "பஜார் சாலை", "bn": "বাজার রোড", "dev": "बाज़ार रोड"}),
    ("College Road", {"ta": "கல்லூரி சாலை", "bn": "কলেজ রোড", "dev": "कॉलेज रोड"}),
    ("Park Avenue", {"ta": "பார்க் அவென்யூ", "bn": "পার্ক অ্যাভিনিউ", "dev": "पार्क एवेन्यू"}),
    ("Jawahar Nagar", {"ta": "ஜவஹர் நகர்", "bn": "জওহর নগর", "dev": "जवाहर नगर"}),
]
BUILDINGS = [
    "Sunrise Apartments", "Lakshmi Residency", "Green Valley", "Shanti Towers", "Ocean Heights", "Maple Court",
    "Royal Enclave", "Orchid Villas", "Silver Oak Homes", "Skyline Residency",
]
INDUSTRIAL = ["Industrial Estate", "MIDC Area", "Industrial Area Phase 2", "Trade Centre", "Warehouse Complex"]

DEV_STATE = {"Maharashtra": "महाराष्ट्र", "Delhi": "दिल्ली", "Uttar Pradesh": "उत्तर प्रदेश", "Rajasthan": "राजस्थान",
             "Madhya Pradesh": "मध्य प्रदेश", "Bihar": "बिहार"}


def draw_pin(rng: random.Random, city: City) -> str:
    return rng.choice(city.pin_prefixes) + f"{rng.randint(1, 99):02d}{rng.randint(0, 9)}"


def draw_street_index(rng: random.Random) -> int:
    return rng.randrange(len(STREETS))


def line1_latin(rng: random.Random, street_idx: int, *, business: bool = False) -> str:
    street = STREETS[street_idx][0]
    if business:
        return f"Plot {rng.randint(1, 240)}, {rng.choice(INDUSTRIAL)}, {street}"
    style = rng.randrange(4)
    no = rng.randint(1, 480)
    if style == 0:
        return f"{no}, {street}"
    if style == 1:
        return f"Flat {rng.randint(101, 1204)}, {rng.choice(BUILDINGS)}, {street}"
    if style == 2:
        return f"{no}/{rng.randint(1, 9)}, {street}"
    return f"No. {no}, {street}"


def line1_native(latin_line1: str, street_idx: int, script_lang: str) -> str:
    """Native rendering of a residential line1: the leading house number plus the native street fragment."""
    key = "ta" if script_lang == "ta" else "bn" if script_lang == "bn" else "dev"
    digits = "".join(ch for ch in latin_line1.split(",")[0] if ch.isdigit() or ch == "/")
    if not digits:
        digits = "".join(ch for ch in latin_line1 if ch.isdigit())[:3] or "1"
    return f"{digits}, {STREETS[street_idx][1][key]}"


def state_native(state: str, script_lang: str) -> str | None:
    return STATE_NATIVE.get(state, {}).get(script_lang) or (DEV_STATE.get(state) if script_lang in ("hi", "mr") else None)


_NATIVE_DIGITS = {
    "tamil": "௦௧௨௩௪௫௬௭௮௯",
    "bengali": "০১২৩৪৫৬৭৮৯",
    "devanagari": "०१२३४५६७८९",
}


def to_native_digits(text: str, script: str) -> str:
    table = _NATIVE_DIGITS[script]
    return "".join(table[int(ch)] if ch.isdigit() and ch.isascii() else ch for ch in text)


# Words used by the native-script address-proof templates.
NATIVE_WORDS: dict[str, dict[str, str]] = {
    "ta": {
        "elec_bill": "மின் கட்டண ரசீது", "utility_co": "மாநில மின் வாரியம்", "name": "பெயர்", "address": "முகவரி",
        "bill_date": "பில் தேதி", "amount_due": "செலுத்த வேண்டிய தொகை", "rent": "வாடகை ஒப்பந்தம்",
        "landlord": "வீட்டு உரிமையாளர்", "tenant": "வாடகைதாரர்", "monthly_rent": "மாத வாடகை",
        "term": "ஒப்பந்த காலம்", "from": "முதல்", "to": "வரை", "property": "சொத்து முகவரி",
    },
    "bn": {
        "elec_bill": "বিদ্যুৎ বিল", "utility_co": "রাজ্য বিদ্যুৎ বিতরণ কোম্পানি", "name": "নাম", "address": "ঠিকানা",
        "bill_date": "বিলের তারিখ", "amount_due": "প্রদেয় অর্থ", "rent": "ভাড়া চুক্তিপত্র",
        "landlord": "বাড়িওয়ালা", "tenant": "ভাড়াটিয়া", "monthly_rent": "মাসিক ভাড়া",
        "term": "চুক্তির মেয়াদ", "from": "থেকে", "to": "পর্যন্ত", "property": "সম্পত্তির ঠিকানা",
    },
    "hi": {
        "elec_bill": "बिजली बिल", "utility_co": "राज्य विद्युत वितरण कंपनी", "name": "नाम", "address": "पता",
        "bill_date": "बिल की तारीख", "amount_due": "देय राशि", "rent": "किराया अनुबंध",
        "landlord": "मकान मालिक", "tenant": "किरायेदार", "monthly_rent": "मासिक किराया",
        "term": "अनुबंध अवधि", "from": "से", "to": "तक", "property": "संपत्ति का पता",
    },
    "mr": {
        "elec_bill": "वीज बिल", "utility_co": "राज्य वीज वितरण कंपनी", "name": "नाव", "address": "पत्ता",
        "bill_date": "बिलाची तारीख", "amount_due": "देय रक्कम", "rent": "भाडे करारनामा",
        "landlord": "घरमालक", "tenant": "भाडेकरू", "monthly_rent": "मासिक भाडे",
        "term": "कराराचा कालावधी", "from": "पासून", "to": "पर्यंत", "property": "मालमत्तेचा पत्ता",
    },
}

# ------------------------------------------------------------------------------------------------ identifiers

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(digits: str) -> str:
    """Verhoeff check digit to append to ``digits``."""
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[c])


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def gen_aadhaar(rng: random.Random) -> str:
    """12 digits, first digit 2-9, last digit the Verhoeff check digit."""
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    return body + verhoeff_check_digit(body)


_PAN_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def gen_pan(rng: random.Random, holder_type: str, name_initial: str) -> str:
    """``AAAPL1234C``: three letters, holder type (P individual, F firm/LLP, C company), initial of the surname
    (or entity name), four digits, one letter."""
    if holder_type not in ("P", "F", "C"):
        raise ValueError(holder_type)
    head = "".join(rng.choice(_PAN_LETTERS) for _ in range(3))
    initial = name_initial.upper()[:1] if name_initial and name_initial[:1].isalpha() and name_initial.isascii() else "X"
    return f"{head}{holder_type}{initial}{rng.randint(0, 9999):04d}{rng.choice(_PAN_LETTERS)}"


def gen_phone(rng: random.Random) -> str:
    first = rng.choices("6789", weights=[15, 25, 25, 35], k=1)[0]
    return first + "".join(str(rng.randint(0, 9)) for _ in range(9))


def gen_account_number(rng: random.Random) -> str:
    length = rng.randint(11, 16)
    return str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(length - 1))


_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(first14: str) -> str:
    factor, total = 2, 0
    for ch in reversed(first14):
        digit = factor * _GST_CHARS.index(ch)
        factor = 1 if factor == 2 else 2
        total += digit // 36 + digit % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def gen_gstin(rng: random.Random, state_code: str, pan: str) -> str:
    """15 characters: 2-digit state code + PAN + entity number + 'Z' + check character."""
    body = f"{state_code}{pan}{rng.randint(1, 3)}Z"
    return body + gstin_check_char(body)


FREE_EMAIL_DOMAINS = ["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com", "hotmail.com"]
DISPOSABLE_EMAIL_DOMAINS = ["mailinator.com", "guerrillamail.com", "10minutemail.net", "yopmail.com", "tempmail.io"]


def gen_email(rng: random.Random, person_name: str, kind: str, org: str | None = None) -> str:
    """``kind`` is free, corporate or disposable (the identity block's email_domain_type)."""
    first, _, last = person_name.lower().partition(" ")
    last = last.replace(" ", "")
    if kind == "disposable":
        local = "".join(rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(rng.randint(8, 11)))
        return f"{local}@{rng.choice(DISPOSABLE_EMAIL_DOMAINS)}"
    local = rng.choice(
        [f"{first}.{last}", f"{first}{last}", f"{first[:1]}.{last}", f"{last}.{first}", f"{first}.{last}{rng.randint(1, 99)}",
         f"{first}{rng.randint(70, 99)}"]
    )
    if kind == "corporate" and org:
        return f"{first}.{last}@{org_slug(org)}.co.in"
    return f"{local}@{rng.choice(FREE_EMAIL_DOMAINS)}"


def hr_email(person_name: str, org: str) -> str:
    first, _, last = person_name.lower().partition(" ")
    return f"hr.{first}@{org_slug(org)}.co.in" if not last else f"{first}.{last.replace(' ', '')}@{org_slug(org)}.co.in"
