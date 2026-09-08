"""Small curated evidence layer for the local running coach.

These are intentionally conservative high-level principles from systematic reviews /
meta-analyses. They are not a replacement for athlete-specific data and they do not
prescribe an exact plan. Personal response, primary event and safety always outrank
this library. URLs are stored for auditability; the weekly coach does not need live web
access to use the principles.
"""

from __future__ import annotations

from typing import Any

VERSION = "2026-09-08"


def principles() -> list[dict[str, Any]]:
    return [
        {
            "tag": "E1",
            "reference": "HRV-guided endurance training systematic review/meta-analysis (J Sci Med Sport, 2021)",
            "category": "recovery",
            "principle": (
                "Brug HRV som en del af en personlig trend/baseline til at justere belastning; "
                "et enkelt dags HRV bør ikke alene styre træningen."
            ),
            "evidence": (
                "HRV-guidede interventioner havde oftere færre moderate/hårde pas og viste en positiv effekt "
                "på submaksimale fysiologiske mål, mens gruppeniveau-effekter på præstation og VO2peak var små/ikke-signifikante."
            ),
            "confidence": "high",
            "source": "https://pubmed.ncbi.nlm.nih.gov/34489178/",
            "pmid": "34489178",
        },
        {
            "tag": "E2",
            "reference": "Time near VO2max during HIIT systematic review/meta-analysis (2026)",
            "category": "quality",
            "principle": (
                "Når målet er en høj aerob/VO2-stimulus, er længere arbejdsintervaller typisk mindst ca. 2 minutter "
                "en veldokumenteret måde at akkumulere tid ved høj iltoptagelse; den konkrete dosis skal tilpasses atletens historik."
            ),
            "evidence": (
                "På tværs af 239 HIIT-protokoller gav arbejdsintervaller på mindst 2 minutter mere tid ved/omkring VO2max end kortere intervaller."
            ),
            "confidence": "high",
            "source": "https://pubmed.ncbi.nlm.nih.gov/42237396/",
            "pmid": "42237396",
        },
        {
            "tag": "E3",
            "reference": "HIIT in trained athletes systematic review/meta-analysis (2026)",
            "category": "quality",
            "principle": (
                "HIIT kan forbedre maksimal aerob kapacitet hos trænede atleter, men kvalitetspas skal doseres som en del af den samlede uge "
                "og ikke presses ind oven i utilstrækkelig restitution."
            ),
            "evidence": "Meta-analysen fandt signifikant forbedring af VO2max/VO2peak efter HIIT hos trænede atleter.",
            "confidence": "high",
            "source": "https://pubmed.ncbi.nlm.nih.gov/41540436/",
            "pmid": "41540436",
        },
        {
            "tag": "E4",
            "reference": "Heavy resistance vs plyometric training systematic review/meta-analysis (Sports Med, 2023)",
            "category": "strength",
            "principle": (
                "Styrketræning kan være et relevant supplement for distanceløbere, især for løbeøkonomi; "
                "placér den så den ikke unødigt kompromitterer ugens vigtigste løbepas."
            ),
            "evidence": "Tung styrketræning havde en lille, gunstig samlet effekt på løbeøkonomi i langdistanceløbere.",
            "confidence": "high",
            "source": "https://pubmed.ncbi.nlm.nih.gov/36370207/",
            "pmid": "36370207",
        },
        {
            "tag": "E5",
            "reference": "Endurance taper systematic review/meta-analysis (PLOS One, 2023)",
            "category": "taper",
            "principle": (
                "Tæt på et vigtigt udholdenhedsløb bør taper primært reducere volumen, mens noget intensitet/frekvens bevares; "
                "den præcise længde og reduktion skal individualiseres."
            ),
            "evidence": (
                "Meta-analysen fandt forbedret time-trial-præstation ved taper og støtte for op til 21 dage med omtrent 41-60% volumenreduktion "
                "uden tilsvarende reduktion af intensitet/frekvens."
            ),
            "confidence": "high",
            "source": "https://pubmed.ncbi.nlm.nih.gov/37163550/",
            "pmid": "37163550",
        },
        {
            "tag": "E6",
            "reference": "Physiological indicators of trail-running performance systematic review (IJSPP, 2021)",
            "category": "specificity",
            "principle": (
                "Trailpræstation er multifaktoriel. Klassiske aerobe faktorer som VO2max, tærskel, vVO2max og løbeøkonomi betyder noget, "
                "men træningen bør også afspejle den konkrete løbsprofil og terræn."
            ),
            "evidence": (
                "Reviewet fandt sammenhænge mellem trailpræstation og flere aerobe variable, men den klassiske udholdenhedsmodel forklarede trailpræstation svagere end forventet."
            ),
            "confidence": "medium",
            "source": "https://pubmed.ncbi.nlm.nih.gov/33508776/",
            "pmid": "33508776",
        },
        {
            "tag": "E7",
            "reference": "Trail-running muscle/neuromuscular damage systematic review (2026)",
            "category": "specificity",
            "principle": (
                "Hvis løbet har markante nedløb/ujævnt terræn, bør eksponering for den excentriske belastning bygges gradvist op og efterfølges af tilstrækkelig restitution."
            ),
            "evidence": (
                "Trail-events med betydelig excentrisk belastning var gennemgående forbundet med akut muskelskade og reduceret neuromuskulær funktion efter løb."
            ),
            "confidence": "medium",
            "source": "https://pubmed.ncbi.nlm.nih.gov/41718076/",
            "pmid": "41718076",
        },
    ]


def merge_into(context: dict[str, Any]) -> dict[str, Any]:
    existing = context.get("external_plan_principles")
    rows = [row for row in existing if isinstance(row, dict)] if isinstance(existing, list) else []
    seen = {str(row.get("tag") or "") for row in rows}
    core = [row for row in principles() if row["tag"] not in seen]
    context["external_plan_principles"] = core + rows
    context["core_evidence_version"] = VERSION
    context.setdefault("rules", {})["personal_data_and_event_override_core_evidence"] = True
    return context
