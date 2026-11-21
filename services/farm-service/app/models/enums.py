"""Controlled vocabularies for the Farm Service.

Values are stored as plain strings with CHECK constraints (not native
PostgreSQL ENUMs) so adding a value later is a simple migration.
"""

from enum import StrEnum


class Governorate(StrEnum):
    """The 24 governorates of Tunisia."""

    ARIANA = "ariana"
    BEJA = "beja"
    BEN_AROUS = "ben_arous"
    BIZERTE = "bizerte"
    GABES = "gabes"
    GAFSA = "gafsa"
    JENDOUBA = "jendouba"
    KAIROUAN = "kairouan"
    KASSERINE = "kasserine"
    KEBILI = "kebili"
    KEF = "kef"
    MAHDIA = "mahdia"
    MANOUBA = "manouba"
    MEDENINE = "medenine"
    MONASTIR = "monastir"
    NABEUL = "nabeul"
    SFAX = "sfax"
    SIDI_BOUZID = "sidi_bouzid"
    SILIANA = "siliana"
    SOUSSE = "sousse"
    TATAOUINE = "tataouine"
    TOZEUR = "tozeur"
    TUNIS = "tunis"
    ZAGHOUAN = "zaghouan"


class SoilTexture(StrEnum):
    """Simplified soil texture classes (USDA texture triangle groupings)."""

    SAND = "sand"
    LOAMY_SAND = "loamy_sand"
    SANDY_LOAM = "sandy_loam"
    LOAM = "loam"
    SILT_LOAM = "silt_loam"
    CLAY_LOAM = "clay_loam"
    CLAY = "clay"
    UNKNOWN = "unknown"


class IrrigationMethod(StrEnum):
    DRIP = "drip"
    SPRINKLER = "sprinkler"
    SURFACE = "surface"  # furrow / basin / flood
    RAINFED = "rainfed"  # no irrigation


class CropType(StrEnum):
    OLIVE = "olive"
    TOMATO = "tomato"


class CropStatus(StrEnum):
    PLANNED = "planned"  # planting date may be in the future
    ACTIVE = "active"
    HARVESTED = "harvested"
    REMOVED = "removed"


class TomatoStage(StrEnum):
    """FAO-56 crop development stages (Allen et al., 1998, ch. 6).

    Used directly by the Irrigation Service to select Kc ini / mid / end.
    """

    INITIAL = "initial"
    DEVELOPMENT = "development"
    MID_SEASON = "mid_season"
    LATE_SEASON = "late_season"


class OliveStage(StrEnum):
    """Simplified olive phenology based on the BBCH principal growth stages
    for olive (Sanz-Cortés et al., 2002, Annals of Applied Biology 140:151-157).
    """

    DORMANCY = "dormancy"  # winter rest
    VEGETATIVE_GROWTH = "vegetative_growth"  # BBCH 0-3: bud and shoot development
    FLOWERING = "flowering"  # BBCH 5-6: inflorescence emergence and flowering
    FRUIT_SET = "fruit_set"  # BBCH 7 (early): fruit set
    PIT_HARDENING = "pit_hardening"  # BBCH 7 (mid): endocarp sclerification
    FRUIT_MATURATION = "fruit_maturation"  # BBCH 8: veraison and ripening


STAGES_BY_CROP: dict[CropType, type[StrEnum]] = {
    CropType.TOMATO: TomatoStage,
    CropType.OLIVE: OliveStage,
}

ALL_STAGES: tuple[str, ...] = tuple(
    dict.fromkeys(stage.value for enum in STAGES_BY_CROP.values() for stage in enum)
)


class ActivityType(StrEnum):
    PLANTING = "planting"
    IRRIGATION = "irrigation"
    FERTILIZATION = "fertilization"
    PESTICIDE_TREATMENT = "pesticide_treatment"
    PRUNING = "pruning"
    SOIL_TILLAGE = "soil_tillage"
    HARVEST = "harvest"
    OBSERVATION = "observation"
    OTHER = "other"


def in_values(column: str, values: type[StrEnum] | tuple[str, ...]) -> str:
    """SQL CHECK expression: `column IN ('a', 'b', ...)`."""
    items = [v.value for v in values] if isinstance(values, type) else list(values)
    return f"{column} IN ({', '.join(repr(v) for v in items)})"
