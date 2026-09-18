import json
import re
import sys
from num2words import num2words

LANGUAGES = frozenset(("ar","da","de","el","en","es","fi","fr","hi","it","ms","nl","no","pl","pt","sv","sw","tr"))
FALLBACK_DIGITS = {
    "el": ("μηδέν","ένα","δύο","τρία","τέσσερα","πέντε","έξι","επτά","οκτώ","εννέα"),
    "ms": ("sifar","satu","dua","tiga","empat","lima","enam","tujuh","lapan","sembilan"),
    "sw": ("sifuri","moja","mbili","tatu","nne","tano","sita","saba","nane","tisa"),
}
DECIMAL_WORDS = {
    "ar":"فاصلة","da":"komma","de":"Komma","el":"κόμμα","en":"point","es":"coma","fi":"pilkku","fr":"virgule",
    "hi":"दशमलव","it":"virgola","ms":"perpuluhan","nl":"komma","no":"komma",
    "pl":"przecinek","pt":"vírgula","sv":"komma","sw":"nukta","tr":"virgül",
}
FALLBACK_MINUS = {"el":"μείον","ms":"tolak","sw":"hasi"}
CURRENCY_RE = re.compile(r"(?<![\w])(?:(?P<symbol>[$€£])\s*(?P<prefix>\d+(?:[.,]\d{1,2})?)|(?P<suffix>\d+(?:[.,]\d{1,2})?)\s*(?P<code>PLN|EUR|USD|GBP))(?![\w])", re.IGNORECASE)
DATE_RE = re.compile(r"(?<![\w])(\d{1,4})([./-])(\d{1,2})\2(\d{2,4})(?![\w])")
DECIMAL_RE = re.compile(r"(?<![\w])[-+]?\d+[.,]\d+(?![\w])")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{5,}\d(?![\w])")
INTEGER_RE = re.compile(r"(?<![\w])[-+]?\d+(?![\w])")
CURRENCIES = {
    "pl": {"$":"USD","€":"EUR","PLN":"PLN","EUR":"EUR","USD":"USD"},
    "it": {"$":"USD","€":"EUR","£":"GBP","EUR":"EUR","USD":"USD","GBP":"GBP"},
    "pt": {"$":"USD","€":"EUR","£":"GBP","EUR":"EUR","USD":"USD","GBP":"GBP"},
}

def digit_word(digit, language):
    return FALLBACK_DIGITS[language][int(digit)] if language in FALLBACK_DIGITS else num2words(int(digit), lang=language)

def spell_digits(value, language):
    return " ".join(digit_word(digit, language) for digit in value if digit.isdigit())

def cardinal(value, language):
    signless = value.lstrip("+-")
    if re.fullmatch(r"0\d+", signless):
        spoken = spell_digits(signless, language)
        if value.startswith("-"):
            return (FALLBACK_MINUS.get(language) or num2words(-1, lang=language).rsplit(" ", 1)[0]) + " " + spoken
        return spoken
    if language in FALLBACK_DIGITS:
        spoken = spell_digits(signless, language)
        return FALLBACK_MINUS[language] + " " + spoken if value.startswith("-") else spoken
    return num2words(int(value), lang=language)

def minus_word(language):
    if language in FALLBACK_MINUS:
        return FALLBACK_MINUS[language]
    one = num2words(1, lang=language)
    return num2words(-1, lang=language).replace(one, "", 1).strip()

def decimal(value, language):
    sign = value[:1] if value[:1] in "+-" else ""
    body = value[1:] if sign else value
    whole, fraction = re.split(r"[.,]", body, maxsplit=1)
    prefix = minus_word(language) + " " if sign == "-" else ""
    return prefix + cardinal(whole, language) + " " + DECIMAL_WORDS[language] + " " + spell_digits(fraction, language)

def normalize(text, language):
    if language not in LANGUAGES:
        raise SystemExit(f"unsupported language: {language}")
    changes = []
    protected = {}
    def protect(target):
        key = chr(0xE000 + len(protected))
        protected[key] = target
        return key
    def replace(kind, source, target):
        changes.append({"kind":kind,"source":source,"target":target})
        return protect(target)
    def currency(match):
        token = match.group("symbol") or match.group("code").upper()
        code = CURRENCIES.get(language, {}).get(token)
        if not code:
            return match.group(0)
        value = match.group("prefix") or match.group("suffix")
        return replace("currency", match.group(0), num2words(float(value.replace(",", ".")), lang=language, to="currency", currency=code))
    def date(match):
        return replace("date_components", match.group(0), " ".join(cardinal(str(int(part)), language) for part in (match.group(1), match.group(3), match.group(4))))
    def decimal_value(match):
        return replace("decimal", match.group(0), decimal(match.group(0), language))
    def phone(match):
        source = match.group(0)
        return replace("digit_sequence", source, spell_digits(source, language))
    def integer(match):
        source = match.group(0)
        target = cardinal(source, language)
        kind = "digit_sequence" if re.fullmatch(r"[+-]?0\d+", source) else "cardinal"
        changes.append({"kind":kind,"source":source,"target":target})
        return target
    work = CURRENCY_RE.sub(currency, text)
    work = DATE_RE.sub(date, work)
    work = DECIMAL_RE.sub(decimal_value, work)
    work = PHONE_RE.sub(phone, work)
    work = INTEGER_RE.sub(integer, work)
    for key, target in protected.items():
        work = work.replace(key, target)
    return {"policy":"language_number_verbalization_v1","language":language,"original_text":text,"transport_text":work,"changes":changes}

def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit("usage: text_normalize.py LANGUAGE")
    print(json.dumps(normalize(sys.stdin.read(), sys.argv[1].lower()), ensure_ascii=False, separators=(",", ":")))

if __name__ == "__main__":
    main()
