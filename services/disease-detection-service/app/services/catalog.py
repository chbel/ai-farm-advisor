"""Human-readable information for each model label.

Names and causal agents only: well-established plant pathology facts. Treatment
advice is deliberately NOT given here; it belongs to the AI Advisor, which must
ground it in sourced documents (RAG).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConditionInfo:
    name_en: str
    name_fr: str
    cause_type: str  # fungus, oomycete, bacterium, virus, pest, none
    causal_agent: str | None


CATALOG: dict[str, ConditionInfo] = {
    "tomato_bacterial_spot": ConditionInfo(
        "Bacterial spot", "Tache bactérienne", "bacterium", "Xanthomonas spp."),
    "tomato_early_blight": ConditionInfo(
        "Early blight", "Alternariose", "fungus", "Alternaria solani / A. linariae"),
    "tomato_late_blight": ConditionInfo(
        "Late blight", "Mildiou", "oomycete", "Phytophthora infestans"),
    "tomato_leaf_mold": ConditionInfo(
        "Leaf mold", "Cladosporiose", "fungus", "Passalora fulva (syn. Fulvia fulva)"),
    "tomato_septoria_leaf_spot": ConditionInfo(
        "Septoria leaf spot", "Septoriose", "fungus", "Septoria lycopersici"),
    "tomato_spider_mites": ConditionInfo(
        "Two-spotted spider mite damage", "Dégâts d'acariens (tétranyque tisserand)",
        "pest", "Tetranychus urticae"),
    "tomato_target_spot": ConditionInfo(
        "Target spot", "Corynesporiose", "fungus", "Corynespora cassiicola"),
    "tomato_yellow_leaf_curl_virus": ConditionInfo(
        "Tomato yellow leaf curl virus (TYLCV)",
        "Virus des feuilles jaunes en cuillère de la tomate (TYLCV)",
        "virus", "Tomato yellow leaf curl virus (Begomovirus), spread by the whitefly Bemisia tabaci"),
    "tomato_mosaic_virus": ConditionInfo(
        "Tomato mosaic virus (ToMV)", "Virus de la mosaïque de la tomate (ToMV)",
        "virus", "Tomato mosaic virus (Tobamovirus)"),
    "tomato_healthy": ConditionInfo("Healthy", "Saine", "none", None),
}
