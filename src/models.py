"""Pydantic models for nutrition data and sheet logging."""

from typing import Literal

from pydantic import BaseModel, field_validator


def _coerce_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, str):
        v = v.strip().replace(",", "")
        if not v:
            return 0.0
        try:
            return float(v)
        except ValueError:
            return 0.0
    return float(v)


class FoodItem(BaseModel):
    """A single identified food item within a meal."""

    name: str
    description: str = ""
    brand: str | None = None                 # brand name if packaged product (e.g. "Chobani", "Vital Proteins")
    has_label: bool = False                  # true if nutrition facts label was visible in the image
    calories: float
    protein: float
    carbs: float
    fat: float
    weight_g: float | None = None
    gemini_per_100g: dict | None = None      # {calories, protein, carbs, fat} from Gemini
    fatsecret_per_100g: dict | None = None   # {calories, protein, carbs, fat} from FatSecret
    usda_per_100g: dict | None = None        # {calories, protein, carbs, fat} from USDA FDC
    source: str | None = None                # "image", "usda", "fatsecret", "gemini"

    @field_validator("calories", "protein", "carbs", "fat", mode="before")
    @classmethod
    def coerce_float(cls, v):
        return _coerce_float(v)

    @field_validator("calories", "protein", "carbs", "fat")
    @classmethod
    def clamp_non_negative(cls, v: float) -> float:
        return max(0.0, v)


class NutritionResponse(BaseModel):
    """LLM output schema: must match exactly for parsing."""

    item_name: str
    meal_description: str = ""
    items: list[FoodItem] = []
    calories: float
    protein: float
    carbs: float
    fat: float

    @field_validator("calories", "protein", "carbs", "fat", mode="before")
    @classmethod
    def coerce_float(cls, v):
        return _coerce_float(v)

    @field_validator("calories", "protein", "carbs", "fat")
    @classmethod
    def clamp_non_negative(cls, v: float) -> float:
        return max(0.0, v)


class NutritionResult(BaseModel):
    """Final consensus result used for logging and bot reply."""

    item_name: str
    meal_description: str = ""
    items: list[FoodItem] = []
    calories: float
    protein: float
    carbs: float
    fat: float
    source: str = "Gemini"

    @field_validator("calories", "protein", "carbs", "fat", mode="before")
    @classmethod
    def coerce_float(cls, v):
        return _coerce_float(v)

    @field_validator("calories", "protein", "carbs", "fat")
    @classmethod
    def clamp_non_negative(cls, v: float) -> float:
        return max(0.0, v)



class TotalsResult(BaseModel):
    """Aggregated totals for a period (day/week/month)."""

    period: Literal["day", "week", "month"]
    total_calories: float
    total_protein: float
    total_carbs: float
    total_fat: float
    meal_count: int
    avg_calories_per_day: float | None = None
    avg_protein_per_day: float | None = None
    avg_carbs_per_day: float | None = None
    avg_fat_per_day: float | None = None


class UserRecord(BaseModel):
    """A registered user stored in the Users tab."""

    chat_id: int
    username: str = ""
    first_name: str = ""
    sheet_tab: str
    registered_at: str = ""
