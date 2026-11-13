"""Airlines: the 3-letter code planes broadcast (ICAO), the 2-letter one on tickets (IATA), and the name.

Used to read flight numbers ("UA 1234" -> UAL1234) and, on the phone, to search ("Southwest", "WN", "SWA").
"""

from __future__ import annotations

AIRLINES = [
    # United States
    ("AAL", "AA", "American Airlines"), ("DAL", "DL", "Delta Air Lines"), ("UAL", "UA", "United Airlines"),
    ("SWA", "WN", "Southwest Airlines"), ("ASA", "AS", "Alaska Airlines"), ("JBU", "B6", "JetBlue"),
    ("NKS", "NK", "Spirit Airlines"), ("FFT", "F9", "Frontier Airlines"), ("HAL", "HA", "Hawaiian Airlines"),
    ("AAY", "G4", "Allegiant Air"), ("SCX", "SY", "Sun Country Airlines"), ("MXY", "MX", "Breeze Airways"),
    ("VXP", "XP", "Avelo Airlines"), ("QXE", "QX", "Horizon Air"), ("SKW", "OO", "SkyWest Airlines"),
    ("RPA", "YX", "Republic Airways"), ("ENY", "MQ", "Envoy Air"), ("EDV", "9E", "Endeavor Air"),
    ("JIA", "OH", "PSA Airlines"), ("ASH", "YV", "Mesa Airlines"), ("AWI", "ZW", "Air Wisconsin"),
    ("PDT", "PT", "Piedmont Airlines"), ("UCA", "C5", "CommuteAir"), ("GJS", "G7", "GoJet Airlines"),
    ("KAP", "9K", "Cape Air"), ("SIL", "3M", "Silver Airways"), ("VTE", "LF", "Contour Airlines"),
    ("JSX", "XE", "JSX"), ("FDX", "FX", "FedEx"), ("UPS", "5X", "UPS Airlines"), ("GTI", "5Y", "Atlas Air"),
    ("CKS", "K4", "Kalitta Air"), ("PAC", "PO", "Polar Air Cargo"), ("ABX", "GB", "ABX Air"),
    ("WGN", "KD", "Western Global Airlines"), ("NCR", "N8", "National Airlines"), ("EJA", "1I", "NetJets"),
    ("LXJ", "", "Flexjet"),
    # Canada and Latin America
    ("ACA", "AC", "Air Canada"), ("JZA", "QK", "Air Canada Jazz"), ("ROU", "RV", "Air Canada Rouge"),
    ("WJA", "WS", "WestJet"), ("POE", "PD", "Porter Airlines"), ("FLE", "F8", "Flair Airlines"),
    ("TSC", "TS", "Air Transat"), ("CJT", "W8", "Cargojet"), ("AMX", "AM", "Aeromexico"),
    ("SLI", "5D", "Aeromexico Connect"), ("VOI", "Y4", "Volaris"), ("VIV", "VB", "Viva Aerobus"),
    ("LAN", "LA", "LATAM Airlines"), ("TAM", "JJ", "LATAM Brasil"), ("AVA", "AV", "Avianca"),
    ("CMP", "CM", "Copa Airlines"), ("AZU", "AD", "Azul"), ("GLO", "G3", "GOL"),
    ("ARG", "AR", "Aerolineas Argentinas"), ("JAT", "JA", "JetSMART"), ("SKU", "H2", "Sky Airline"),
    # Europe
    ("BAW", "BA", "British Airways"), ("VIR", "VS", "Virgin Atlantic"), ("DLH", "LH", "Lufthansa"),
    ("AFR", "AF", "Air France"), ("KLM", "KL", "KLM"), ("IBE", "IB", "Iberia"), ("EIN", "EI", "Aer Lingus"),
    ("RYR", "FR", "Ryanair"), ("EZY", "U2", "easyJet"), ("WZZ", "W6", "Wizz Air"),
    ("NAX", "DY", "Norwegian"), ("SAS", "SK", "SAS Scandinavian Airlines"), ("FIN", "AY", "Finnair"),
    ("SWR", "LX", "Swiss"), ("AUA", "OS", "Austrian Airlines"), ("BEL", "SN", "Brussels Airlines"),
    ("TAP", "TP", "TAP Air Portugal"), ("ITY", "AZ", "ITA Airways"), ("THY", "TK", "Turkish Airlines"),
    ("PGT", "PC", "Pegasus Airlines"), ("ICE", "FI", "Icelandair"), ("LOT", "LO", "LOT Polish Airlines"),
    ("CFG", "DE", "Condor"), ("EWG", "EW", "Eurowings"), ("EXS", "LS", "Jet2"), ("TOM", "BY", "TUI Airways"),
    ("VLG", "VY", "Vueling"), ("AEA", "UX", "Air Europa"), ("EDW", "WK", "Edelweiss Air"),
    ("AEE", "A3", "Aegean Airlines"), ("CLX", "CV", "Cargolux"),
    # Middle East and Africa
    ("UAE", "EK", "Emirates"), ("QTR", "QR", "Qatar Airways"), ("ETD", "EY", "Etihad Airways"),
    ("SVA", "SV", "Saudia"), ("ELY", "LY", "El Al"), ("RJA", "RJ", "Royal Jordanian"), ("FDB", "FZ", "flydubai"),
    ("OMA", "WY", "Oman Air"), ("GFA", "GF", "Gulf Air"), ("ETH", "ET", "Ethiopian Airlines"),
    ("KQA", "KQ", "Kenya Airways"), ("SAA", "SA", "South African Airways"), ("RAM", "AT", "Royal Air Maroc"),
    ("MSR", "MS", "EgyptAir"),
    # Asia and the Pacific
    ("QFA", "QF", "Qantas"), ("VOZ", "VA", "Virgin Australia"), ("JST", "JQ", "Jetstar"),
    ("ANZ", "NZ", "Air New Zealand"), ("FJI", "FJ", "Fiji Airways"), ("SIA", "SQ", "Singapore Airlines"),
    ("TGW", "TR", "Scoot"), ("CPA", "CX", "Cathay Pacific"), ("JAL", "JL", "Japan Airlines"),
    ("ANA", "NH", "All Nippon Airways"), ("TZP", "ZG", "Zipair"), ("KAL", "KE", "Korean Air"),
    ("AAR", "OZ", "Asiana Airlines"), ("CAL", "CI", "China Airlines"), ("EVA", "BR", "EVA Air"),
    ("SJX", "JX", "Starlux Airlines"), ("CCA", "CA", "Air China"), ("CES", "MU", "China Eastern Airlines"),
    ("CSN", "CZ", "China Southern Airlines"), ("CHH", "HU", "Hainan Airlines"), ("PAL", "PR", "Philippine Airlines"),
    ("CEB", "5J", "Cebu Pacific"), ("THA", "TG", "Thai Airways"), ("HVN", "VN", "Vietnam Airlines"),
    ("MAS", "MH", "Malaysia Airlines"), ("AXM", "AK", "AirAsia"), ("GIA", "GA", "Garuda Indonesia"),
    ("AIC", "AI", "Air India"), ("IGO", "6E", "IndiGo"),
]

IATA_TO_ICAO = {iata: icao for icao, iata, _ in AIRLINES if iata}
BY_ICAO = {icao: name for icao, _, name in AIRLINES}
