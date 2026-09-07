"""Mise en forme d'un ticket meteo à partir d'une entite weather."""

from __future__ import annotations

import logging
import unicodedata

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Repli si la largeur n'est pas mesuree sur la police reellement utilisée.
DEFAULT_LINE_WIDTH = 24

CONDITIONS = {
    "clear-night": "Nuit claire",
    "cloudy": "Nuageux",
    "exceptional": "Exceptionnel",
    "fog": "Brouillard",
    "hail": "Grele",
    "lightning": "Orage",
    "lightning-rainy": "Orage pluvieux",
    "partlycloudy": "Eclaircies",
    "pouring": "Pluie forte",
    "rainy": "Pluie",
    "snowy": "Neige",
    "snowy-rainy": "Pluie et neige",
    "sunny": "Ensoleille",
    "windy": "Vente",
    "windy-variant": "Vente",
}

DAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


SUBSTITUTIONS = {
    "°": "", "²": "2", "³": "3", "µ": "u", "×": "x",
    "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",  # espaces insecables
}


def ascii_safe(value) -> str:
    """Réduit une chaine a de l'ASCII imprimable.

    Les attributs des entites meteo charrient des symboles que les polices
    embarquees ne savent pas dessiner : degre, espaces insecables, apostrophes
    typographiques. Ils sortent en carres ou en caractères parasites sur une
    tête thermique.
    """
    text = str(value)
    for source, target in SUBSTITUTIONS.items():
        text = text.replace(source, target)
    # decompose les accents puis retire les diacritiques
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c if 32 <= ord(c) < 127 else "" for c in text).strip()


def _row(label: str, value: str, width: int) -> str:
    """Libelle a gauche, valeur a droite, sur la largeur disponible."""
    space = max(1, width - len(label) - len(value))
    return f"{label}{' ' * space}{value}"[:width]


def _condition(value: str | None) -> str:
    if not value:
        return "?"
    return ascii_safe(CONDITIONS.get(value, value.replace("-", " ").capitalize()))


def _round(value) -> str:
    try:
        return f"{round(float(value)):d}"
    except (TypeError, ValueError):
        return "--"


async def async_collect_weather(
    hass: HomeAssistant, entity_id: str, days: int = 4, title: str = "METEO"
) -> dict:
    """Rassemble les données du ticket, déjà nettoyees pour l'impression."""
    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError(f"Entite introuvable : {entity_id}")

    attrs = state.attributes
    unit = ascii_safe(attrs.get("temperature_unit", "C")) or "C"

    details: list[tuple[str, str]] = []
    if (feels := attrs.get("apparent_temperature")) is not None:
        details.append(("Ressenti", f"{_round(feels)} {unit}"))
    if (humidity := attrs.get("humidity")) is not None:
        details.append(("Humidite", f"{_round(humidity)} %"))
    if (wind := attrs.get("wind_speed")) is not None:
        wind_unit = ascii_safe(attrs.get("wind_speed_unit", "km/h")) or "km/h"
        details.append(("Vent", f"{_round(wind)} {wind_unit}"))
    if (pressure := attrs.get("pressure")) is not None:
        pressure_unit = ascii_safe(attrs.get("pressure_unit", "hPa")) or "hPa"
        details.append(("Pression", f"{_round(pressure)} {pressure_unit}"))

    forecast = []
    for entry in (await _async_forecast(hass, entity_id))[:days]:
        forecast.append({
            "day": _day_label(entry.get("datetime")),
            "condition": entry.get("condition"),
            "label": _condition(entry.get("condition")),
            "high": _round(entry.get("temperature")),
            "low": _round(entry.get("templow")),
        })

    return {
        "title": ascii_safe(title),
        "name": ascii_safe(attrs.get("friendly_name", entity_id)),
        "condition": state.state,
        "condition_label": _condition(state.state),
        "temperature": _round(attrs.get("temperature")),
        "unit": unit,
        "details": details,
        "forecast": forecast,
        "timestamp": dt_util.now().strftime("%d/%m/%Y  %H:%M"),
    }


async def async_build_weather_ticket(
    hass: HomeAssistant,
    entity_id: str,
    days: int = 4,
    title: str = "METEO",
    line_width: int = DEFAULT_LINE_WIDTH,
) -> str:
    """Construit le texte du ticket.

    line_width est mesure sur la police et le corps demandes : sans cela, un
    texte plus grand ferait deborder les colonnes hors des 384 px.
    """
    LINE_WIDTH = max(12, line_width)
    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError(f"Entite introuvable : {entity_id}")

    attrs = state.attributes
    unit = ascii_safe(attrs.get("temperature_unit", "C")) or "C"
    lines = [
        ascii_safe(title).center(LINE_WIDTH).rstrip(),
        "=" * LINE_WIDTH,
        ascii_safe(attrs.get("friendly_name", entity_id)),
        "",
        f"{_round(attrs.get('temperature'))} {unit}  {_condition(state.state)}",
    ]

    if (feels := attrs.get("apparent_temperature")) is not None:
        lines.append(_row("Ressenti", f"{_round(feels)} {unit}", LINE_WIDTH))
    if (humidity := attrs.get("humidity")) is not None:
        lines.append(_row("Humidite", f"{_round(humidity)} %", LINE_WIDTH))
    if (wind := attrs.get("wind_speed")) is not None:
        wind_unit = ascii_safe(attrs.get("wind_speed_unit", "km/h")) or "km/h"
        lines.append(_row("Vent", f"{_round(wind)} {wind_unit}", LINE_WIDTH))
    if (pressure := attrs.get("pressure")) is not None:
        pressure_unit = ascii_safe(attrs.get("pressure_unit", "hPa")) or "hPa"
        lines.append(_row("Pression", f"{_round(pressure)} {pressure_unit}", LINE_WIDTH))

    forecast = await _async_forecast(hass, entity_id)
    if forecast:
        lines += ["", "-" * LINE_WIDTH, "PREVISIONS", ""]
        for entry in forecast[:days]:
            label = _day_label(entry.get("datetime"))
            high = _round(entry.get("temperature"))
            low = _round(entry.get("templow"))
            # 4 + 7 + 2 = 13 colonnes consommees avant la condition
            condition = _condition(entry.get("condition"))[:LINE_WIDTH - 13]
            lines.append(f"{label:<4}{high + '/' + low:>7}  {condition}")

    lines += ["", "-" * LINE_WIDTH,
              dt_util.now().strftime("%d/%m/%Y  %H:%M").center(LINE_WIDTH).rstrip()]
    return "\n".join(lines)


async def _async_forecast(hass: HomeAssistant, entity_id: str) -> list[dict]:
    """Récupéré les previsions.

    Depuis 2024.4, l'attribut 'forecast' n'existe plus sur les entites weather :
    il faut passer par le service weather.get_forecasts, qui renvoie une
    réponse. On retombe sur l'attribut si l'intégration est ancienne.
    """
    try:
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"type": "daily"},
            target={"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
    except Exception:  # noqa: BLE001 - entite sans previsions journalieres
        _LOGGER.debug("get_forecasts indisponible pour %s", entity_id, exc_info=True)
        state = hass.states.get(entity_id)
        return list(state.attributes.get("forecast") or []) if state else []

    data = (response or {}).get(entity_id) or {}
    return list(data.get("forecast") or [])


def _day_label(value) -> str:
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else value
    if parsed is None:
        return "?"
    return DAYS[dt_util.as_local(parsed).weekday()]
