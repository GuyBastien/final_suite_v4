# This file is part of the Tryton fuel_station module.
import math

REFERENCE_TEMPERATURE = 15.0  # degrees Celsius

# ASTM D1250 (API MPMS 11.1) thermal expansion coefficient:
# alpha = K0 / rho**2 + K1 / rho   (rho: kg/m3 at 15 degC)
_K = {
    'gasoline': (346.4228, 0.4388),
    'diesel': (186.9696, 0.4862),
    'kerosene': (594.5418, 0.0),
    }

FUEL_FAMILIES = [
    ('gasoline', "Gasoline"),
    ('diesel', "Diesel"),
    ('kerosene', "Kerosene / Jet"),
    ('other', "Other (LPG, additive...)"),
    ]
DEFAULT_DENSITY = {
    'gasoline': 750.0, 'diesel': 840.0, 'kerosene': 800.0, 'other': 0.0}


def volume_correction_factor(family, density, temperature):
    """Factor converting a volume observed at `temperature` (degC) to the
    volume at 15 degC. Returns 1.0 when data is missing or the family
    is not covered."""
    if temperature is None or not density or family not in _K:
        return 1.0
    k0, k1 = _K[family]
    alpha = k0 / density ** 2 + k1 / density
    dt = temperature - REFERENCE_TEMPERATURE
    return round(math.exp(-alpha * dt * (1 + 0.8 * alpha * dt)), 5)
