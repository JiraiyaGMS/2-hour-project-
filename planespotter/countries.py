"""Which country an aircraft is registered in, from its registration (N123AB = USA, G-EUPT = UK...)."""

from __future__ import annotations

# Registration prefix (without the hyphen) -> (ISO country code, name). Longest prefix wins.
PREFIXES = {
    "N": ("US", "United States"), "CF": ("CA", "Canada"), "CG": ("CA", "Canada"), "CI": ("CA", "Canada"),
    "XA": ("MX", "Mexico"), "XB": ("MX", "Mexico"), "XC": ("MX", "Mexico"), "G": ("GB", "United Kingdom"),
    "EI": ("IE", "Ireland"), "EJ": ("IE", "Ireland"), "F": ("FR", "France"), "D": ("DE", "Germany"),
    "I": ("IT", "Italy"), "EC": ("ES", "Spain"), "CS": ("PT", "Portugal"), "PH": ("NL", "Netherlands"),
    "OO": ("BE", "Belgium"), "HB": ("CH", "Switzerland"), "OE": ("AT", "Austria"), "SE": ("SE", "Sweden"),
    "LN": ("NO", "Norway"), "OY": ("DK", "Denmark"), "OH": ("FI", "Finland"), "SP": ("PL", "Poland"),
    "OK": ("CZ", "Czechia"), "OM": ("SK", "Slovakia"), "HA": ("HU", "Hungary"), "YR": ("RO", "Romania"),
    "LZ": ("BG", "Bulgaria"), "SX": ("GR", "Greece"), "TC": ("TR", "Turkey"), "4X": ("IL", "Israel"),
    "A6": ("AE", "United Arab Emirates"), "A7": ("QA", "Qatar"), "A9C": ("BH", "Bahrain"), "A4O": ("OM", "Oman"),
    "HZ": ("SA", "Saudi Arabia"), "9K": ("KW", "Kuwait"), "JY": ("JO", "Jordan"), "OD": ("LB", "Lebanon"),
    "YI": ("IQ", "Iraq"), "EP": ("IR", "Iran"), "SU": ("EG", "Egypt"), "5Y": ("KE", "Kenya"),
    "ET": ("ET", "Ethiopia"), "ZS": ("ZA", "South Africa"), "CN": ("MA", "Morocco"), "7T": ("DZ", "Algeria"),
    "TS": ("TN", "Tunisia"), "5N": ("NG", "Nigeria"), "9G": ("GH", "Ghana"), "5H": ("TZ", "Tanzania"),
    "3B": ("MU", "Mauritius"), "D2": ("AO", "Angola"), "9V": ("SG", "Singapore"), "9M": ("MY", "Malaysia"),
    "PK": ("ID", "Indonesia"), "HS": ("TH", "Thailand"), "RP": ("PH", "Philippines"), "VN": ("VN", "Vietnam"),
    "BH": ("HK", "Hong Kong"), "BK": ("HK", "Hong Kong"), "BL": ("HK", "Hong Kong"), "BM": ("MO", "Macau"),
    "B": ("CN", "China"), "JA": ("JP", "Japan"), "HL": ("KR", "South Korea"), "VH": ("AU", "Australia"),
    "ZK": ("NZ", "New Zealand"), "VT": ("IN", "India"), "AP": ("PK", "Pakistan"), "4R": ("LK", "Sri Lanka"),
    "S2": ("BD", "Bangladesh"), "9N": ("NP", "Nepal"), "UK": ("UZ", "Uzbekistan"), "UP": ("KZ", "Kazakhstan"),
    "RA": ("RU", "Russia"), "UR": ("UA", "Ukraine"), "EW": ("BY", "Belarus"), "4L": ("GE", "Georgia"),
    "EK": ("AM", "Armenia"), "4K": ("AZ", "Azerbaijan"), "LV": ("AR", "Argentina"), "PP": ("BR", "Brazil"),
    "PR": ("BR", "Brazil"), "PS": ("BR", "Brazil"), "PT": ("BR", "Brazil"), "PU": ("BR", "Brazil"),
    "CC": ("CL", "Chile"), "HK": ("CO", "Colombia"), "OB": ("PE", "Peru"), "YV": ("VE", "Venezuela"),
    "HC": ("EC", "Ecuador"), "CP": ("BO", "Bolivia"), "CX": ("UY", "Uruguay"), "ZP": ("PY", "Paraguay"),
    "TI": ("CR", "Costa Rica"), "HP": ("PA", "Panama"), "TG": ("GT", "Guatemala"), "YS": ("SV", "El Salvador"),
    "HR": ("HN", "Honduras"), "YN": ("NI", "Nicaragua"), "6Y": ("JM", "Jamaica"), "C6": ("BS", "Bahamas"),
    "HI": ("DO", "Dominican Republic"), "CU": ("CU", "Cuba"), "8P": ("BB", "Barbados"),
    "9Y": ("TT", "Trinidad and Tobago"), "VPB": ("BM", "Bermuda"), "VQB": ("BM", "Bermuda"),
    "VPC": ("KY", "Cayman Islands"), "P4": ("AW", "Aruba"), "PJ": ("CW", "Curaçao"), "9H": ("MT", "Malta"),
    "5B": ("CY", "Cyprus"), "TF": ("IS", "Iceland"), "ES": ("EE", "Estonia"), "YL": ("LV", "Latvia"),
    "LY": ("LT", "Lithuania"), "S5": ("SI", "Slovenia"), "9A": ("HR", "Croatia"), "YU": ("RS", "Serbia"),
    "LX": ("LU", "Luxembourg"), "2": ("GG", "Guernsey"), "M": ("IM", "Isle of Man"), "ZJ": ("JE", "Jersey"),
    "Z": ("ZW", "Zimbabwe"), "A2": ("BW", "Botswana"), "V5": ("NA", "Namibia"), "9J": ("ZM", "Zambia"),
}
_LONGEST_FIRST = sorted(PREFIXES, key=len, reverse=True)


def country_of(registration: str) -> tuple[str, str] | None:
    """('US', 'United States') for N123AB, or None if unknown."""
    reg = (registration or "").upper().replace("-", "").strip()
    if not reg:
        return None
    if reg[0] == "N":
        return PREFIXES["N"] if len(reg) > 1 and reg[1].isdigit() else None
    if reg.startswith("B") and len(reg) == 6 and reg[1:].isdigit():
        return ("TW", "Taiwan")  # B-12345: Taiwan uses five digits, mainland China four
    for prefix in _LONGEST_FIRST:
        if reg.startswith(prefix) and prefix != "N":
            return PREFIXES[prefix]
    return None


def flag(iso: str) -> str:
    """'US' -> 🇺🇸."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso.upper()) if len(iso) == 2 else ""
