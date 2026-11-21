"""Oil types offered in prediction mode (label -> OpenOil/ADIOS database name)."""

OILS = [
    {"id": "crude_light", "label": "Crude oil, light (Louisiana sweet)", "adios": "LIGHT LOUISIANNA SWEET, BP"},
    {"id": "crude_medium", "label": "Crude oil, medium", "adios": "Generic Medium Crude"},
    {"id": "crude_heavy", "label": "Crude oil, heavy", "adios": "Generic Heavy Crude"},
    {"id": "ifo380", "label": "Bunker fuel (IFO 380)", "adios": "IFO-380LS 2014"},
    {"id": "fuel_no6", "label": "Heavy fuel oil No. 6", "adios": "FUEL OIL NO.6"},
    {"id": "diesel", "label": "Diesel / marine gas oil", "adios": "Generic Diesel"},
    {"id": "jet", "label": "Jet fuel", "adios": "Generic Jet Fuel"},
    {"id": "gasoline", "label": "Gasoline", "adios": "Generic Gasoline"},
    {"id": "condensate", "label": "Condensate", "adios": "Generic Condensate"},
]
DEFAULT_OIL_ID = "crude_medium"
BY_ID = {o["id"]: o for o in OILS}


def resolve(name: str | None) -> str | None:
    """Curated id -> ADIOS name; anything else passes through (the worker resolves free text)."""
    if name in BY_ID:
        return BY_ID[name]["adios"]
    return name
