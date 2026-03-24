import re
import unicodedata
import logging

# --- Logging Setup ---
def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# --- Name Normalisierung ---
def normalize_name(name: str) -> str:
    """Alles klein, Sonderzeichen entfernen, Leerzeichen entfernen."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = name.lower()
    name = re.sub(r"[^a-z0-9]", "", name)
    return name

# --- Regex Helfer ---
def extract_type_code(text: str, types=["TST", "KAK", "TEF"]):
    """
    Extrahiert Typ + Nummer zusammen, z.B. 'KAK29467'.
    Gibt (base_name, code) zurück.
    """
    pattern = re.compile(rf"^(.*?)(?:({'|'.join(t + r'\d+' for t in types)}))$")
    match = pattern.match(text)
    if match:
        return match.groups()
    return text, None