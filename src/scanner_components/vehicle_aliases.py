import re
from src.utils import norm

BRAND_ALIASES = [
    ("mercedes-benz", "Mercedes"), ("mercedes", "Mercedes"), ("benz", "Mercedes"),
    ("volkswagen", "Volkswagen"), ("vw", "Volkswagen"),
    ("bmw", "Bmw"), ("audi", "Audi"), ("opel", "Opel"), ("ford", "Ford"),
    ("toyota", "Toyota"), ("skoda", "Skoda"), ("seat", "Seat"),
    ("renault", "Renault"), ("peugeot", "Peugeot"), ("hyundai", "Hyundai"),
    ("kia", "Kia"), ("mazda", "Mazda"), ("honda", "Honda"),
    ("volvo", "Volvo"), ("dacia", "Dacia"), ("fiat", "Fiat"),
    ("alfa romeo", "Alfa Romeo"), ("mini", "Mini"), ("nissan", "Nissan"),
    ("suzuki", "Suzuki"), ("mitsubishi", "Mitsubishi"), ("chevrolet", "Chevrolet"),
    ("jeep", "Jeep"), ("range rover", "Land Rover"), ("land rover", "Land Rover"), ("jaguar", "Jaguar"),
    ("lexus", "Lexus"), ("porsche", "Porsche"), ("subaru", "Subaru"),
    ("citroen", "Citroen"), ("smart", "Smart"), ("tesla", "Tesla"),
]

