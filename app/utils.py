import re
import unicodedata
import logging

# --- Logging Setup ---
def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

def normalize_obj_name(name: str) -> str:
    """Compatibility alias for the shared import normalization."""
    from app.import_helpers import normalize_object_name
    return normalize_object_name(name)

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
    joined = '|'.join(f"{t}\\d+" for t in types)
    pattern = re.compile(rf"^(.*?)(?:({joined}))$")
    match = pattern.match(text)
    if match:
        return match.groups()
    return text, None
