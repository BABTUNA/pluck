from typing import Any
from pathlib import Path
from pydantic import BaseModel, field_validator

# Load categories once at module level
CATEGORIES_FILE = Path(__file__).parent / "categories.txt"
VALID_CATEGORIES = set()
if CATEGORIES_FILE.exists():
    with open(CATEGORIES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                VALID_CATEGORIES.add(line)

class Category(BaseModel):
    # A category from Google's Product Taxonomy
    # https://www.google.com/basepages/producttype/taxonomy.en-US.txt
    name: str

    @field_validator("name")
    @classmethod
    def validate_name_exists(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Category '{v}' is not a valid category in categories.txt")
        return v

class Price(BaseModel):
    price: float
    currency: str
    # If a product is on sale, this is the original price
    compare_at_price: float | None = None

# This is the final product schema that you need to output. 
# You may add additional models as needed.
class Product(BaseModel):
    name: str
    price: Price
    description: str
    key_features: list[str]
    image_urls: list[str]
    video_url: str | None = None
    category: Category
    brand: str
    colors: list[str]
    variants: list["Variant"]  # defined below, one discrete configuration

# the extractor's own models (Product with per-field provenance, Field) live
# in pluck/extract.py, this file keeps the assignment's output schema


class Variant(BaseModel):
    # one discrete configuration of the product, a point in the option matrix
    name: str                        # "French Blue Two Tone / XS"
    price: float | None = None       # when the page prices variants separately
    compare_at_price: float | None = None
    available: bool | None = None    # only when the page says


# the extractor's internal product (pluck.extract.Product, every field tagged
# with which source answered it) flattened into the schema above
def from_pluck(p) -> Product:
    variants = [Variant(name=v["name"], price=v.get("price"),
                        compare_at_price=v.get("compare_at"),
                        available=v.get("available")) for v in p.variants]
    return Product(
        name=p.name.value or "",
        price=Price(price=p.price.value or 0.0,
                    currency=p.currency.value or "USD",
                    compare_at_price=p.compare_at.value),
        description=p.description or "",
        key_features=p.key_features,
        image_urls=p.images,
        video_url=p.video_url,
        category=Category(name=_category_or_nearest(p)),
        brand=p.brand.value or "",
        colors=p.colors,
        variants=variants,
    )


# the validator rejects anything not in categories.txt, and unseen pages can
# leave category empty, so fall back to the nearest real path for the name
# rather than crash the run
def _category_or_nearest(p) -> str:
    from pluck.taxonomy import _CATS, snap
    return p.category.value or snap(p.name.value or "") or _CATS[0]