MODEL_ALIASES = [
    ("golf4", "Golf", "Volkswagen"), ("golf 4", "Golf", "Volkswagen"),
    ("golf5", "Golf", "Volkswagen"), ("golf 5", "Golf", "Volkswagen"),
    ("golf6", "Golf", "Volkswagen"), ("golf 6", "Golf", "Volkswagen"),
    ("golf7", "Golf", "Volkswagen"), ("golf 7", "Golf", "Volkswagen"),
    ("golf", "Golf", "Volkswagen"), ("polo", "Polo", "Volkswagen"),
    ("passat", "Passat", "Volkswagen"), ("touran", "Touran", "Volkswagen"),
    ("tiguan", "Tiguan", "Volkswagen"), ("caddy", "Caddy", "Volkswagen"),
    ("sharan", "Sharan", "Volkswagen"), ("t5", "T5", "Volkswagen"),
    ("t4", "T4", "Volkswagen"), ("transporter", "T5", "Volkswagen"),
    ("caravelle", "T5", "Volkswagen"), ("multivan", "T5", "Volkswagen"),
    ("octavia", "Octavia", "Skoda"), ("fabia", "Fabia", "Skoda"),
    ("superb", "Superb", "Skoda"), ("leon", "Leon", "Seat"),
    ("ibiza", "Ibiza", "Seat"), ("alhambra", "Alhambra", "Seat"),
    ("astra", "Astra", "Opel"), ("corsa", "Corsa", "Opel"),
    ("zafira", "Zafira", "Opel"), ("insignia", "Insignia", "Opel"),
    ("vectra", "Vectra", "Opel"), ("crossland", "Crossland", "Opel"),
    ("focus", "Focus", "Ford"), ("fokus", "Focus", "Ford"),
    ("fiesta", "Fiesta", "Ford"), ("mondeo", "Mondeo", "Ford"),
    ("galaxy", "Galaxy", "Ford"), ("s-max", "S-Max", "Ford"),
    ("smax", "S-Max", "Ford"), ("kuga", "Kuga", "Ford"),
    ("yaris", "Yaris", "Toyota"), ("auris", "Auris", "Toyota"),
    ("corolla", "Corolla", "Toyota"), ("avensis", "Avensis", "Toyota"),
    ("jazz", "Jazz", "Honda"), ("civic", "Civic", "Honda"),
    ("i30", "i30", "Hyundai"), ("i 30", "i30", "Hyundai"),
    ("i20", "i20", "Hyundai"), ("i 20", "i20", "Hyundai"),
    ("ix35", "ix35", "Hyundai"), ("rio", "Rio", "Kia"),
    ("ceed", "Ceed", "Kia"), ("c'eed", "Ceed", "Kia"),
    ("picanto", "Picanto", "Kia"), ("sportage", "Sportage", "Kia"),
    ("mazda 2", "2", "Mazda"), ("mazda2", "2", "Mazda"),
    ("mazda 3", "3", "Mazda"), ("mazda3", "3", "Mazda"),
    ("mazda 6", "6", "Mazda"), ("mazda6", "6", "Mazda"),
    ("mx-5", "MX-5", "Mazda"), ("mx5", "MX-5", "Mazda"),
    ("qashqai", "Qashqai", "Nissan"), ("clio", "Clio", "Renault"),
    ("twingo", "Twingo", "Renault"), ("megane", "Megane", "Renault"),
    ("scenic", "Scenic", "Renault"), ("captur", "Captur", "Renault"),
    ("sandero", "Sandero", "Dacia"), ("duster", "Duster", "Dacia"),
    ("peugeot 208", "208", "Peugeot"), ("peugeot 308", "308", "Peugeot"),
    ("citroen c3", "C3", "Citroen"), ("c3", "C3", "Citroen"), ("c4", "C4", "Citroen"),
    ("fiat 500", "500", "Fiat"), ("up", "UP", "Volkswagen"), ("fox", "Fox", "Volkswagen"),
    ("mii", "Mii", "Seat"), ("meriva", "Meriva", "Opel"), ("venga", "Venga", "Kia"),
    ("soul", "Soul", "Kia"), ("colt", "Colt", "Mitsubishi"), ("yeti", "Yeti", "Skoda"),
    ("a3", "A3", "Audi"), ("a4", "A4", "Audi"), ("a5", "A5", "Audi"),
    ("a6", "A6", "Audi"), ("q5", "Q5", "Audi"),
    ("116i", "1er", "Bmw"), ("118i", "1er", "Bmw"), ("120i", "1er", "Bmw"),
    ("116d", "1er", "Bmw"), ("118d", "1er", "Bmw"), ("120d", "1er", "Bmw"),
    ("316i", "3er", "Bmw"), ("316", "3er", "Bmw"), ("318i", "3er", "Bmw"), ("320i", "3er", "Bmw"), ("328i", "3er", "Bmw"),
    ("318d", "3er", "Bmw"), ("320d", "3er", "Bmw"), ("330d", "3er", "Bmw"),
    ("e34", "5er", "Bmw"), ("520i", "5er", "Bmw"), ("e46", "3er", "Bmw"), ("e90", "3er", "Bmw"), ("e91", "3er", "Bmw"), ("e92", "3er", "Bmw"), ("x1", "X1", "Bmw"), ("x3", "X3", "Bmw"), ("x5", "X5", "Bmw"),
    ("a180", "A-Klasse", "Mercedes"), ("a 180", "A-Klasse", "Mercedes"),
    ("b180", "B-Klasse", "Mercedes"), ("b 180", "B-Klasse", "Mercedes"),
    ("c klasse", "C-Klasse", "Mercedes"), ("c-klasse", "C-Klasse", "Mercedes"), ("c180", "C-Klasse", "Mercedes"), ("c 180", "C-Klasse", "Mercedes"),
    ("c200", "C-Klasse", "Mercedes"), ("c 200", "C-Klasse", "Mercedes"),
    ("c220", "C-Klasse", "Mercedes"), ("c 220", "C-Klasse", "Mercedes"),
    ("e200", "E-Klasse", "Mercedes"), ("e 200", "E-Klasse", "Mercedes"),
    ("e220", "E-Klasse", "Mercedes"), ("e 220", "E-Klasse", "Mercedes"),
    ("evoque", "Evoque", "Land Rover"), ("discovery", "Discovery", "Land Rover"), ("freelander", "Freelander", "Land Rover"),
]

def extract_brand_model(title: str) -> tuple:
    """Extract brand/model from noisy German listing titles."""
    normalized_title = norm(title or "")

    brand = None
    matched_alias = None
    for alias, normalized in BRAND_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", normalized_title):
            brand = normalized
            matched_alias = alias
            break

    model = None
    inferred_brand = None
    for alias, normalized_model, alias_brand in MODEL_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", normalized_title):
            model = normalized_model
            inferred_brand = alias_brand
            break

    if not brand and inferred_brand:
        brand = inferred_brand

    if brand and not model and matched_alias:
        match = re.search(rf"\b{re.escape(matched_alias)}\b\s+([a-z0-9-]{{2,18}})", normalized_title)
        if match:
            candidate = match.group(1).strip(" ,.-").upper()
            blocked = {"verkaufe", "auto", "pkw", "gebraucht", "benz", "klasse", "schoener", "schoner"}
            if candidate.lower() not in blocked:
                model = candidate

    return brand, model